#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List


def _iter_json_lines(stream: List[str]):
    for line in stream:
        s = line.strip()
        if not s:
            continue
        # Try strict JSON
        try:
            yield json.loads(s)
            continue
        except Exception:
            pass
        # Fallback: try to find the first '{' and parse substring
        if '{' in s and '}' in s:
            try:
                start = s.index('{')
                end = s.rfind('}') + 1
                payload = s[start:end]
                yield json.loads(payload)
            except Exception:
                continue


def fetch_logs_with_gcloud(project: str, service: str, minutes: int) -> List[str]:
    # Use simpler filter to avoid complex quoting issues in shells:
    # - timestamp>=-PT{minutes}M (without quotes)
    # - textPayload:"EVAL_" (match our JSON entries that include event names EVAL_METRICS/EVAL_FAILURE)
    filter_expr = (
        f'resource.type="cloud_run_revision" '
        f'AND resource.labels.service_name="{service}" '
        f'AND textPayload:"EVAL_"'
    )
    cmd = [
        'gcloud', 'logging', 'read',
        filter_expr,
        '--project', project,
        '--freshness', f'{minutes}m',
        '--limit', '200',
        '--format', 'value(textPayload)'
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        return out.decode('utf-8', errors='ignore').splitlines()
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"Failed to read logs: {e}\n{e.output.decode('utf-8', errors='ignore')}\n")
        return []


def analyze(entries: List[Dict[str, Any]]) -> None:
    failures = [e for e in entries if e.get('event') == 'EVAL_FAILURE']
    metrics = [e for e in entries if e.get('event') == 'EVAL_METRICS']

    print(f"Total entries: {len(entries)} | failures: {len(failures)} | metrics: {len(metrics)}")

    # Failure reasons
    reasons = Counter([e.get('blocking_reason') or 'unknown' for e in failures])
    print("\nTop failure reasons:")
    for r, c in reasons.most_common():
        print(f"- {r}: {c}")

    # Variant-level checks
    def collect_variant_field(entries, field_path: List[str]):
        values = []
        for e in entries:
            variants = e.get('variants') or {}
            for v in variants.values():
                cur = v
                for p in field_path:
                    cur = cur.get(p) if isinstance(cur, dict) else None
                    if cur is None:
                        break
                if cur is not None:
                    values.append(cur)
        return values

    # Goldset similarity
    gold_sims = collect_variant_field(failures, ['metrics', 'goldset_similarity'])
    print(f"\nGoldset similarity (failures) — count: {len(gold_sims)} | avg: {sum([v for v in gold_sims if isinstance(v,(int,float))]) / max(1,len(gold_sims)):.3f}")

    # Speaks to you
    speak_flags = collect_variant_field(failures, ['metrics', 'speaks_to_you'])
    print(f"Speaks-to-you (failures) — true: {sum(1 for v in speak_flags if v)} / {len(speak_flags)}")

    # Lengths and commas
    lengths = collect_variant_field(failures, ['metrics', 'chars'])
    commas = sum(1 for v in collect_variant_field(failures, ['metrics', 'has_commas']) if v)
    print(f"Lengths (failures) — avg: {sum(lengths)/max(1,len(lengths)):.1f} | with commas: {commas}")

    # Fast eval scores
    fast_style = collect_variant_field(failures, ['evaluation', 'fast', 'style_score'])
    fast_clarity = collect_variant_field(failures, ['evaluation', 'fast', 'clarity_score'])
    print(f"Fast style score avg: {sum([v for v in fast_style if isinstance(v,(int,float))]) / max(1,len(fast_style)):.2f}")
    print(f"Fast clarity score avg: {sum([v for v in fast_clarity if isinstance(v,(int,float))]) / max(1,len(fast_clarity)):.2f}")

    # Pair similarity
    sim_vals = []
    for e in failures:
        ps = e.get('pair_similarity') or {}
        sim = ps.get('similarity')
        if isinstance(sim, (int, float)):
            sim_vals.append(sim)
    if sim_vals:
        print(f"Pair similarity (failures) — avg: {sum(sim_vals)/len(sim_vals):.3f} | max: {max(sim_vals):.3f}")


def main():
    ap = argparse.ArgumentParser(description='Analyze structured evaluation logs (EVAL_METRICS/EVAL_FAILURE).')
    ap.add_argument('--use-gcloud', action='store_true', help='Fetch logs via gcloud logging read')
    ap.add_argument('--project', default='xbot-473616')
    ap.add_argument('--service', default='x-bot-mei')
    ap.add_argument('--minutes', type=int, default=60)
    args = ap.parse_args()

    lines: List[str] = []
    if args.use_gcloud:
        lines = fetch_logs_with_gcloud(args.project, args.service, args.minutes)
    else:
        lines = sys.stdin.read().splitlines()

    entries = list(_iter_json_lines(lines))
    if not entries:
        print('No entries found. Ensure logging is enabled and the filter is correct.')
        return

    analyze(entries)


if __name__ == '__main__':
    main()
