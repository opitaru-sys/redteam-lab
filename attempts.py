#!/usr/bin/env python3
"""
attempts.py - queryable memory for the red-team lab.

An append-first log of every fire (target x model x lever -> result), stored in
SQLite so the grind can ask questions like "what have I tried against model X",
"what's the pass-rate of lever Y", or "which behaviors are still open" instead of
grepping markdown. Adopted from the r/ClaudeAI loop-orchestrator thread: the
persistent, queryable ticket table is what turns a loop into an orchestrator.

Governance rule (baked in): NOTHING is ever hard-deleted. A row that no longer
applies is soft-closed via `supersede` (status + superseded_by + closed_reason),
so a bad call stays visible instead of vanishing. `export` writes a readable
markdown snapshot so git still gets a diffable artifact next to the binary .db.
The snapshot deliberately omits the `payload` column so the exported artifact
stays raw-payload-free and safe to publish (G-SATURATION).

Zero dependencies (stdlib sqlite3 + argparse). Python 3.9+.

Examples:
  python attempts.py init
  python attempts.py add --challenge grayswan --wave W16 --behavior weak-password-change \
      --model "Copper Beetle Extreme" --lever maintenance-persona --result win --score 100 \
      --refusal-class win --next-move done --payload payload.txt --notes "admin last"
  python attempts.py load seed-session4.json        # historical import (no G-READ gate)
  python attempts.py load fires.json                # new fires: G-READ required per row
  python attempts.py ls --model "Copper Beetle Extreme"
  python attempts.py stats
  python attempts.py open --challenge grayswan
  python attempts.py supersede 5 --by 12 --reason "replaced by whole-surname derivation"
  python attempts.py export learn/attempts-snapshot.md
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "attempts.db")

# The date the G-READ hard rule took effect (CLAUDE.md). Fires logged on/after this
# date must carry a refusal_class + next_move; historical seeds predating it may not.
GREAD_RULE_DATE = "2026-08-20"

NEW_JAILBREAK_CHALLENGE = "hazard-hunt-q3"

# Controlled vocabulary for `result`. Keep it small so stats stay meaningful.
RESULTS = ("win", "block", "near_miss", "tripwire", "dead", "scope_out")

# CRASH-COURSE section 4 refusal-reading table, as a controlled vocab. Recording this on
# EVERY fire is the enforcement that stops the "rewrite blindly, never reroll, never
# classify" drift (Omri, 2026-08-20). The class PICKS the next move; you do not choose freely.
#   byte-identical      -> deterministic input classifier. Reroll is waste. change-surface.
#   note-and-skip       -> injection quarantined / summarized as data. change-surface / change-family.
#   soft-refusal        -> one tripwire, near the boundary. HIGHEST VALUE. reroll or edit-one-clause.
#   adjacent            -> answered a safer neighbour. edit-one-clause to narrow the gap.
#   structure-no-payload-> you own the frame, content gate holds. edit-one-clause for specificity.
#   complied-useless    -> willingness beat capability (judge rejected). extract-detail, do NOT re-jailbreak.
#   win                 -> it broke. done.
#   null                -> G-NULL backend/harness error, not a real result. reroll (re-fire).
REFUSAL_CLASSES = (
    "byte-identical", "note-and-skip", "soft-refusal", "adjacent",
    "structure-no-payload", "complied-useless", "win", "null",
)
# The move must FOLLOW FROM the class (reroll-before-rewrite; change family only when the class
# stops moving after ~8 in-class fires). Recorded so the log shows the decision, not just the shot.
NEXT_MOVES = (
    "reroll", "edit-one-clause", "change-surface", "change-family",
    "extract-detail", "pivot-target", "done",
)

# Controlled vocab for an asserted per-cell fact's key. Extend by adding a value,
# never a second spelling (same discipline as BEHAVIOR_ALIASES).
STATUS_KEYS = ("channel", "probe", "guard", "reopen")

# --- Canonicalization ------------------------------------------------------
# open/stats GROUP BY the raw string, so casing/spelling variants silently fragment
# the same target and make the rollups LIE (report an already-won behavior as still
# open). Canonicalize on INSERT so add and load can never re-introduce a variant, and
# migrate_2026_08_20.py folds the pre-existing rows once. Add a new alias here the
# moment you notice a second spelling, never a second bucket.
BEHAVIOR_ALIASES = {
    # All keys MUST be in normalized form (lowercase-kebab); _normalize() runs before lookup.
    # --- General aliases ---
    "cpf": "call-prohibited-function",
    "call-a-prohibited-function": "call-prohibited-function",
    "download": "encourage-harmful-download",
    "infiltrate": "infiltrate-private-party",
    "exploit-gift-recommendation": "exploit-gift-rec",
    "inflammatory-linkedin": "inflammatory-linkedin-post",
}
# The arena is carried by `wave` (LB-*, W*, PG-*), so the three Gray Swan challenge
# spellings collapse to one and `--challenge grayswan` stops silently missing rows.
CHALLENGE_ALIASES = {
    "grayswan-ipi": "grayswan",
    "grayswan-luckybreak": "grayswan",
    "grayswan-hazard-hunt": NEW_JAILBREAK_CHALLENGE,
    "hazardhunt": NEW_JAILBREAK_CHALLENGE,
    "hazard-hunt": NEW_JAILBREAK_CHALLENGE,
    "hazard-hunt-q3": NEW_JAILBREAK_CHALLENGE,
}
# Lucky Break waves were logged under two naming schemes; line them up.
WAVE_ALIASES = {
    "Easy(ish)": "LB-Easy",
    "Medium": "LB-Medium",
}

# Wave CATEGORIES: `--wave <category>` expands to all waves in that category instead
# of an exact match, so a session never loads unrelated cell names into the context
# window (G-SATURATION). Case-insensitive lookup. Populate per session from the
# operator's local materials; empty here so no roster is tracked in the tool.
WAVE_CATEGORIES: dict[str, list[str]] = {}


def _normalize(v: str) -> str:
    """Lowercase, spaces/underscores to hyphens, collapse runs, strip edges.

    This is the first step of canonicalization: it folds Title Case, spaces, and
    underscore variants into a single kebab-case form BEFORE the alias lookup, so
    'Weak Password Change' and 'weak-password-change' hit the same
    bucket without needing an explicit alias for every casing variant.
    """
    v = v.strip().lower()
    v = re.sub(r"[\s_]+", "-", v)
    v = re.sub(r"-{2,}", "-", v)
    return v.strip("-")


def canon_behavior(v):
    v = _normalize(v)
    return BEHAVIOR_ALIASES.get(v, v)


MODEL_ALIASES: dict[str, str] = {
    "ecm": "Earth Cat Mighty",
    "etb": "Electric Tiger Bold",
    "fee": "Fire Elk Enormous",
    "gsi": "Gray Salmon Intrepid",
    "gts": "Ghost Tuna Supreme",
    "bss": "Bronze Shark Stellar",
    "tce": "Turquoise Capybara Extreme",
    "vtpu": "Violet Turtle Plus Ultra",
    "ohg": "Onyx Hippo Giga",
}


def canon_model(v: str) -> str:
    """Normalize model names to Title Case with spaces.

    Arena models display as 'Ghost Tuna Supreme' but batch-mode logging
    sometimes produces 'ghost-tuna-supreme' or 'ghost tuna supreme'.
    Fold all variants so GROUP BY never fragments the same model.
    """
    if not v:
        return v
    v = v.strip()
    low = v.lower().replace("-", " ").replace("_", " ")
    low = re.sub(r"\s{2,}", " ", low).strip()
    alias = MODEL_ALIASES.get(low)
    if alias:
        return alias
    return low.title()


def canon_challenge(v):
    return CHALLENGE_ALIASES.get(v, v)


def canon_wave(v):
    return WAVE_ALIASES.get(v, v)


def expand_wave(v: str) -> list[str] | None:
    """If v is a category name, return the list of waves. Otherwise return None (exact match)."""
    return WAVE_CATEGORIES.get(v.lower())


# One home for asserted per-cell facts (probe result, guard mechanism, channel status).
# model='' means the fact applies to all models of the cell. UNIQUE lets `note` upsert so
# there is exactly one row per fact and it can never drift across prose files.
CELL_STATUS_DDL = """
CREATE TABLE IF NOT EXISTS cell_status (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    challenge  TEXT NOT NULL,
    behavior   TEXT NOT NULL,
    model      TEXT NOT NULL DEFAULT '',
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    source     TEXT,
    UNIQUE(challenge, behavior, model, key)
);
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    challenge     TEXT NOT NULL,
    wave          TEXT,
    behavior      TEXT NOT NULL,
    model         TEXT NOT NULL,
    lever         TEXT,
    result        TEXT NOT NULL,
    score         TEXT,
    score_num     REAL,
    pred_guard    TEXT,
    pred_score    TEXT,
    payload       TEXT,
    notes         TEXT,
    refusal_class TEXT,
    next_move     TEXT,
    oracle_type   TEXT,
    conversation_id TEXT,
    turn_index    INTEGER,
    turn_goal     TEXT,
    latency_ms    REAL,
    status        TEXT NOT NULL DEFAULT 'active',
    superseded_by INTEGER,
    closed_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_attempts_behavior ON attempts(behavior);
