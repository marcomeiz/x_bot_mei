#!/usr/bin/env python3
"""
Voice compliance checker for JSON drafts.

Usage:
  - echo '{"short":"...","mid":"...","long":"..."}' | python tools/voice_check.py
  - python tools/voice_check.py path/to/drafts.json
  - python tools/voice_check.py --text "Single text to check (generic)"

Checks (mechanical, LLM-free):
  - English-only (no Spanish diacritics)
  - One sentence per line; line ends with . ! ?
  - 5–12 words per line (configurable via env)
  - No commas (unless ENFORCE_NO_COMMAS=0)
  - No and/or (unless ENFORCE_NO_AND_OR=0)
  - No hashtags, links, emojis
  - No hedging/cliché/hype heuristics
  - Character ranges: short ≤160; mid 180–230; long 240–280

Exit code: 0 if all pass; 1 otherwise.
"""

import argparse
import json
import sys
from typing import Dict, List, Tuple

from writing_rules import detect_banned_elements
import variant_generators as vg


def _issues_for_variant(label: str, text: str) -> List[str]:
    issues: List[str] = []
    t = (text or "").strip()
    if not t:
        return ["empty text"]

    # banned elements (commas/conjunctions honoring env toggles inside vg._enforce_variant_compliance)
    be = detect_banned_elements(t)
    if not vg.ENFORCE_NO_COMMAS:
        be = [i for i in be if "uses commas" not in i]
    if not vg.ENFORCE_NO_AND_OR:
        be = [i for i in be if "uses conjunction" not in i]
    issues.extend(be)

    # english-only
    if not vg._english_only(t):
        issues.append("non-English characters detected")

    # one sentence per line
    if not vg._one_sentence_per_line(t):
        issues.append("one sentence per line required (end with . ! ?)")

    # words per line range
    if not vg._avg_words_per_line_between(t, vg.WARDEN_WPL_LO, vg.WARDEN_WPL_HI):
        issues.append(f"word count per line must be {vg.WARDEN_WPL_LO}–{vg.WARDEN_WPL_HI}")

    # char ranges by label
    if label in {"short", "mid", "long"}:
        if not vg._range_ok(label, t):
            issues.append(f"char range violation for {label}")
    else:
        if len(t) > 280:
            issues.append("exceeds 280 characters")

    # extra guards: commas / and/or if toggles enabled
    if vg.ENFORCE_NO_COMMAS and "," in t:
        issues.append("commas not allowed")
    import re as _re
    if vg.ENFORCE_NO_AND_OR and _re.search(r"\b(and|or)\b", t, _re.I):
        issues.append("'and/or' not allowed")

    return issues


def _load_json_from_stdin_or_file(path: str | None, single_text: str | None) -> Dict[str, str]:
    if single_text:
        return {"generic": single_text}
    if path:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        raw = sys.stdin.read()
        data = json.loads(raw)
    if not isinstance(data, dict):
        raise SystemExit("Input must be a JSON object with keys short/mid/long or a single text via --text")
    # accept {short,mid,long} or any string fields
    out: Dict[str, str] = {}
    for k, v in data.items():
        if isinstance(v, str):
            out[k] = v
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Voice compliance checker (mechanical)")
    ap.add_argument("json_path", nargs="?", help="Path to JSON with drafts {short,mid,long}")
    ap.add_argument("--text", dest="single_text", help="Single text to check (generic)")
    args = ap.parse_args()

    drafts = _load_json_from_stdin_or_file(args.json_path, args.single_text)
    labels = ["short", "mid", "long"] if all(k in drafts for k in ("short", "mid", "long")) else list(drafts.keys())

    any_fail = False
    for label in labels:
        txt = drafts.get(label, "")
        problems = _issues_for_variant(label, txt)
        status = "OK" if not problems else "FAIL"
        print(f"[{label}] {status}")
        if problems:
            any_fail = True
            for p in problems:
                print(f"  - {p}")
    return 1 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())

