#!/usr/bin/env python3
"""
fire.py - send payloads to a target endpoint without routing through Claude.

Reads payloads from a JSON file, POSTs each to the target, logs responses.
Claude composes strategy; this script delivers. No AI classifier in the loop.

Usage:
  py -3.12 fire.py payloads.json                          # dry-run (print, don't send)
  py -3.12 fire.py payloads.json --target URL             # fire at URL
  py -3.12 fire.py payloads.json --target URL --batch     # fire all, don't pause between

Payload file format (payloads.json):
  [
    {"prompt": "your payload text here"},
    {"prompt": "another payload", "meta": "optional notes"}
  ]

Or just a plain list of strings:
  ["payload one", "payload two"]
"""

import argparse
import json
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    print("Need requests: py -3.12 -m pip install requests")
    sys.exit(1)


def load_payloads(path):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        print(f"Error: {path} must contain a JSON array")
        sys.exit(1)
    out = []
    for i, item in enumerate(raw):
        if isinstance(item, str):
            out.append({"prompt": item, "meta": ""})
        elif isinstance(item, dict) and "prompt" in item:
            out.append(item)
        else:
            print(f"Warning: skipping item {i} (no 'prompt' key)")
    return out


def main():
    ap = argparse.ArgumentParser(description="Fire payloads at a target endpoint")
    ap.add_argument("payloads", help="Path to payloads JSON file")
    ap.add_argument("--target", help="Target URL to POST to (omit for dry-run)")
    ap.add_argument("--batch", action="store_true", help="No pause between payloads")
    ap.add_argument("--field", default="prompt", help="JSON field name for the payload (default: prompt)")
    ap.add_argument("--header", action="append", default=[], help="Extra header as Key:Value (repeatable)")
    ap.add_argument("--delay", type=float, default=1.0, help="Seconds between requests (default: 1)")
    ap.add_argument("--out", help="Output log file (default: fire-log-<timestamp>.json)")
    args = ap.parse_args()

    payloads = load_payloads(args.payloads)
    print(f"Loaded {len(payloads)} payloads from {args.payloads}")

    if not args.target:
        print("\n[DRY RUN] No --target given. Payloads that would fire:\n")
        for i, p in enumerate(payloads):
            preview = p["prompt"][:120]
            print(f"  {i+1}. {preview}{'...' if len(p['prompt']) > 120 else ''}")
        return

    headers = {}
    for h in args.header:
        if ":" not in h:
            print(f"Error: --header expected Key:Value, got {h}")
            sys.exit(1)
        k, v = h.split(":", 1)
        headers[k.strip()] = v.strip()

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    log_path = args.out or f"logs/fire-log-{ts}.json"
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    results = []
    session = requests.Session()
    session.headers.update(headers)

    for i, p in enumerate(payloads):
        body = {args.field: p["prompt"]}
        print(f"[{i+1}/{len(payloads)}] Firing ({len(p['prompt'])} chars)... ", end="", flush=True)

        try:
            resp = session.post(args.target, json=body, timeout=60)
            result = {
                "index": i,
                "status": resp.status_code,
                "response": resp.text[:2000],
                "elapsed_ms": int(resp.elapsed.total_seconds() * 1000),
                "prompt_preview": p["prompt"][:200],
                "meta": p.get("meta", ""),
            }
            print(f"{resp.status_code} ({result['elapsed_ms']}ms)")
        except Exception as e:
            result = {"index": i, "error": str(e), "prompt_preview": p["prompt"][:200]}
            print(f"ERROR: {e}")

        results.append(result)

        if not args.batch and i < len(payloads) - 1:
            time.sleep(args.delay)

    Path(log_path).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nDone. {len(results)} results logged to {log_path}")

    hits = [r for r in results if r.get("status") == 200]
    print(f"  {len(hits)}/{len(results)} returned 200")


if __name__ == "__main__":
    main()