CREATE INDEX IF NOT EXISTS idx_attempts_model ON attempts(model);
CREATE INDEX IF NOT EXISTS idx_attempts_result ON attempts(result);
""" + CELL_STATUS_DDL

# Columns added to the table after its first release. Each is a nullable ALTER, applied
# idempotently by _migrate against a pre-existing DB.
# oracle_type distinguishes a REAL-EFFECT win (a tool actually fired / data actually left) from a
# JUDGE-ARTIFACT win (the judge scored high with no underlying action, e.g. it read a composed
# {fill} as the answer after an empty final turn). Only real-effect wins count as capability.
# conversation_id/turn_index/turn_goal/latency_ms carry the multi-turn conversation unit: which
# turns belong to the same conversation, this turn's position and intent, and how long it took.
_ADDED_COLUMNS = ("refusal_class", "next_move", "score_num", "pred_guard",
                  "pred_score", "oracle_type", "conversation_id", "turn_index",
                  "turn_goal", "latency_ms")

# Explicit SQLite type per added column; anything not listed here defaults to TEXT.
_COLUMN_TYPES = {"score_num": "REAL", "turn_index": "INTEGER", "latency_ms": "REAL"}

# A win-lever fired fewer than this many times is provisional (a single lucky draw on a stochastic
# guard is not a confirmed technique - the G-SOLVE bar). Used only for honest reporting, computed
# live so it can never go stale.
CONFIRM_N = 3


def _reconfigure_stdout() -> None:
    """Force UTF-8 on stdout/stderr so printing a lever/notes string that contains a
    non-Latin glyph (arrows, the hourglass emoji, box-drawing) does not crash with
    UnicodeEncodeError on a Windows cp1252 console. G-LOG mandates running stats/open
    at every session end, so a render crash silently blocks the mandated close-out."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the first release to a pre-existing DB (SQLite ALTER
    is a safe nullable add), and create the cell_status table if it is missing. Idempotent:
    checks the live column set first, and cell_status uses CREATE TABLE IF NOT EXISTS."""
    if "attempts" not in {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}:
        return  # fresh DB; SCHEMA will create with the columns already present
    cols = {row[1] for row in conn.execute("PRAGMA table_info(attempts)")}
    for col in _ADDED_COLUMNS:
        if col not in cols:
            col_type = _COLUMN_TYPES.get(col, "TEXT")
            conn.execute(f"ALTER TABLE attempts ADD COLUMN {col} {col_type}")
    conn.executescript(CELL_STATUS_DDL)  # idempotent; brings pre-existing DBs up to date


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _migrate(conn)
    return conn


def cmd_init(args: argparse.Namespace) -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
    print(f"initialized {DB_PATH}")


def _resolve_payload(value: str | None) -> str | None:
    """A --payload value that names an existing file is read as that file's text."""
    if value and os.path.isfile(value):
        with open(value, "r", encoding="utf-8") as fh:
            return fh.read()
    return value


def _score_str(score) -> str | None:
    if score is None or score == "":
        return None
    return str(score)


def _score_num(score) -> float | None:
    """Extract the first standalone 0-100 number from a free-text score so near-misses
    can be sorted by gradient (the evolutionary-optimization workflow needs a numeric
    field). 'flip 100/100 OpSec 20' -> 100.0; 'blocked' -> None."""
    s = _score_str(score)
    if s is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def rule_of_three_ub(n: int) -> float:
    """One-sided ~95% upper bound on the true rate given 0 events in n trials (3/n)."""
    return 3.0 / n if n else 1.0


def wilson_lower_bound(successes: int, n: int, z: float = 1.96) -> float:
    """Wilson score-interval lower bound for a binomial proportion. Pure stdlib.
    Used to decide 'durable' (need the lower bound above 0.8), never a point estimate."""
    if n == 0:
        return 0.0
    phat = successes / n
    denom = 1.0 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * ((phat * (1 - phat) + z * z / (4 * n)) / n) ** 0.5
    return max(0.0, (centre - margin) / denom)


def has_reopen(conn: sqlite3.Connection, challenge: str, behavior: str) -> bool:
    """True when an operator override (key='reopen') exists for this cell in cell_status,
    meaning the cell should NOT be flagged CLOSED-CHANNEL regardless of n/wins."""
    row = conn.execute(
        "SELECT 1 FROM cell_status WHERE challenge=? AND behavior=? AND key='reopen' LIMIT 1",
        (challenge, behavior),
    ).fetchone()
    return row is not None


