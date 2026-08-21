#!/usr/bin/env python3
"""Smoke tests for attempts.py, covering the exact bugs the 2026-08-20 audit found.

Run: python -m unittest discover -s tests   (or: python tests/test_attempts.py)
Uses a throwaway temp DB, never the real attempts.db.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import attempts  # noqa: E402


class AttemptsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = attempts.DB_PATH
        attempts.DB_PATH = os.path.join(self.tmp, "t.db")
        self._run("init")

    def tearDown(self):
        attempts.DB_PATH = self._orig

    def _run(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            attempts.main(list(argv))
        return buf.getvalue()

    def _seed(self, records):
        path = os.path.join(self.tmp, "s.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(records, fh)
        return path

    # --- item 2: the cp1252 crash --------------------------------------------
    def test_stats_renders_non_latin_glyph_without_crashing(self):
        # A lever with an hourglass + arrow is exactly what crashed stats on Windows.
        self._run("add", "--challenge", "grayswan", "--behavior", "b", "--model", "m",
                  "--lever", "wait ⌛ then → buy", "--result", "block",
                  "--refusal-class", "note-and-skip", "--next-move", "change-surface")
        out = self._run("stats")  # must not raise UnicodeEncodeError
        self.assertIn("⌛", out)

    # --- item 3: behavior + challenge canonicalization on insert -------------
    def test_behavior_and_challenge_aliases_fold_on_insert(self):
        for beh in ("cpf", "CPF", "call-a-prohibited-function"):
            self._run("add", "--challenge", "grayswan-luckybreak", "--behavior", beh,
                      "--model", "m", "--result", "block",
                      "--refusal-class", "note-and-skip", "--next-move", "change-surface")
        out = self._run("open", "--challenge", "grayswan")  # folded challenge queryable
        self.assertIn("call-prohibited-function", out)
        self.assertNotIn("cpf", out)
        self.assertNotIn("CPF", out)
        # one canonical bucket of 3 tries, not three buckets of 1
        self.assertIn("3 tries", out)

    def test_won_behavior_is_not_reported_open_across_spellings(self):
        # The core bug: a win under one spelling must suppress losses under its variants.
        self._run("add", "--challenge", "grayswan", "--behavior", "call-prohibited-function",
                  "--model", "VS", "--result", "win", "--score", "100",
                  "--refusal-class", "win", "--next-move", "done")
        self._run("add", "--challenge", "grayswan", "--behavior", "cpf", "--model", "Eel",
                  "--result", "block", "--refusal-class", "note-and-skip",
                  "--next-move", "change-surface")
        out = self._run("open", "--challenge", "grayswan")
        self.assertIn("no open behaviors", out.lower())

    # --- item 8: G-READ gate on the bulk-load path --------------------------
    def test_load_rejects_post_rule_row_without_class(self):
        path = self._seed([{"challenge": "grayswan", "behavior": "b", "model": "m",
                            "result": "block", "ts": "2026-08-20T10:00:00+00:00"}])
        with self.assertRaises(SystemExit):
            self._run("load", path)  # no --seed, no class -> rejected

    def test_load_seed_flag_allows_historical_no_class(self):
        path = self._seed([{"challenge": "grayswan", "behavior": "b", "model": "m",
                            "result": "block", "ts": "2026-08-01T10:00:00+00:00"}])
        out = self._run("load", path, "--seed")
        self.assertIn("1 inserted", out)

    # --- item 9: load robustness + idempotency ------------------------------
    def test_load_is_idempotent(self):
        recs = [{"challenge": "grayswan", "behavior": "b", "model": "m", "result": "block",
                 "lever": "x", "notes": "n", "refusal_class": "note-and-skip",
                 "next_move": "change-surface", "ts": "2026-08-20T10:00:00+00:00"}]
        path = self._seed(recs)
        self._run("load", path)
        out = self._run("load", path)  # second load must skip, not double-count
        self.assertIn("0 inserted", out)
        self.assertIn("1 skipped", out)

    def test_load_bad_row_does_not_sink_the_batch(self):
        path = self._seed([
            {"challenge": "grayswan", "behavior": "b", "model": "m", "result": "block",
             "refusal_class": "note-and-skip", "next_move": "change-surface",
             "ts": "2026-08-20T10:00:00+00:00", "notes": "good"},
            {"challenge": "grayswan", "behavior": "b", "model": "m", "result": "NOT_A_RESULT",
             "refusal_class": "note-and-skip", "next_move": "change-surface",
             "ts": "2026-08-20T10:00:00+00:00", "notes": "bad enum"},
        ])
        out = self._run("load", path)
        self.assertIn("1 inserted", out)
        self.assertIn("1 failed", out)

    # --- item 12: score_num extraction + public export gate -----------------
    def test_score_num_extracted(self):
        self._run("add", "--challenge", "grayswan", "--behavior", "b", "--model", "m",
                  "--result", "near_miss", "--score", "flip 100/100 OpSec 20",
                  "--refusal-class", "soft-refusal", "--next-move", "reroll")
        import sqlite3
        c = sqlite3.connect(attempts.DB_PATH)
        self.assertEqual(c.execute("SELECT score_num FROM attempts").fetchone()[0], 100.0)

    def test_export_withholds_internal_targets_by_default(self):
        self._run("add", "--challenge", "internal-target", "--behavior", "internal-behavior", "--model", "AgentX",
                  "--result", "win", "--refusal-class", "win", "--next-move", "done")
        self._run("add", "--challenge", "grayswan", "--behavior", "pub", "--model", "m",
                  "--result", "block", "--refusal-class", "note-and-skip",
                  "--next-move", "change-surface")
        out_path = os.path.join(self.tmp, "snap.md")
        self._run("export", out_path)
        text = open(out_path, encoding="utf-8").read()
        self.assertIn("pub", text)
        self.assertNotIn("AgentX", text)       # internal target withheld
        self.assertNotIn("internal-target", text)

    # --- substrate: cell_status table exists after connect ------------------
    def test_cell_status_table_created(self):
        import sqlite3
        c = sqlite3.connect(attempts.DB_PATH)
        cols = {r[1] for r in c.execute("PRAGMA table_info(cell_status)")}
        self.assertEqual(cols, {"id", "ts", "challenge", "behavior", "model",
                                "key", "value", "source"})

    def test_cell_status_added_to_preexisting_db(self):
        # Simulate an old DB that has attempts but not cell_status, then reconnect.
        import sqlite3
        c = sqlite3.connect(attempts.DB_PATH)
        c.execute("DROP TABLE IF EXISTS cell_status")
        c.commit()
        c.close()
        attempts.connect().close()  # _migrate must recreate it
        c = sqlite3.connect(attempts.DB_PATH)
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("cell_status", names)

    # --- substrate: note upsert + vocab ------------------------------------
    def test_note_roundtrips_and_canonicalizes(self):
        self._run("note", "--challenge", "grayswan-luckybreak", "--behavior", "cpf",
                  "--model", "Ostrich", "--key", "probe", "--value", "escaped")
        import sqlite3
        c = sqlite3.connect(attempts.DB_PATH)
        row = c.execute("SELECT challenge, behavior, model, key, value FROM cell_status").fetchone()
        self.assertEqual(row, ("grayswan", "call-prohibited-function", "Ostrich", "probe", "escaped"))

    def test_note_upserts_one_row_per_fact(self):
        for val in ("open", "closed-appeal-only"):
            self._run("note", "--challenge", "grayswan", "--behavior", "infiltrate",
                      "--key", "channel", "--value", val)
        import sqlite3
        c = sqlite3.connect(attempts.DB_PATH)
        rows = c.execute("SELECT value FROM cell_status WHERE key='channel'").fetchall()
        self.assertEqual(len(rows), 1)                       # upsert, not append
        self.assertEqual(rows[0][0], "closed-appeal-only")   # latest wins

    def test_note_rejects_unknown_key(self):
        with self.assertRaises(SystemExit):
            self._run("note", "--challenge", "grayswan", "--behavior", "b",
                      "--key", "notavalidkey", "--value", "x")

    # --- substrate: stat helpers -------------------------------------------
    def test_rule_of_three_ub(self):
        self.assertAlmostEqual(attempts.rule_of_three_ub(30), 0.1)
        self.assertEqual(attempts.rule_of_three_ub(0), 1.0)

    def test_wilson_lower_bound(self):
        # 0 successes -> lower bound 0; a strong 10/10 -> comfortably above 0.6.
        self.assertEqual(attempts.wilson_lower_bound(0, 0), 0.0)
        self.assertAlmostEqual(attempts.wilson_lower_bound(0, 10), 0.0)
        self.assertGreater(attempts.wilson_lower_bound(10, 10), 0.65)
        self.assertLess(attempts.wilson_lower_bound(8, 10), 0.8)  # 8/10 is NOT durable

    # --- substrate: check gates --------------------------------------------
    def _mkfire(self, behavior, model, result, score=None, lever=None, rc="note-and-skip", nm="change-surface"):
        argv = ["add", "--challenge", "grayswan", "--behavior", behavior, "--model", model,
                "--result", result, "--refusal-class", rc, "--next-move", nm]
        if score is not None:
            argv += ["--score", str(score)]
        if lever is not None:
            argv += ["--lever", lever]
        self._run(*argv)

    def test_check_safe_reports_unmeasured_bound(self):
        for i in range(10):
            self._mkfire("infiltrate", f"m{i}", "block")
        conn = attempts.connect()
        code, msg = attempts.check_verdict(conn, "safe", "grayswan", behavior="infiltrate")
        self.assertEqual(code, 1)                 # never gate-legal to call it safe
        self.assertIn("UNMEASURED", msg)
        self.assertIn("<=30%", msg)               # 3/10 = 30%
        self.assertIn("ABSENT", msg)              # no win in this challenge -> control absent

    def test_check_solved_confirmed_vs_provisional(self):
        self._mkfire("cpf", "VS", "win", score=100, lever="derivation")
        conn = attempts.connect()
        code, msg = attempts.check_verdict(conn, "solved", "grayswan", lever="derivation")
        self.assertEqual(code, 1)                 # 1 pass, n<3 -> provisional
        self.assertIn("PROVISIONAL", msg)
        for _ in range(2):
            self._mkfire("cpf", "VS", "win", score=100, lever="derivation")
        conn = attempts.connect()
        code, msg = attempts.check_verdict(conn, "solved", "grayswan", lever="derivation")
        self.assertEqual(code, 0)                 # n>=3 -> confirmed
        self.assertIn("CONFIRMED", msg)

    def test_check_closed_only_at_n30(self):
        for i in range(29):
            self._mkfire("infiltrate", f"m{i}", "block")
        conn = attempts.connect()
        code, _ = attempts.check_verdict(conn, "closed", "grayswan", behavior="infiltrate")
        self.assertEqual(code, 1)                 # n<30, not closed yet
        self._mkfire("infiltrate", "m29", "block")
        conn = attempts.connect()
        code, msg = attempts.check_verdict(conn, "closed", "grayswan", behavior="infiltrate")
        self.assertEqual(code, 0)
        self.assertIn("CLOSED-CHANNEL", msg)

    def test_check_wall_ctf_is_never_gate_legal(self):
        conn = attempts.connect()
        code, msg = attempts.check_verdict(conn, "wall", "grayswan")
        self.assertEqual(code, 1)
        self.assertIn("SOLVABLE-PRIOR", msg)

    def test_check_cli_exits_nonzero_on_fail(self):
        with self.assertRaises(SystemExit) as cm:
            self._run("check", "wall", "--challenge", "grayswan")
        self.assertEqual(cm.exception.code, 1)

    def test_check_verdict_canonicalizes_challenge(self):
        conn = attempts.connect()
        code, msg = attempts.check_verdict(conn, "wall", "grayswan-luckybreak")
        self.assertEqual(code, 1)
        self.assertIn("SOLVABLE-PRIOR", msg)   # canonicalized to grayswan -> CTF branch

    def test_check_durable_needs_high_n_and_ci(self):
        # 8/10 wins -> Wilson lower bound ~0.49 < 0.8 -> not durable
        for i in range(8):
            self._mkfire("cpf", f"m{i}", "win", lever="stableL")
        for i in range(2):
            self._mkfire("cpf", f"b{i}", "block", lever="stableL")
        conn = attempts.connect()
        code, msg = attempts.check_verdict(conn, "durable", "grayswan", lever="stableL")
        self.assertEqual(code, 1)
        self.assertIn("NOT DURABLE", msg)
        # 20/20 wins -> Wilson lower bound ~0.84 >= 0.8 and n>=10 -> durable
        for i in range(20):
            self._mkfire("essay", f"w{i}", "win", lever="rockL")
        conn = attempts.connect()
        code, msg = attempts.check_verdict(conn, "durable", "grayswan", lever="rockL")
        self.assertEqual(code, 0)
        self.assertNotIn("NOT DURABLE", msg)
        self.assertIn("Wilson", msg)


if __name__ == "__main__":
    unittest.main()
