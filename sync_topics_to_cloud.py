import os
import sys
import time
import json
from typing import List, Dict, Any

import requests
from dotenv import load_dotenv

from embeddings_manager import get_topics_collection


def flatten(lst):
    if isinstance(lst, list) and lst and isinstance(lst[0], list):
        out = []
        for sub in lst:
            out.extend(sub)
        return out
    return lst


def main() -> int:
    load_dotenv()
    remote = os.getenv("REMOTE_INGEST_URL", "").strip()
    token = os.getenv("ADMIN_API_TOKEN", "").strip()
    if not remote or not token:
        print("Missing REMOTE_INGEST_URL or ADMIN_API_TOKEN in environment.", file=sys.stderr)
        print("Set REMOTE_INGEST_URL=https://<service-url>/ingest_topics and ADMIN_API_TOKEN accordingly.")
        return 2

    coll = get_topics_collection()
    print("Listing topic IDs from local ChromaDB…", flush=True)
    raw = coll.get(include=[])
    raw_ids = (raw or {}).get("ids")
    if not raw_ids:
        print("No topics found locally (topics_collection is empty).")
        return 0
    ids = flatten(raw_ids)
    total = len(ids)
    print(f"Found {total} topic IDs. Fetching in batches and syncing to Cloud…")

    batch_size = int(os.getenv("SYNC_BATCH", "25") or 25)
    sent = 0
    added_total = 0
    skipped_total = 0
    error_total = 0

    def post_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        url = remote
        if "?" in url:
            url = f"{url}&token={token}"
        else:
            url = f"{url}?token={token}"
        r = requests.post(url, json=payload, timeout=180)
        try:
            return r.json() if r.headers.get("content-type", "").startswith("application/json") else {"ok": False, "http": r.status_code}
        except Exception:
            return {"ok": False, "http": r.status_code}

    for i in range(0, total, batch_size):
        batch_ids = ids[i:i + batch_size]
        data = coll.get(ids=batch_ids, include=["documents", "metadatas"])  # type: ignore
        docs = flatten((data or {}).get("documents")) or []
        mds = flatten((data or {}).get("metadatas")) or []
        # Coll.get preserves order of ids
        topics = []
        for idx, tid in enumerate(batch_ids):
            try:
                doc = docs[idx] if idx < len(docs) else None
                md = mds[idx] if idx < len(mds) else None
                abstract = doc if isinstance(doc, str) else None
                pdf = None
                if isinstance(md, dict):
                    pdf = md.get("pdf") or md.get("source_pdf")
                if tid and abstract:
                    topics.append({"id": tid, "abstract": abstract, "pdf": pdf})
            except Exception:
                continue
        if not topics:
            continue

        payload = {"topics": topics}
        resp = post_payload(payload)
        added = int(resp.get("added", 0)) if isinstance(resp, dict) else 0
        skipped = int(resp.get("skipped_existing", 0)) if isinstance(resp, dict) else 0
        errs = int(resp.get("errors", 0)) if isinstance(resp, dict) else 0
        added_total += added
        skipped_total += skipped
        error_total += errs
        sent += len(topics)
        print(f"Batch {i//batch_size+1}: sent={len(topics)} added={added} skipped={skipped} errors={errs} (progress {sent}/{total})")
        # small pause to be gentle on rate limits
        time.sleep(0.2)

    print("\nSync completed.")
    print(json.dumps({
        "sent": sent,
        "added_total": added_total,
        "skipped_total": skipped_total,
        "error_total": error_total,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
