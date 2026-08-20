#!/usr/bin/env python3
"""firing-queue helper for the autonomous (human-kicked) relay.

The queue is a human-readable JSON file (learn/firing-queue.json). Each entry is a
turnkey payload: the base64 is stored PRE-ENCODED so a firing session can inject it
with zero Bash (base64 -> atob in the browser). Updates (mark/result/add) go through
this helper, typically run by a SUBAGENT (fresh context, its Bash is not saturated).

Commands:
  python queue.py list [--status pending|fired|win|exhausted|skip]
  python queue.py next                 # next pending entry as JSON (incl base64), or EMPTY
  python queue.py add <entries.json>   # append entries (a JSON array) to the queue
  python queue.py mark <id> <status> [note...]
  python queue.py result <id> <win|miss> [--batch B] [--models m1,m2] [--note ...]
  python queue.py stats

Entry schema (fields the relay relies on):
  id, target, event_url, behavior, wave, models[], lever, target_signal,
  base64, arg, status, fired_batches[], notes
"""
import json, sys, os, argparse, datetime

QUEUE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "firing-queue.json")
STATUSES = {"pending", "fired", "win", "exhausted", "skip"}


def _now():
    # avoid importing time-of-day into logic; a plain ISO date is enough for notes
    return datetime.date.today().isoformat()


def load():
    if not os.path.exists(QUEUE):
        return {"meta": {"target": None, "event_url": None, "updated": _now()}, "entries": []}
    with open(QUEUE, "r", encoding="utf-8") as f:
        return json.load(f)


def save(db):
    db["meta"]["updated"] = _now()
    with open(QUEUE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _find(db, eid):
    for e in db["entries"]:
        if e.get("id") == eid:
            return e
    return None


def cmd_list(args):
    db = load()
    rows = db["entries"]
    if args.status:
        rows = [e for e in rows if e.get("status") == args.status]
    if not rows:
        print("(no matching entries)")
        return
    for e in rows:
        print(f"{e.get('id'):<22} {e.get('status',''):<10} {e.get('behavior',''):<26} {e.get('lever','')}")


def cmd_next(args):
    db = load()
    for e in db["entries"]:
        if e.get("status") == "pending":
            print(json.dumps(e, indent=2, ensure_ascii=False))
            return
    print("EMPTY")


def cmd_add(args):
    db = load()
    with open(args.file, "r", encoding="utf-8") as f:
        incoming = json.load(f)
    if isinstance(incoming, dict) and "entries" in incoming:
        incoming = incoming["entries"]
    if not isinstance(incoming, list):
        print("ERROR: expected a JSON array of entries (or {entries:[...]})", file=sys.stderr)
        sys.exit(1)
    existing_ids = {e.get("id") for e in db["entries"]}
    added = 0
    for e in incoming:
        eid = e.get("id")
        if not eid:
            print("ERROR: every entry needs an id", file=sys.stderr)
            sys.exit(1)
        if eid in existing_ids:
            print(f"skip (dup id): {eid}")
            continue
        e.setdefault("status", "pending")
        if e["status"] not in STATUSES:
            print(f"ERROR: entry {eid} status must be one of {sorted(STATUSES)}", file=sys.stderr)
            sys.exit(1)
        e.setdefault("fired_batches", [])
        e.setdefault("notes", "")
        db["entries"].append(e)
        existing_ids.add(eid)
        added += 1
    save(db)
    print(f"added {added} entr{'y' if added==1 else 'ies'}; queue now has {len(db['entries'])}")


def cmd_mark(args):
    db = load()
    e = _find(db, args.id)
    if not e:
        print(f"ERROR: no entry {args.id}", file=sys.stderr)
        sys.exit(1)
    if args.status not in STATUSES:
        print(f"ERROR: status must be one of {sorted(STATUSES)}", file=sys.stderr)
        sys.exit(1)
    e["status"] = args.status
    note = " ".join(args.note).strip()
    if note:
        e["notes"] = (e.get("notes", "") + f" | [{_now()}] {note}").strip(" |")
    save(db)
    print(f"{args.id} -> {args.status}")


def cmd_result(args):
    db = load()
    e = _find(db, args.id)
    if not e:
        print(f"ERROR: no entry {args.id}", file=sys.stderr)
        sys.exit(1)
    rec = {"date": _now(), "result": args.result}
    if args.batch:
        rec["batch"] = args.batch
    if args.models:
        rec["models"] = args.models.split(",")
    if args.note:
        rec["note"] = " ".join(args.note)
    e.setdefault("fired_batches", []).append(rec)
    # a win banks the entry; a miss leaves it pending unless the caller marks it exhausted
    if args.result == "win":
        e["status"] = "win"
    elif e.get("status") == "pending":
        e["status"] = "fired"
    save(db)
    print(f"{args.id}: recorded {args.result}" + (f" (batch {args.batch})" if args.batch else ""))


def cmd_stats(args):
    db = load()
    counts = {}
    for e in db["entries"]:
        counts[e.get("status", "?")] = counts.get(e.get("status", "?"), 0) + 1
    print(f"queue: {len(db['entries'])} entries; " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


def main():
    # force UTF-8 on stdout/stderr so printing a lever/notes string with a non-Latin
    # glyph does not crash on a Windows cp1252 console (mirrors attempts.py)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    p = argparse.ArgumentParser(description="firing-queue helper")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list"); s.add_argument("--status"); s.set_defaults(fn=cmd_list)
    s = sub.add_parser("next"); s.set_defaults(fn=cmd_next)
    s = sub.add_parser("add"); s.add_argument("file"); s.set_defaults(fn=cmd_add)
    s = sub.add_parser("mark"); s.add_argument("id"); s.add_argument("status"); s.add_argument("note", nargs="*"); s.set_defaults(fn=cmd_mark)
    s = sub.add_parser("result"); s.add_argument("id"); s.add_argument("result", choices=["win", "miss"]); s.add_argument("--batch"); s.add_argument("--models"); s.add_argument("--note", nargs="*"); s.set_defaults(fn=cmd_result)
    s = sub.add_parser("stats"); s.set_defaults(fn=cmd_stats)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