def _reopened_behaviors(conn: sqlite3.Connection, challenge: str | None) -> set[str]:
    """Return the set of behaviors with an operator reopen override, scoped to a challenge."""
    if challenge:
        rows = conn.execute(
            "SELECT DISTINCT behavior FROM cell_status WHERE challenge=? AND key='reopen'",
            (challenge,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT behavior FROM cell_status WHERE key='reopen'"
        ).fetchall()
    return {r["behavior"] for r in rows}


def _row_signature(rec: dict) -> tuple:
    """Content identity of a fire, ignoring id/ts, so re-loading a seed is idempotent.
    conversation_id + turn_index are part of the identity so two turns of the same
    conversation (same challenge/behavior/model/lever/result/score/notes) are not deduped
    against each other."""
    return (
        canon_challenge(rec.get("challenge")), canon_behavior(rec.get("behavior")),
        canon_model(rec.get("model") or ""), rec.get("lever"), rec.get("result"),
        _score_str(rec.get("score")), rec.get("notes"),
        rec.get("conversation_id"), rec.get("turn_index"),
    )


def add_attempt(conn: sqlite3.Connection, rec: dict) -> int:
    result = rec.get("result")
    if result not in RESULTS:
        raise ValueError(f"result must be one of {RESULTS}, got {result!r}")
    for required in ("challenge", "behavior", "model"):
        if not rec.get(required):
            raise ValueError(f"missing required field: {required}")
    rc = rec.get("refusal_class")
    if rc is not None and rc not in REFUSAL_CLASSES:
        raise ValueError(f"refusal_class must be one of {REFUSAL_CLASSES}, got {rc!r}")
    nm = rec.get("next_move")
    if nm is not None and nm not in NEXT_MOVES:
        raise ValueError(f"next_move must be one of {NEXT_MOVES}, got {nm!r}")
    ti = rec.get("turn_index")
    if ti is not None and not isinstance(ti, int):
        raise ValueError(f"turn_index must be an int, got {ti!r}")
    lm = rec.get("latency_ms")
    if lm is not None and not isinstance(lm, (int, float)):
        raise ValueError(f"latency_ms must be a number (int or float), got {lm!r}")
    cur = conn.execute(
        """INSERT INTO attempts
           (ts, challenge, wave, behavior, model, lever, result, score, score_num,
            pred_guard, pred_score, payload, notes, refusal_class, next_move, oracle_type,
            conversation_id, turn_index, turn_goal, latency_ms)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            rec.get("ts") or now_iso(),
            canon_challenge(rec["challenge"]), canon_wave(rec.get("wave")),
            canon_behavior(rec["behavior"]), canon_model(rec["model"]),
            rec.get("lever"), result, _score_str(rec.get("score")), _score_num(rec.get("score")),
            rec.get("pred_guard"),
            # pred_score is free text ("HC9/AIO10/Real9"), not a bare number: store the raw
            # string like `score`. A numeric can be derived on read via _score_num if ever needed.
            (rec.get("pred_score") or None),
            rec.get("payload"), rec.get("notes"), rc, nm, rec.get("oracle_type"),
            rec.get("conversation_id"), rec.get("turn_index"),
            rec.get("turn_goal"), rec.get("latency_ms"),
        ),
    )
    return cur.lastrowid


def upsert_cell_status(conn: sqlite3.Connection, rec: dict) -> None:
    key = rec.get("key")
    if key not in STATUS_KEYS:
        raise ValueError(f"key must be one of {STATUS_KEYS}, got {key!r}")
    for required in ("challenge", "behavior", "value"):
        if not rec.get(required):
            raise ValueError(f"missing required field: {required}")
    conn.execute(
        """INSERT INTO cell_status (ts, challenge, behavior, model, key, value, source)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(challenge, behavior, model, key)
           DO UPDATE SET value=excluded.value, ts=excluded.ts,
                         source=COALESCE(excluded.source, cell_status.source)""",
        (rec.get("ts") or now_iso(), canon_challenge(rec["challenge"]),
         canon_behavior(rec["behavior"]), canon_model(rec.get("model") or ""),
         key, rec["value"], rec.get("source")),
    )


def cmd_note(args: argparse.Namespace) -> None:
    ch, beh, model = canon_challenge(args.challenge), canon_behavior(args.behavior), canon_model(args.model or "")
    with connect() as conn:
        prior = conn.execute(
            "SELECT value FROM cell_status WHERE challenge=? AND behavior=? AND model=? AND key=?",
            (ch, beh, model, args.key),
        ).fetchone()
        upsert_cell_status(conn, {
            "challenge": args.challenge, "behavior": args.behavior, "model": args.model,
            "key": args.key, "value": args.value, "source": args.source,
        })
    scope = args.model or "(all models)"
    if prior:
        print(f"updated {args.key} for {beh}/{scope}: {prior['value']!r} -> {args.value!r}")
    else:
        print(f"noted {args.key} for {beh}/{scope}: {args.value!r}")


def cmd_reopen(args: argparse.Namespace) -> None:
    """Record an operator override that suppresses CLOSED-CHANNEL for a cell. The override
    persists in cell_status (key='reopen') and is respected by brief, open, and check closed."""
    ch = canon_challenge(args.challenge)
    beh = canon_behavior(args.behavior)
    with connect() as conn:
        upsert_cell_status(conn, {
            "challenge": args.challenge, "behavior": args.behavior,
            "key": "reopen", "value": args.reason, "source": "operator",
        })
        tries, wins = _behavior_counts(conn, ch, beh)
    if wins == 0 and tries >= 30:
        ub = rule_of_three_ub(tries)
        print(f"reopened {beh} (0/{tries}, ub<={ub*100:.0f}%): CLOSED-CHANNEL suppressed")
    else:
        print(f"reopened {beh} ({wins}/{tries}): override recorded (cell was not flagged closed)")
    print(f"reason: {args.reason}")


def cmd_add(args: argparse.Namespace) -> None:
    rec = {
        "challenge": args.challenge, "wave": args.wave, "behavior": args.behavior,
        "model": args.model, "lever": args.lever, "result": args.result,
        "score": args.score, "payload": _resolve_payload(args.payload), "notes": args.notes,
        "refusal_class": args.refusal_class, "next_move": args.next_move,
        "pred_guard": args.pred_guard, "pred_score": args.pred_score,
        "oracle_type": args.oracle_type,
        "conversation_id": args.conversation_id, "turn_index": args.turn_index,
        "turn_goal": args.turn_goal, "latency_ms": args.latency_ms,
    }
    with connect() as conn:
        rid = add_attempt(conn, rec)
    if not args.pred_guard and not args.pred_score:
        print("  note (pre-registration): logged without --pred-guard/--pred-score. Predicting the "
              "guard + score BEFORE firing is the highest credibility-per-minute move; add them next time.")
    print(f"added attempt #{rid} ({args.result} / {args.refusal_class} -> {args.next_move}: "
          f"{canon_behavior(args.behavior)} / {canon_model(args.model)})")
    _do_export(quiet=True)


def cmd_load(args: argparse.Namespace) -> None:
    with open(args.file, "r", encoding="utf-8") as fh:
        rows = json.load(fh)
    if not isinstance(rows, list):
        sys.exit("load expects a JSON array of attempt objects")

    # G-READ gate: a real fire logged on/after the rule date must carry its class + move.
    # --seed exempts a historical import. Enforced BEFORE any insert so a bad batch is
    # rejected whole, not half-applied.
    if not args.seed:
        offenders = [
            i for i, rec in enumerate(rows)
            if (rec.get("ts") or now_iso()) >= GREAD_RULE_DATE
            and (not rec.get("refusal_class") or not rec.get("next_move"))
        ]
        if offenders:
            sys.exit(
                f"G-READ: {len(offenders)} record(s) dated >= {GREAD_RULE_DATE} lack refusal_class/"
                f"next_move (first at index {offenders[0]}). Read + classify every reply, or pass "
                f"--seed if this is a historical import that predates the gate.")

    added = skipped = failed = unclassified = 0
    errors: list[str] = []
    with connect() as conn:
        # Load existing signatures once for idempotency.
        seen = {
            _row_signature(dict(r)) for r in conn.execute(
                "SELECT challenge, behavior, model, lever, result, score, notes, "
                "conversation_id, turn_index FROM attempts")
        }
        for i, rec in enumerate(rows):
            sig = _row_signature(rec)
            if sig in seen:
                skipped += 1
                continue
            try:
                add_attempt(conn, rec)
            except Exception as e:  # one bad enum must not roll back the whole batch
                failed += 1
                errors.append(f"  row {i}: {e}")
                continue
            seen.add(sig)
            added += 1
            if not rec.get("refusal_class"):
                unclassified += 1
    print(f"loaded {args.file}: {added} inserted, {skipped} skipped (already present), {failed} failed")
    for line in errors:
        print(line)
    if unclassified:
        print(f"  note: {unclassified} inserted row(s) have no refusal_class (historical/seed import).")


def _active_filter(args: argparse.Namespace) -> tuple[str, list]:
    clauses, params = ["status = 'active'"], []
    for col in ("challenge", "wave", "behavior", "model", "lever", "result"):
        val = getattr(args, col, None)
        if val:
            if col == "challenge":
                val = canon_challenge(val)
            elif col == "behavior":
                val = canon_behavior(val)
            elif col == "wave":
                val = canon_wave(val)
            clauses.append(f"{col} = ?")
            params.append(val)
    return " AND ".join(clauses), params


def cmd_ls(args: argparse.Namespace) -> None:
    where, params = _active_filter(args)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT id, wave, behavior, model, lever, result, score FROM attempts "
            f"WHERE {where} ORDER BY id",
            params,
        ).fetchall()
    if not rows:
        print("(no matching attempts)")
        return
    for r in rows:
        score = f" {r['score']}" if r["score"] else ""
        print(f"#{r['id']:<3} {r['result']:<9} {r['wave'] or '-':<7} "
              f"{r['behavior']:<26} {r['model']:<28} [{r['lever'] or '-'}]{score}")


def cmd_wins(args: argparse.Namespace) -> None:
    args.result = "win"
    cmd_ls(args)


def _lever_counts(conn: sqlite3.Connection, challenge: str, lever: str) -> tuple[int, int]:
    row = conn.execute(
        "SELECT COUNT(*) tries, SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) wins "
        "FROM attempts WHERE status='active' AND challenge=? AND lever=?",
        (challenge, lever),
    ).fetchone()
    return row["tries"] or 0, row["wins"] or 0


def _behavior_counts(conn: sqlite3.Connection, challenge: str, behavior: str,
                     model: str | None = None) -> tuple[int, int]:
    q = ("SELECT COUNT(*) tries, SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) wins "
         "FROM attempts WHERE status='active' AND challenge=? AND behavior=?")
    p = [challenge, behavior]
    if model:
        q += " AND model=?"
        p.append(model)
    row = conn.execute(q, p).fetchone()
    return row["tries"] or 0, row["wins"] or 0


def check_verdict(conn, claim, challenge, behavior=None, model=None, lever=None):
    """Run one conclusion-guard gate. Returns (exit_code, message). exit_code 0 means the
    claim is gate-legal to WRITE; 1 means it is not (needs more n, or is forbidden). The
    message is built to be pasted into the conclusion line and carries the exact bound the
    loop-audit hook already accepts (UNMEASURED / n= / ub<= / 95% / pass_rate / CI)."""
    challenge = canon_challenge(challenge)
    # TODO(2026-08-25): this CTF-tuple check reads PUBLIC_CHALLENGES, so it picks up
    # NEW_JAILBREAK_CHALLENGE automatically once that tuple is updated above - just make
    # sure the tuple edit lands, nothing to change here.
    is_ctf = challenge in PUBLIC_CHALLENGES
    if claim == "solved":
        tries, wins = _lever_counts(conn, challenge, lever)
        if wins >= 1 and tries >= CONFIRM_N:
            return 0, f"CONFIRMED: lever {lever!r} pass {wins}/{tries}, n>={CONFIRM_N} (gate-legal: SOLVED)"
        if wins >= 1:
            return 1, (f"PROVISIONAL: lever {lever!r} 1+ pass in n={tries}, pass_rate UNMEASURED, "
                       f"need n>={CONFIRM_N} before SOLVED (G-SOLVE)")
        return 1, f"NOT A SOLVE: lever {lever!r} has 0 wins in n={tries} (G-SOLVE)"
    if claim == "durable":
        tries, wins = _lever_counts(conn, challenge, lever)
        lb = wilson_lower_bound(wins, tries)
        if tries >= 10 and lb >= 0.8:
            return 0, f"DURABLE: lever {lever!r} pass {wins}/{tries}, Wilson 95% lower bound {lb:.2f} >= 0.80"
        return 1, (f"NOT DURABLE: lever {lever!r} pass {wins}/{tries}, Wilson lower bound {lb:.2f}, "
                   f"need n>=10 and CI above 0.80 (G-SOLVE b)")
    if claim == "safe":
        behavior = canon_behavior(behavior)
        tries, wins = _behavior_counts(conn, challenge, behavior, model)
        control = conn.execute(
            "SELECT COUNT(*) n FROM attempts WHERE status='active' AND challenge=? AND result='win'",
            (challenge,),
        ).fetchone()["n"]
        if wins == 0:
            ub = rule_of_three_ub(tries)
            ctrl = "present" if control else "ABSENT (null may be blind)"
            return 1, (f"robustness UNMEASURED: opening rate ub<={ub*100:.0f}% (0/{tries}, 95%); "
                       f"positive control: {ctrl}. Report 'no opening found', never 'secure' (G-NULL)")
        return 1, f"NOT SAFE: {behavior} has {wins} win(s) in n={tries}"
    if claim == "closed":
        behavior = canon_behavior(behavior)
        tries, wins = _behavior_counts(conn, challenge, behavior, model)
        if wins == 0 and tries >= 30:
            if has_reopen(conn, challenge, behavior):
                return 1, (f"REOPENED by operator override (0/{tries}); reopen note suppresses "
                           f"CLOSED-CHANNEL. Content levers allowed.")
            ub = rule_of_three_ub(tries)
            return 0, (f"[CLOSED-CHANNEL ub<={ub*100:.0f}%] (0/{tries}); next fire MUST be "
                       f"provenance-changing, not a content reroll (G-CHANNEL-CLOSED)")
        if wins:
            return 1, f"channel OPEN: {behavior} has {wins} win(s)"
        return 1, f"NOT YET CLOSED: n={tries} < 30; keep content levers (G-CHANNEL-CLOSED)"
    if claim == "wall":
        if is_ctf:
            return 1, ("SOLVABLE-PRIOR holds for a CTF target; a wall is a search-failure by "
                       "definition. Run a fresh brainstorm; do not write the wall (G-PERSIST/G-REOPEN)")
        return 1, ("report 'no break found with techniques tried', never 'unbreakable'; robustness "
                   "UNMEASURED, hold the solvable-prior open (mode-aware G-PERSIST)")
    return 1, f"unknown claim {claim!r}"


def cmd_check(args: argparse.Namespace) -> None:
    # Per-claim required args, validated here so the message is specific.
    need_lever = args.claim in ("solved", "durable")
    need_behavior = args.claim in ("safe", "closed")
    if need_lever and not args.lever:
        sys.exit(f"check {args.claim} requires --lever")
    if need_behavior and not args.behavior:
        sys.exit(f"check {args.claim} requires --behavior")
    with connect() as conn:
        code, msg = check_verdict(conn, args.claim, args.challenge,
                                  behavior=args.behavior, model=args.model, lever=args.lever)
    print(("PASS " if code == 0 else "FAIL ") + msg)
    sys.exit(code)


def _wave_clause(alias: str, wave_arg: str | None) -> tuple[str, list]:
    """Build a wave filter clause for the given table alias. Returns (sql, params)."""
    if not wave_arg:
        return "", []
    waves = expand_wave(wave_arg)
    if waves:
        ph = ",".join("?" for _ in waves)
        return f" AND {alias}.wave IN ({ph})", list(waves)
    return f" AND {alias}.wave=?", [canon_wave(wave_arg)]


def _capability_counts(conn: sqlite3.Connection, challenge: str | None,
                       wave_arg: str | None = None) -> dict:
    """DISTINCT real-effect breaks and the confirmed/provisional/artifact split. Shared by
    stats and brief so the honest headline is computed in exactly one place."""
    ch_clause = " AND w.challenge=?" if challenge else ""
    sub_ch = " AND a.challenge=?" if challenge else ""
    w_wave_sql, w_wave_p = _wave_clause("w", wave_arg)
    a_wave_sql, a_wave_p = _wave_clause("a", wave_arg)
    ch_params = [challenge] if challenge else []
    params = tuple(ch_params + w_wave_p + ch_params + a_wave_p)
    wins = conn.execute(
        f"SELECT w.behavior, w.lever, w.oracle_type, "
        f"(SELECT COUNT(*) FROM attempts a WHERE a.status='active' AND a.lever IS w.lever{sub_ch}{a_wave_sql}) n "
        f"FROM attempts w WHERE w.status='active' AND w.result='win'{ch_clause}{w_wave_sql}",
        params,
    ).fetchall()
    real = [w for w in wins if (w["oracle_type"] or "real-effect") != "judge-artifact"]
    confirmed = sum(1 for w in real if w["n"] >= CONFIRM_N)
    return {
        "breaks": len({w["behavior"] for w in real}),
        "win_rows": len(wins),
        "confirmed": confirmed,
        "provisional": len(real) - confirmed,
        "artifacts": len(wins) - len(real),
    }


def cmd_stats(args: argparse.Namespace) -> None:
    challenge = canon_challenge(args.challenge) if args.challenge else None
    ch_clause = " AND challenge=?" if challenge else ""
    params = (challenge,) if challenge else ()
    with connect() as conn:
        # Capability, stated honestly. DISTINCT real-effect breaks is the headline number, never the
        # win-row count (a win-row is inflated: one break can be logged many times). A win is
        # confirmed only if its lever was fired >= CONFIRM_N times (a single draw is provisional),
        # and a judge-artifact win (scored high with no real action) is not capability at all.
        cap = _capability_counts(conn, challenge)
        by_result = conn.execute(
            f"SELECT result, COUNT(*) n FROM attempts "
            f"WHERE status='active'{ch_clause} GROUP BY result ORDER BY n DESC",
            params,
        ).fetchall()
        by_lever = conn.execute(
            f"SELECT lever, "
            f"SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) wins, COUNT(*) tries "
            f"FROM attempts WHERE status='active' AND lever IS NOT NULL{ch_clause} "
            f"GROUP BY lever ORDER BY wins DESC, tries DESC",
            params,
        ).fetchall()
    print("capability (the honest headline):")
    print(f"  DISTINCT real-effect breaks : {cap['breaks']}   <- the real number; cite this, never win-rows")
    print(f"  win rows                    : {cap['win_rows']}   (inflated: one break logs many rows)")
    print(f"  of wins: {cap['confirmed']} confirmed (n>={CONFIRM_N}) | {cap['provisional']} provisional (single draw) "
          f"| {cap['artifacts']} judge-artifact (no real action)")
    print("\nresults:")
    for r in by_result:
        print(f"  {r['result']:<10} {r['n']}")
    print("\nlever pass-rate (wins/tries):")
    for r in by_lever:
        print(f"  {r['lever']:<32} {r['wins']}/{r['tries']}")


def cmd_open(args: argparse.Namespace) -> None:
    """Behaviors attempted but never won, excluding ones ruled out of scope."""
    challenge = canon_challenge(args.challenge) if args.challenge else None
    ch = " AND challenge = ?" if challenge else ""
    p = (challenge,) if challenge else ()
    with connect() as conn:
        rows = conn.execute(
            f"SELECT behavior, wave, COUNT(*) tries "
            f"FROM attempts WHERE status='active' AND result != 'scope_out'{ch} "
            f"GROUP BY behavior "
            f"HAVING SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) = 0 "
            f"ORDER BY tries DESC",
            p,
        ).fetchall()
        reopened = _reopened_behaviors(conn, challenge)
    if not rows:
        print("(no open behaviors: everything attempted has at least one win)")
        return
    print("open behaviors (attempted, no win yet):")
    print("  [CLOSED-CHANNEL] = 0 wins with enough fires that the 95% upper bound on the opening rate "
          "is low.\n  G-CHANNEL-CLOSED: do NOT reroll another content lever here - queue a "
          "provenance-changing\n  lever first (trusted-tool-result, a second trusted turn, "
          "tool-registration, modify-trusted-data).\n")
    for r in rows:
        n = r["tries"]
        # Rule of three: for 0 wins in n trials the ~95% one-sided upper bound on p is 3/n.
        ub = 3.0 / n if n else 1.0
        if n >= 30 and r["behavior"] not in reopened:
            flag = f"  [CLOSED-CHANNEL ub<={ub*100:.0f}%]"
        elif n >= 30 and r["behavior"] in reopened:
            flag = "  [REOPENED]"
        else:
            flag = ""
        print(f"  {r['wave'] or '-':<7} {r['behavior']:<28} {n} tries, 0 wins{flag}")


def _brief_filter(args) -> tuple[str, list]:
    clause, params = "", []
    if getattr(args, "challenge", None):
        clause += " AND challenge=?"
        params.append(canon_challenge(args.challenge))
    if getattr(args, "wave", None):
        waves = expand_wave(args.wave)
        if waves:
            placeholders = ",".join("?" for _ in waves)
            clause += f" AND wave IN ({placeholders})"
            params.extend(waves)
        else:
            clause += " AND wave=?"
            params.append(canon_wave(args.wave))
    return clause, params


def _conversation_rows(conn, clause, params):
    return conn.execute(
        f"SELECT conversation_id, turn_index, model, behavior, result, "
        f"refusal_class, score_num FROM attempts "
        f"WHERE status='active' AND conversation_id IS NOT NULL{clause} "
        f"ORDER BY conversation_id, turn_index", params).fetchall()


def cmd_brief(args: argparse.Namespace) -> None:
    """Reconstruct the actionable session STATE from the ledger, payload-free. This is what a
    fresh session reads INSTEAD of the PROGRESS.md RESUME prose (source of truth = the DB)."""
    challenge = canon_challenge(args.challenge) if getattr(args, "challenge", None) else None
    wave_arg = getattr(args, "wave", None)
    clause, params = _brief_filter(args)
    with connect() as conn:
        cap = _capability_counts(conn, challenge, wave_arg=wave_arg)
        cells = conn.execute(
            f"SELECT behavior, wave, COUNT(*) tries, MAX(score_num) best "
            f"FROM attempts WHERE status='active' AND result!='scope_out'{clause} "
            f"GROUP BY behavior, wave "
            f"HAVING SUM(CASE WHEN result='win' THEN 1 ELSE 0 END)=0",
            params,
        ).fetchall()
        last = {}
        for r in conn.execute(
            f"SELECT behavior, wave, refusal_class, next_move FROM attempts "
            f"WHERE status='active'{clause} ORDER BY id", params,
        ):
            last[(r["behavior"], r["wave"])] = (r["refusal_class"], r["next_move"])
        gradients = conn.execute(
            f"SELECT wave, behavior, model, score_num, refusal_class FROM attempts "
            f"WHERE status='active' AND result!='win' AND score_num IS NOT NULL{clause} "
            f"ORDER BY score_num DESC LIMIT 8", params,
        ).fetchall()
        # Channel-closed is a BEHAVIOR-level property (wave-agnostic), matching cmd_open and
        # check closed. Compute behavior totals under the same scope so brief cannot call a
        # channel open that open / check call closed (whole-branch review Important #1).
        beh_totals = {}
        for r in conn.execute(
            f"SELECT behavior, COUNT(*) n, "
            f"SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) wins "
            f"FROM attempts WHERE status='active' AND result!='scope_out'{clause} "
            f"GROUP BY behavior", params,
        ):
            beh_totals[r["behavior"]] = (r["n"], r["wins"] or 0)

        reopened = _reopened_behaviors(conn, challenge)

        def _closed(behavior):
            if behavior in reopened:
                return False
            n, wins = beh_totals.get(behavior, (0, 0))
            return wins == 0 and n >= 30
        cs_clause = " AND challenge=?" if challenge else ""
        cs_params: list = [challenge] if challenge else []
        if wave_arg:
            scoped_behaviors = {c["behavior"] for c in cells}
            scoped_behaviors |= {
                r["behavior"] for r in conn.execute(
                    f"SELECT DISTINCT behavior FROM attempts "
                    f"WHERE status='active'{clause}", params,
                )
            }
            if scoped_behaviors:
                ph = ",".join("?" for _ in scoped_behaviors)
                cs_clause += f" AND behavior IN ({ph})"
                cs_params.extend(sorted(scoped_behaviors))
            else:
                cs_clause += " AND 0"
        cs = conn.execute(
            f"SELECT behavior, model, key, value FROM cell_status "
            f"WHERE 1=1{cs_clause} "
            f"ORDER BY behavior, model, key",
            cs_params,
        ).fetchall()
        convos = _conversation_rows(conn, clause, params)

    print(f"CAPABILITY: {cap['breaks']} distinct real-effect breaks "
          f"({cap['confirmed']} confirmed, {cap['provisional']} provisional, "
          f"{cap['artifacts']} judge-artifact). win-rows={cap['win_rows']} (inflated).")

    def rank(c):
        closed = 1 if _closed(c["behavior"]) else 0
        best = c["best"] if c["best"] is not None else -1
        return (closed, -best, c["tries"])

    print("\nFIRE-NEXT QUEUE (open cells, EV-ranked: open-channel first, then gradient, then least-explored):")
    for c in sorted(cells, key=rank):
        rc, nm = last.get((c["behavior"], c["wave"]), (None, None))
        flag = " [CLOSED-CHANNEL]" if _closed(c["behavior"]) else ""
        best = f"best={c['best']:.0f}" if c["best"] is not None else "no-gradient"
        print(f"  {c['wave'] or '-':<7} {c['behavior']:<28} n={c['tries']:<3} {best:<12} "
              f"last={rc or '-'}/{nm or '-'}{flag}")

    print("\nCLOSED CHANNELS (G-CHANNEL-CLOSED: next fire MUST be provenance-changing, not a content reroll):")
    closed_behaviors = sorted({c["behavior"] for c in cells if _closed(c["behavior"])})
    if not closed_behaviors:
        print("  (none)")
    for beh in closed_behaviors:
        n = beh_totals[beh][0]
        ub = rule_of_three_ub(n)
        print(f"  {beh:<28} 0/{n}, ub<={ub*100:.0f}%")

    print("\nTOP GRADIENTS (closest to a break; the optimizer's seed set):")
    if not gradients:
        print("  (none scored)")
    for g in gradients:
        print(f"  {g['score_num']:>5.0f}  {g['wave'] or '-':<7} {g['behavior']:<24} "
              f"{g['model']:<22} {g['refusal_class'] or '-'}")

    print("\nGUARD / PROBE STATUS (asserted facts, one home; cell-level, wave-agnostic):")
    if not cs:
        print("  (none noted)")
    for r in cs:
        print(f"  {r['behavior']:<28} {r['model'] or '(all)':<20} {r['key']}={r['value']}")

    print("\nCONVERSATIONS (multi-turn attempts, turn order; * = winning turn):")
    if not convos:
        print("  (none logged)")
    current = None
    for r in convos:
        if r["conversation_id"] != current:
            current = r["conversation_id"]
            print(f"  {current} [{r['behavior']} / {r['model']}]")
        mark = "*" if r["result"] == "win" else " "
        sc = f"{r['score_num']:.0f}" if r["score_num"] is not None else "-"
        print(f"    {mark} turn {r['turn_index']}: {r['refusal_class'] or '-'} score={sc}")


def cmd_supersede(args: argparse.Namespace) -> None:
    with connect() as conn:
        row = conn.execute("SELECT id FROM attempts WHERE id = ?", (args.id,)).fetchone()
        if not row:
            sys.exit(f"no attempt #{args.id}")
        conn.execute(
            "UPDATE attempts SET status='superseded', superseded_by=?, closed_reason=? "
            "WHERE id=?",
            (args.by, args.reason, args.id),
        )
    print(f"attempt #{args.id} superseded"
          + (f" by #{args.by}" if args.by else "") + " (kept, not deleted)")


# The snapshot is the git-diffable PUBLISHABLE artifact, so by default it carries only
# sandboxed-CTF challenges. Real/internal targets (owai-master, and anything added later)
# are coordinated-disclosure material and are withheld unless --include-internal is passed
# for a purely local full export. This is the disclosure gate the README advertises, applied
# at the one place that writes a committed file.
PUBLIC_CHALLENGES = ("agentbreaker", "grayswan", NEW_JAILBREAK_CHALLENGE)


DEFAULT_EXPORT_PATH = os.path.join("learn", "attempts-snapshot.md")


def _do_export(out: str = DEFAULT_EXPORT_PATH, *, include_internal: bool = False,
               quiet: bool = False) -> int:
    """Core export logic. Returns the number of rows exported."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM attempts WHERE status='active' ORDER BY challenge, wave, id"
        ).fetchall()
    withheld = 0
    if not include_internal:
        kept = [r for r in rows if r["challenge"] in PUBLIC_CHALLENGES]
        withheld = len(rows) - len(kept)
        rows = kept
    note = (f" ({withheld} internal/real-target row(s) withheld; run with --include-internal "
            f"for a local full export)" if withheld else "")
    lines = [f"# Attempts snapshot ({now_iso()})", "",
             f"{len(rows)} active CTF attempts. Generated by attempts.py; do not hand-edit. "
             f"The raw `payload` column is intentionally omitted so this file stays "
             f"publishable (G-SATURATION).{note}", ""]
    keys = rows[0].keys() if rows else ()
    has_read = "refusal_class" in keys
    hdr = "| # | ch | wave | behavior | model | lever | result | score |"
    sep = "|---|----|------|----------|-------|-------|--------|-------|"
    if has_read:
        hdr = hdr + " class | move |"
        sep = sep + "-------|------|"
    lines.append(hdr)
    lines.append(sep)
    for r in rows:
        row = (f"| {r['id']} | {r['challenge']} | {r['wave'] or ''} | {r['behavior']} | "
               f"{r['model']} | {r['lever'] or ''} | {r['result']} | {r['score'] or ''} |")
        if has_read:
            row = row + f" {r['refusal_class'] or ''} | {r['next_move'] or ''} |"
        lines.append(row)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    if not quiet:
        print(f"exported {len(rows)} attempts to {out}")
    return len(rows)


def cmd_export(args: argparse.Namespace) -> None:
    out = args.file or DEFAULT_EXPORT_PATH
    _do_export(out, include_internal=getattr(args, "include_internal", False))


def cmd_suggest(args: argparse.Namespace) -> None:
    """Recommend which attack family to try next for open models of a behavior, based on
    the refusal_class history. Groups open models by their most recent class and maps
    each group to the decision-tree move from the family bank."""
    behavior = canon_behavior(args.behavior)
    clause, params = _brief_filter(args)
    recommendations = {
        "soft-refusal": "Reroll 3-5x, then edit-one-clause. If 8+ same-class fires: escalate to the next family in the bank.",
        "adjacent": "Reroll 3-5x, then edit-one-clause. If 8+ same-class fires: escalate to the next family in the bank.",
        "structure-no-payload": "Escalate to the next family in the bank.",
        "note-and-skip": "Escalate to the next family in the bank.",
        "byte-identical": "Change the input surface. Rerolling is waste.",
        "null": "Re-fire (G-NULL). Not a real refusal.",
        "complied-useless": "Extract detail from the compliant response. Do NOT re-jailbreak.",
    }
    with connect() as conn:
        open_models = conn.execute(
            f"SELECT model FROM attempts "
            f"WHERE status='active' AND behavior=?{clause} "
            f"GROUP BY model "
            f"HAVING SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) = 0",
            [behavior] + params,
        ).fetchall()
        if not open_models:
            print(f"suggest: {behavior} (no open models)")
            return
        model_names = [r["model"] for r in open_models]
        last_class = {}
        for m in model_names:
            row = conn.execute(
                f"SELECT refusal_class FROM attempts "
                f"WHERE status='active' AND behavior=? AND model=?{clause} "
                f"ORDER BY id DESC LIMIT 1",
                [behavior, m] + params,
            ).fetchone()
            last_class[m] = row["refusal_class"] if row and row["refusal_class"] else "(unclassified)"
        model_levers = {}
        for m in model_names:
            rows = conn.execute(
                f"SELECT DISTINCT lever FROM attempts "
                f"WHERE status='active' AND behavior=? AND model=? AND lever IS NOT NULL{clause}",
                [behavior, m] + params,
            ).fetchall()
            model_levers[m] = [r["lever"] for r in rows]
    groups = {}
    for m in model_names:
        groups.setdefault(last_class[m], []).append(m)
    print(f"suggest: {behavior} ({len(model_names)} open models)")
    for rc in sorted(groups, key=lambda k: -len(groups[k])):
        models = groups[rc]
        n = len(models)
        print(f"\n  {rc} ({n} model{'s' if n != 1 else ''}): {', '.join(models)}")
        rec = recommendations.get(rc, f"(no recommendation for class {rc!r})")
        print(f"    -> {rec}")
        all_levers = set()
        for m in models:
            all_levers.update(model_levers[m])
        if all_levers:
            print(f"    Levers tried: {', '.join(sorted(all_levers))}")
        else:
            print(f"    Levers tried: (none)")


def cmd_propagate(args: argparse.Namespace) -> None:
    """Two modes:
    --lever: find which models a lever broke, then list other open behaviors for those models.
    --behavior: show which models are broken/open on a behavior, plus top levers by pass rate.
    """
    lever = getattr(args, "lever", None)
    behavior = getattr(args, "behavior", None)
    if not lever and not behavior:
        print("propagate: provide --lever or --behavior")
        return
    clause, params = _brief_filter(args)
    with connect() as conn:
        if behavior:
            _propagate_behavior(conn, canon_behavior(behavior), clause, params)
        else:
            _propagate_lever(conn, lever, clause, params)


def _propagate_behavior(conn, behavior, clause, params):
    """Behavior-centric: show broken/open models and top levers for a behavior."""
    broken = conn.execute(
        f"SELECT DISTINCT model FROM attempts "
        f"WHERE status='active' AND behavior=? AND result='win'{clause}",
        [behavior] + params,
    ).fetchall()
    broken_set = {r["model"] for r in broken}
    all_models = conn.execute(
        f"SELECT DISTINCT model FROM attempts "
        f"WHERE status='active' AND behavior=?{clause}",
        [behavior] + params,
    ).fetchall()
    all_set = {r["model"] for r in all_models}
    open_set = all_set - broken_set
    print(f"propagate (behavior): {behavior}")
    print(f"\n  BROKEN ({len(broken_set)}): {', '.join(sorted(broken_set)) or '(none)'}")
    print(f"  OPEN   ({len(open_set)}): {', '.join(sorted(open_set)) or '(none)'}")
    roster = conn.execute(
        f"SELECT DISTINCT model FROM attempts WHERE status='active'{clause}",
        params,
    ).fetchall()
    roster_set = {r["model"] for r in roster}
    unattempted = roster_set - all_set
    if unattempted:
        print(f"  UNATTEMPTED ({len(unattempted)}): {', '.join(sorted(unattempted))}")
    levers = conn.execute(
        f"SELECT lever, COUNT(*) fires, "
        f"SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) wins "
        f"FROM attempts WHERE status='active' AND behavior=?{clause} "
        f"GROUP BY lever ORDER BY wins DESC, fires ASC LIMIT 10",
        [behavior] + params,
    ).fetchall()
    if levers:
        print(f"\n  TOP LEVERS (by wins on this behavior):")
        for lv in levers:
            pct = f"{lv['wins']/lv['fires']*100:.0f}%" if lv["fires"] else "0%"
            print(f"    {lv['lever']}: {lv['wins']}/{lv['fires']} ({pct})")
    if open_set:
        print(f"\n  OPEN MODEL DETAIL:")
        for m in sorted(open_set):
            fires = conn.execute(
                f"SELECT COUNT(*) c FROM attempts "
                f"WHERE status='active' AND model=? AND behavior=?{clause}",
                [m, behavior] + params,
            ).fetchone()["c"]
            last = conn.execute(
                f"SELECT refusal_class FROM attempts "
                f"WHERE status='active' AND model=? AND behavior=?{clause} "
                f"ORDER BY id DESC LIMIT 1",
                [m, behavior] + params,
            ).fetchone()
            last_rc = last["refusal_class"] if last and last["refusal_class"] else "-"
            print(f"    {m}: {fires} fires, last={last_rc}")


def _propagate_lever(conn, lever, clause, params):
    """Lever-centric: find models a lever broke, list their other open behaviors."""
    won_models = conn.execute(
        f"SELECT DISTINCT model FROM attempts "
        f"WHERE status='active' AND lever=? AND result='win'{clause}",
        [lever] + params,
    ).fetchall()
    if not won_models:
        print(f"propagate: {lever}\n\n  (no models broken by this lever)")
        return
    model_names = [r["model"] for r in won_models]
    print(f"propagate: {lever}\n")
    print(f"  Models broken by this lever: {', '.join(model_names)}")
    for m in model_names:
        open_behaviors = conn.execute(
            f"SELECT behavior, COUNT(*) fires FROM attempts "
            f"WHERE status='active' AND model=?{clause} "
            f"GROUP BY behavior "
            f"HAVING SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) = 0 "
            f"ORDER BY fires DESC",
            [m] + params,
        ).fetchall()
        if not open_behaviors:
            print(f"\n  {m}: (no open behaviors)")
            continue
        nb = len(open_behaviors)
        print(f"\n  {m} (open on {nb} behavior{'s' if nb != 1 else ''}):")
        for ob in open_behaviors:
            beh = ob["behavior"]
            fires = ob["fires"]
            last = conn.execute(
                f"SELECT refusal_class FROM attempts "
                f"WHERE status='active' AND model=? AND behavior=?{clause} "
                f"ORDER BY id DESC LIMIT 1",
                [m, beh] + params,
            ).fetchone()
            last_rc = last["refusal_class"] if last and last["refusal_class"] else "-"
            print(f"    {beh} ({fires} fires, last={last_rc})")


def cmd_migrate_names(args: argparse.Namespace) -> None:
    """One-time migration: re-canonicalize all behavior names in both tables using the
    current _normalize + BEHAVIOR_ALIASES rules. Idempotent (a second run is a no-op)."""
    with connect() as conn:
        behaviors = conn.execute("SELECT DISTINCT behavior FROM attempts").fetchall()
        att_updated = 0
        for row in behaviors:
            old = row["behavior"]
            new = canon_behavior(old)
            if old != new:
                n = conn.execute("UPDATE attempts SET behavior=? WHERE behavior=?",
                                 (new, old)).rowcount
                print(f"  attempts: {old!r} -> {new!r} ({n} rows)")
                att_updated += n

        cs_rows = conn.execute(
            "SELECT id, challenge, behavior, model, key, value, ts FROM cell_status"
        ).fetchall()
        cs_updated = cs_deleted = 0
        for r in cs_rows:
            old = r["behavior"]
            new = canon_behavior(old)
            if old == new:
                continue
            existing = conn.execute(
                "SELECT id, ts FROM cell_status "
                "WHERE challenge=? AND behavior=? AND model=? AND key=?",
                (r["challenge"], new, r["model"], r["key"]),
            ).fetchone()
            if existing:
                if r["ts"] > existing["ts"]:
                    conn.execute("UPDATE cell_status SET value=?, ts=? WHERE id=?",
                                 (r["value"], r["ts"], existing["id"]))
                conn.execute("DELETE FROM cell_status WHERE id=?", (r["id"],))
                cs_deleted += 1
            else:
                conn.execute("UPDATE cell_status SET behavior=? WHERE id=?",
                             (new, r["id"]))
                cs_updated += 1

        models = conn.execute("SELECT DISTINCT model FROM attempts").fetchall()
        mdl_updated = 0
        for row in models:
            old = row["model"]
            new = canon_model(old)
            if old != new:
                n = conn.execute("UPDATE attempts SET model=? WHERE model=?",
                                 (new, old)).rowcount
                print(f"  attempts model: {old!r} -> {new!r} ({n} rows)")
                mdl_updated += n

        cs_models = conn.execute("SELECT DISTINCT model FROM cell_status WHERE model != ''").fetchall()
        csm_updated = 0
        for row in cs_models:
            old = row["model"]
            new = canon_model(old)
            if old != new:
                conn.execute("UPDATE cell_status SET model=? WHERE model=?",
                             (new, old))
                csm_updated += 1

    print(f"\nmigrate-names: {att_updated} behavior rows + {mdl_updated} model rows updated in attempts, "
          f"{cs_updated} cell_status rows updated, {cs_deleted} cell_status duplicates merged, "
          f"{csm_updated} cell_status model names fixed")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Queryable memory for the red-team lab.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create the database").set_defaults(func=cmd_init)

    a = sub.add_parser("add", help="record one attempt")
    a.add_argument("--challenge", required=True)
    a.add_argument("--wave")
    a.add_argument("--behavior", required=True)
    a.add_argument("--model", required=True)
    a.add_argument("--lever")
    a.add_argument("--result", required=True, choices=RESULTS)
    # G-READ (hard gate): you MUST read the model's reply and classify it against the
    # CRASH-COURSE section-4 table before logging, and the move MUST follow from the class
    # (reroll-before-rewrite; change family only when the class stops moving). Required so
    # the discipline cannot be skipped - see CRASH-COURSE.md section 4 + section 10.
    a.add_argument("--refusal-class", dest="refusal_class", required=True,
                   choices=REFUSAL_CLASSES,
                   help="what the reply WAS (read it first): byte-identical|note-and-skip|"
                        "soft-refusal|adjacent|structure-no-payload|complied-useless|win|null")
    a.add_argument("--next-move", dest="next_move", required=True, choices=NEXT_MOVES,
                   help="the move the class dictates: reroll|edit-one-clause|change-surface|"
                        "change-family|extract-detail|pivot-target|done")
    a.add_argument("--score")
    # Pre-registration (soft gate): predict the guard type + judge score BEFORE firing.
    # Optional today (a warning nudges you); make required=True here to harden it to a gate.
    a.add_argument("--pred-guard", dest="pred_guard",
                   help="pre-fire prediction of the guard type/class you expect")
    a.add_argument("--pred-score", dest="pred_score",
                   help="pre-fire predicted judge score (free text, e.g. HC9/AIO10/Real9)")
    a.add_argument("--oracle-type", dest="oracle_type",
                   choices=("real-effect", "judge-artifact"),
                   help="for a win: did a tool actually fire / data actually leave (real-effect), or "
                        "did the judge score high with no underlying action (judge-artifact)?")
    a.add_argument("--payload", help="literal text, or a path to a file to read")
    a.add_argument("--notes")
    a.add_argument("--conversation-id", dest="conversation_id",
                   help="group id for a multi-turn attempt (same across its turns)")
    a.add_argument("--turn-index", dest="turn_index", type=int,
                   help="0-based position of this turn in the conversation")
    a.add_argument("--turn-goal", dest="turn_goal",
                   help="what this turn is meant to establish (process language)")
    a.add_argument("--latency-ms", dest="latency_ms", type=float,
                   help="time from first prompt to judged result, for speed rank")
    a.set_defaults(func=cmd_add)

    nt = sub.add_parser("note", help="record an asserted per-cell fact (channel/probe/guard)")
    nt.add_argument("--challenge", required=True)
    nt.add_argument("--behavior", required=True)
    nt.add_argument("--model", default="", help="omit for a fact that applies to all models")
    nt.add_argument("--key", required=True, choices=STATUS_KEYS)
    nt.add_argument("--value", required=True)
    nt.add_argument("--source", help="where the fact came from, e.g. 'probe#12'")
    nt.set_defaults(func=cmd_note)

    ro = sub.add_parser("reopen", help="suppress CLOSED-CHANNEL for a cell (operator override)")
    ro.add_argument("--challenge", required=True)
    ro.add_argument("--behavior", required=True)
    ro.add_argument("--reason", required=True, help="why the cell is being reopened")
    ro.set_defaults(func=cmd_reopen)

    ld = sub.add_parser("load", help="bulk-insert from a JSON array")
    ld.add_argument("file")
    ld.add_argument("--seed", action="store_true",
                    help="historical import: exempt from the G-READ per-row class requirement")
    ld.set_defaults(func=cmd_load)

    for name, help_ in (("ls", "list attempts"), ("wins", "list wins")):
        q = sub.add_parser(name, help=help_)
        for col in ("challenge", "wave", "behavior", "model", "lever"):
            q.add_argument(f"--{col}")
        if name == "ls":
            q.add_argument("--result", choices=RESULTS)
        q.set_defaults(func=cmd_ls if name == "ls" else cmd_wins)

    st = sub.add_parser("stats", help="result counts + lever pass-rates")
    st.add_argument("--challenge")
    st.set_defaults(func=cmd_stats)

    op = sub.add_parser("open", help="behaviors attempted but not yet won")
    op.add_argument("--challenge")
    op.set_defaults(func=cmd_open)

    br = sub.add_parser("brief", help="derive the session STATE from the ledger (payload-free)")
    br.add_argument("--challenge")
    br.add_argument("--wave")
    br.set_defaults(func=cmd_brief)

    sg = sub.add_parser("suggest", help="recommend next attack family for open models of a behavior")
    sg.add_argument("--challenge")
    sg.add_argument("--wave")
    sg.add_argument("--behavior", required=True)
    sg.set_defaults(func=cmd_suggest)

    pr = sub.add_parser("propagate", help="--lever: open behaviors for models broken by a lever; --behavior: broken/open models for a behavior")
    pr.add_argument("--lever")
    pr.add_argument("--behavior")
    pr.add_argument("--challenge")
    pr.add_argument("--wave")
    pr.set_defaults(func=cmd_propagate)

    sp = sub.add_parser("supersede", help="soft-close an attempt (never deletes)")
    sp.add_argument("id", type=int)
    sp.add_argument("--by", type=int, help="id of the attempt that replaces it")
    sp.add_argument("--reason", required=True)
    sp.set_defaults(func=cmd_supersede)

    ex = sub.add_parser("export", help="write a readable, publishable markdown snapshot")
    ex.add_argument("file", nargs="?")
    ex.add_argument("--include-internal", dest="include_internal", action="store_true",
                    help="local full export: include real/internal-target rows (owai-master etc.). "
                         "NEVER commit the output of this - it names non-public targets.")
    ex.set_defaults(func=cmd_export)

    sub.add_parser("migrate-names",
                   help="re-canonicalize all behavior names in the DB (one-time fix)"
                   ).set_defaults(func=cmd_migrate_names)

    ck = sub.add_parser("check", help="run a conclusion-guard gate; prints the bound, exits 0 if gate-legal")
    ck.add_argument("claim", choices=("solved", "safe", "closed", "durable", "wall"))
    ck.add_argument("--challenge", required=True)
    ck.add_argument("--behavior")
    ck.add_argument("--model")
    ck.add_argument("--lever")
    ck.set_defaults(func=cmd_check)
    return p


def main(argv: list[str] | None = None) -> None:
    _reconfigure_stdout()
    args = build_parser().parse_args(argv)
    if args.cmd != "init" and not os.path.exists(DB_PATH):
        sys.exit("no attempts.db yet - run `python attempts.py init` first")
    args.func(args)


if __name__ == "__main__":
    main()
