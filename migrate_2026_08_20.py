#!/usr/bin/env python3
"""
One-off ledger migration (2026-08-20 audit remediation). Idempotent; safe to re-run.

Folds the fragmented category keys that made open/stats lie, backfills score_num and the
19 post-rule NULL refusal_class rows from their own evidence, and tags the aggregate/meta
rows so they stop counting as fires. Run once, then `python attempts.py export`.

A backup (attempts.db.bak-2026-08-20) was taken before this ran.
"""
from __future__ import annotations

import sqlite3

import attempts  # canon_* helpers, _score_num, DB_PATH, _migrate via connect()

# The aggregate/summary rows that are not single fires (id=82 was counted as a WIN).
META_IDS_SQL = """
    behavior IN ('ipi-aggregate', 'BRAINSTORM-META')
    OR behavior LIKE '%(all behaviors)%'
"""


def main() -> None:
    conn = attempts.connect()  # applies _migrate (adds score_num/pred_* columns)
    cur = conn.cursor()

    print("=== BEFORE ===")
    _print_challenge_behavior(cur)

    # 1. Fold challenge variants.
    for src, dst in attempts.CHALLENGE_ALIASES.items():
        n = cur.execute("UPDATE attempts SET challenge=? WHERE challenge=?", (dst, src)).rowcount
        if n:
            print(f"challenge: {src} -> {dst} ({n} rows)")

    # 2. Fold behavior variants.
    for src, dst in attempts.BEHAVIOR_ALIASES.items():
        n = cur.execute("UPDATE attempts SET behavior=? WHERE behavior=?", (dst, src)).rowcount
        if n:
            print(f"behavior: {src} -> {dst} ({n} rows)")

    # 3. Fold Lucky Break wave names.
    for src, dst in attempts.WAVE_ALIASES.items():
        n = cur.execute("UPDATE attempts SET wave=? WHERE wave=?", (dst, src)).rowcount
        if n:
            print(f"wave: {src} -> {dst} ({n} rows)")

    # 4. Backfill score_num from the free-text score for every row that has a parseable number.
    filled = 0
    for rid, score in cur.execute(
            "SELECT id, score FROM attempts WHERE score_num IS NULL AND score IS NOT NULL").fetchall():
        sn = attempts._score_num(score)
        if sn is not None:
            cur.execute("UPDATE attempts SET score_num=? WHERE id=?", (sn, rid))
            filled += 1
    print(f"score_num: backfilled {filled} rows")

    # 5. Backfill the post-rule NULL refusal_class rows. All 19 (ids ~879-897) are CPF
    #    faithful-summary blocks: the model summarized {fill} as data and ignored the
    #    injected buy_item instruction, i.e. note-and-skip -> change-surface. Evidence-based
    #    reconstruction from their own notes, not a fresh classification. Meta rows excluded.
    n = cur.execute(
        f"""UPDATE attempts SET refusal_class='note-and-skip', next_move='change-surface'
            WHERE refusal_class IS NULL AND ts >= ? AND NOT ({META_IDS_SQL})""",
        (attempts.GREAD_RULE_DATE,),
    ).rowcount
    print(f"refusal_class backfill (note-and-skip/change-surface): {n} rows")

    # 6. Tag aggregate/meta rows so stats/open ignore them (status='active' is the filter).
    n = cur.execute(
        f"UPDATE attempts SET status='meta' WHERE status='active' AND ({META_IDS_SQL})").rowcount
    print(f"meta-tagged (excluded from stats/open): {n} rows")

    conn.commit()

    print("\n=== AFTER ===")
    _print_challenge_behavior(cur)
    conn.close()
    print("\nDone. Now run: python attempts.py export learn/attempts-snapshot.md")


def _print_challenge_behavior(cur: sqlite3.Cursor) -> None:
    for r in cur.execute(
            "SELECT challenge, COUNT(DISTINCT behavior) b, COUNT(*) n FROM attempts "
            "WHERE status='active' GROUP BY challenge ORDER BY challenge").fetchall():
        print(f"  {r[0]:<20} {r[1]} distinct behaviors, {r[2]} active rows")


if __name__ == "__main__":
    main()
