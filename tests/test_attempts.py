#!/usr/bin/env python3
"""Smoke tests for attempts.py, covering the exact bugs the 2026-08-20 audit found.

Run: python -m unittest discover -s tests   (or: python tests/test_attempts.py)
Uses a throwaway temp DB, never the real attempts.db.
"""
import argparse
import contextlib
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

    def test_behavior_aliases_fold_on_insert(self):
        # A shorthand alias must fold to its canonical bucket on INSERT so a variant
        # spelling never fragments the rollups. Checked via a direct DB read.
        cases = (("cpf", "call-prohibited-function"),
                 ("download", "encourage-harmful-download"),
                 ("infiltrate", "infiltrate-private-party"))
        for alias, canonical in cases:
            self._run("add", "--challenge", "grayswan", "--behavior", alias,
                      "--model", "m", "--result", "block",
                      "--refusal-class", "note-and-skip", "--next-move", "change-surface")
        import sqlite3
        c = sqlite3.connect(attempts.DB_PATH)
        stored = {r[0] for r in c.execute(
            "SELECT DISTINCT behavior FROM attempts WHERE model='M'")}
        self.assertEqual(stored, {"call-prohibited-function",
                                  "encourage-harmful-download",
                                  "infiltrate-private-party"})

    # --- pred_score is free text, not a bare float (2026-08-23 fix) ----------
    def test_pred_score_accepts_multi_criterion_string(self):
        # Regression: --pred-score "HC9/AIO10/Real9" used to crash on float() coercion.
        # It must now store the raw string verbatim.
        self._run("add", "--challenge", "grayswan", "--behavior", "b", "--model", "m",
                  "--result", "win", "--refusal-class", "win", "--next-move", "done",
                  "--pred-score", "HC9/AIO10/Real9")
        import sqlite3
        c = sqlite3.connect(attempts.DB_PATH)
        stored = c.execute(
            "SELECT pred_score FROM attempts WHERE model='M'").fetchone()[0]
        self.assertEqual(stored, "HC9/AIO10/Real9")

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

    def test_load_is_idempotent_with_conversation_fields(self):
        # Regression: _row_signature carries conversation_id/turn_index, but cmd_load's
        # `seen` query used to omit both columns, so a record with these fields set could
        # never match an already-loaded row and reloading the same file duplicated it.
        recs = [{"challenge": "grayswan", "behavior": "b", "model": "m", "result": "win",
                 "refusal_class": "win", "next_move": "done",
                 "ts": "2026-08-20T10:00:00+00:00",
                 "conversation_id": "c-1", "turn_index": 0}]
        path = self._seed(recs)
        self._run("load", path)
        out = self._run("load", path)  # second load must skip, not double-count
        self.assertIn("0 inserted", out)
        self.assertIn("1 skipped", out)

    def test_load_rejects_non_numeric_turn_index(self):
        path = self._seed([
            {"challenge": "grayswan", "behavior": "b", "model": "m", "result": "win",
             "refusal_class": "win", "next_move": "done",
             "ts": "2026-08-20T10:00:00+00:00", "notes": "good"},
            {"challenge": "grayswan", "behavior": "b", "model": "m", "result": "win",
             "refusal_class": "win", "next_move": "done",
             "ts": "2026-08-20T10:00:01+00:00", "turn_index": "not-a-number"},
        ])
        out = self._run("load", path)
        self.assertIn("1 inserted", out)
        self.assertIn("1 failed", out)

    def test_load_rejects_non_numeric_latency_ms(self):
        path = self._seed([
            {"challenge": "grayswan", "behavior": "b", "model": "m", "result": "win",
             "refusal_class": "win", "next_move": "done",
             "ts": "2026-08-20T10:00:00+00:00", "notes": "good"},
            {"challenge": "grayswan", "behavior": "b", "model": "m", "result": "win",
             "refusal_class": "win", "next_move": "done",
             "ts": "2026-08-20T10:00:01+00:00", "latency_ms": "fast"},
        ])
        out = self._run("load", path)
        self.assertIn("1 inserted", out)
        self.assertIn("1 failed", out)

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

    # --- substrate: brief derives state ------------------------------------
    def test_brief_has_all_sections_and_no_payload(self):
        self._run("add", "--challenge", "grayswan", "--behavior", "cpf", "--model", "Eel",
                  "--result", "near_miss", "--score", "flip 80", "--lever", "derivation",
                  "--payload", "SECRETPAYLOADTEXT", "--refusal-class", "soft-refusal",
                  "--next-move", "reroll")
        self._run("note", "--challenge", "grayswan", "--behavior", "cpf", "--model", "Eel",
                  "--key", "probe", "--value", "escaped")
        out = self._run("brief", "--challenge", "grayswan")
        for header in ("CAPABILITY", "FIRE-NEXT", "CLOSED CHANNELS", "TOP GRADIENTS", "GUARD"):
            self.assertIn(header, out)
        self.assertNotIn("SECRETPAYLOADTEXT", out)   # payload-free by construction
        self.assertIn("escaped", out)                # cell_status surfaced

    def test_brief_fire_next_ranks_open_before_closed(self):
        # infiltrate: a CLOSED channel (30 fires) that ALSO has a HIGH gradient (95).
        # cpf: OPEN with a LOWER gradient (20). Only the open-before-closed tier rule makes
        # cpf rank first; a pure gradient sort would put infiltrate (95) ahead of cpf (20),
        # so this fixture isolates the tier rule from the gradient rule.
        for i in range(29):
            self._run("add", "--challenge", "grayswan", "--behavior", "infiltrate",
                      "--model", f"m{i}", "--result", "block",
                      "--refusal-class", "note-and-skip", "--next-move", "change-surface")
        self._run("add", "--challenge", "grayswan", "--behavior", "infiltrate",
                  "--model", "m29", "--result", "near_miss", "--score", "95",
                  "--refusal-class", "soft-refusal", "--next-move", "reroll")
        self._run("add", "--challenge", "grayswan", "--behavior", "cpf", "--model", "Eel",
                  "--result", "near_miss", "--score", "20",
                  "--refusal-class", "soft-refusal", "--next-move", "reroll")
        out = self._run("brief", "--challenge", "grayswan")
        fire_next = out.split("FIRE-NEXT")[1].split("CLOSED CHANNELS")[0]
        self.assertLess(fire_next.index("call-prohibited-function"), fire_next.index("infiltrate"))

    def test_closed_channel_partition_agrees_across_commands(self):
        # infiltrate: 15 in LB-Easy + 15 in LB-Medium = 30 behavior-level, 0 wins. A channel is
        # behavior-level, so open, check closed, and brief must ALL call it closed, even though
        # neither wave alone reaches 30 (whole-branch review Important #1).
        for wave in ("LB-Easy", "LB-Medium"):
            for i in range(15):
                self._run("add", "--challenge", "grayswan", "--behavior", "infiltrate",
                          "--wave", wave, "--model", f"{wave}m{i}", "--result", "block",
                          "--refusal-class", "note-and-skip", "--next-move", "change-surface")
        conn = attempts.connect()
        code, msg = attempts.check_verdict(conn, "closed", "grayswan", behavior="infiltrate")
        self.assertEqual(code, 0)
        self.assertIn("CLOSED-CHANNEL", msg)
        out_open = self._run("open", "--challenge", "grayswan")
        self.assertIn("CLOSED-CHANNEL", out_open)
        out_brief = self._run("brief", "--challenge", "grayswan")
        closed_section = out_brief.split("CLOSED CHANNELS")[1].split("TOP GRADIENTS")[0]
        self.assertIn("infiltrate", closed_section)

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

    def test_capability_confirmed_is_challenge_scoped(self):
        # Same lever wins 2x in grayswan and 1x in agentbreaker. Scoped to grayswan the lever
        # has n=2 (< CONFIRM_N), so its wins are PROVISIONAL, not confirmed - brief CAPABILITY
        # must agree with check solved rather than counting the agentbreaker win (Important #2).
        for _ in range(2):
            self._mkfire("cpf", "m", "win", lever="crosslever")
        self._run("add", "--challenge", "agentbreaker", "--behavior", "b", "--model", "m",
                  "--result", "win", "--lever", "crosslever",
                  "--refusal-class", "win", "--next-move", "done")
        conn = attempts.connect()
        cap = attempts._capability_counts(conn, "grayswan")
        self.assertEqual(cap["confirmed"], 0)     # before the fix this was 2 (counted agentbreaker)
        self.assertEqual(cap["provisional"], 2)   # both grayswan crosslever wins are provisional
        code, msg = attempts.check_verdict(conn, "solved", "grayswan", lever="crosslever")
        self.assertEqual(code, 1)
        self.assertIn("PROVISIONAL", msg)

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

    def test_compete_mode_banks_first_break(self):
        with attempts.connect() as conn:
            attempts.add_attempt(conn, {
                "challenge": "grayswan", "behavior": "b1", "model": "M1",
                "lever": "L1", "result": "win", "refusal_class": "win",
                "next_move": "done"})
            code_n, _ = attempts.check_verdict(conn, "solved", "grayswan",
                                               lever="L1", mode="normal")
            code_c, msg_c = attempts.check_verdict(conn, "solved", "grayswan",
                                                   lever="L1", mode="compete")
        self.assertEqual(code_n, 1)   # normal: provisional at n=1
        self.assertEqual(code_c, 0)   # compete: first break is legal
        self.assertIn("COMPETE", msg_c)

    def test_default_mode_is_normal_behavior_unchanged(self):
        # Omitting mode entirely must behave exactly like mode="normal" (off by default).
        with attempts.connect() as conn:
            attempts.add_attempt(conn, {
                "challenge": "grayswan", "behavior": "b1", "model": "M1",
                "lever": "L2", "result": "win", "refusal_class": "win",
                "next_move": "done"})
            code, msg = attempts.check_verdict(conn, "solved", "grayswan", lever="L2")
        self.assertEqual(code, 1)
        self.assertIn("PROVISIONAL", msg)

    def test_check_cli_mode_compete_exits_zero_on_first_break(self):
        self._mkfire("cpf", "VS", "win", score=100, lever="cli-lever")
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit) as cm:
                attempts.main(["check", "solved", "--challenge", "grayswan",
                              "--lever", "cli-lever", "--mode", "compete"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("COMPETE", buf.getvalue())

    def test_brief_compete_mode_prints_header(self):
        out = self._run("brief", "--challenge", "grayswan", "--mode", "compete")
        self.assertTrue(out.startswith("COMPETE MODE:"))

    def test_brief_normal_mode_has_no_compete_header(self):
        out = self._run("brief", "--challenge", "grayswan")
        self.assertFalse(out.startswith("COMPETE MODE:"))

    def test_hazard_hunt_aliases_are_public_ctf(self):
        self._run("add", "--challenge", "grayswan-hazard-hunt", "--behavior", "misc",
                  "--model", "m", "--result", "block",
                  "--refusal-class", "note-and-skip", "--next-move", "change-surface")
        out_path = os.path.join(self.tmp, "snap.md")
        self._run("export", out_path)
        text = open(out_path, encoding="utf-8").read()
        self.assertIn("hazard-hunt-q3", text)
        conn = attempts.connect()
        code, msg = attempts.check_verdict(conn, "wall", "hazardhunt")
        self.assertEqual(code, 1)
        self.assertIn("SOLVABLE-PRIOR", msg)

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


    # --- substrate: reopen operator override --------------------------------
    def test_reopen_suppresses_closed_channel_in_check(self):
        for i in range(35):
            self._mkfire("infiltrate", f"m{i}", "block")
        conn = attempts.connect()
        code, msg = attempts.check_verdict(conn, "closed", "grayswan", behavior="infiltrate")
        self.assertEqual(code, 0)
        self.assertIn("CLOSED-CHANNEL", msg)
        # Now reopen
        self._run("reopen", "--challenge", "grayswan", "--behavior", "infiltrate",
                  "--reason", "operator override: not actually closed")
        conn = attempts.connect()
        code, msg = attempts.check_verdict(conn, "closed", "grayswan", behavior="infiltrate")
        self.assertEqual(code, 1)
        self.assertIn("REOPENED", msg)

    def test_reopen_suppresses_closed_channel_in_brief(self):
        for i in range(30):
            self._mkfire("infiltrate", f"m{i}", "block")
        out = self._run("brief", "--challenge", "grayswan")
        self.assertIn("[CLOSED-CHANNEL]", out)
        closed_section = out.split("CLOSED CHANNELS")[1].split("TOP GRADIENTS")[0]
        self.assertIn("infiltrate", closed_section)
        # Reopen and verify the flag is gone
        self._run("reopen", "--challenge", "grayswan", "--behavior", "infiltrate",
                  "--reason", "nothing is closed")
        out = self._run("brief", "--challenge", "grayswan")
        self.assertNotIn("[CLOSED-CHANNEL]", out)
        closed_section = out.split("CLOSED CHANNELS")[1].split("TOP GRADIENTS")[0]
        self.assertNotIn("infiltrate", closed_section)

    def test_reopen_suppresses_closed_channel_in_open(self):
        for i in range(30):
            self._mkfire("infiltrate", f"m{i}", "block")
        out = self._run("open", "--challenge", "grayswan")
        # The behavior line (not the header legend) should show CLOSED-CHANNEL
        beh_line = [l for l in out.splitlines() if "infiltrate" in l][0]
        self.assertIn("CLOSED-CHANNEL", beh_line)
        self._run("reopen", "--challenge", "grayswan", "--behavior", "infiltrate",
                  "--reason", "test override")
        out = self._run("open", "--challenge", "grayswan")
        beh_line = [l for l in out.splitlines() if "infiltrate" in l][0]
        self.assertNotIn("CLOSED-CHANNEL", beh_line)
        self.assertIn("REOPENED", beh_line)

    def test_reopen_canonicalizes_behavior(self):
        for i in range(30):
            self._mkfire("call-prohibited-function", f"m{i}", "block")
        # Use alias "cpf" in the reopen command
        self._run("reopen", "--challenge", "grayswan", "--behavior", "cpf",
                  "--reason", "alias test")
        conn = attempts.connect()
        code, msg = attempts.check_verdict(conn, "closed", "grayswan",
                                           behavior="call-prohibited-function")
        self.assertEqual(code, 1)
        self.assertIn("REOPENED", msg)

    def test_reopen_ranks_cell_as_open_in_brief(self):
        # A reopened cell with high n and a gradient should rank BEFORE a truly-closed cell
        # (same tier as open cells, not demoted to the closed tier).
        for i in range(30):
            self._mkfire("infiltrate", f"m{i}", "block")
        self._mkfire("infiltrate", "m30", "near_miss", score="95")
        for i in range(30):
            self._mkfire("call-prohibited-function", f"c{i}", "block")
        # Reopen infiltrate but not cpf
        self._run("reopen", "--challenge", "grayswan", "--behavior", "infiltrate",
                  "--reason", "test")
        out = self._run("brief", "--challenge", "grayswan")
        fire_next = out.split("FIRE-NEXT")[1].split("CLOSED CHANNELS")[0]
        # infiltrate (reopened, has gradient) should come before cpf (still closed)
        self.assertLess(fire_next.index("infiltrate"), fire_next.index("call-prohibited-function"))

    # --- substrate: suggest subcommand ----------------------------------------
    def test_suggest_groups_by_refusal_class(self):
        self._mkfire("web-vuln", "Model-A", "block", rc="note-and-skip", nm="change-surface")
        self._mkfire("web-vuln", "Model-B", "block", rc="note-and-skip", nm="change-surface")
        self._mkfire("web-vuln", "Model-C", "block", rc="soft-refusal", nm="reroll")
        out = self._run("suggest", "--behavior", "web-vuln", "--challenge", "grayswan")
        self.assertIn("3 open models", out)
        self.assertIn("note-and-skip (2 models)", out)
        self.assertIn("soft-refusal (1 model)", out)
        self.assertIn("Escalate to the next family", out)   # note-and-skip recommendation
        self.assertIn("Reroll", out)       # soft-refusal recommendation

    def test_suggest_excludes_won_models(self):
        self._mkfire("web-vuln", "Model-A", "win", rc="win", nm="done")
        self._mkfire("web-vuln", "Model-B", "block", rc="note-and-skip", nm="change-surface")
        out = self._run("suggest", "--behavior", "web-vuln", "--challenge", "grayswan")
        self.assertIn("1 open models", out)
        self.assertIn("Model B", out)
        self.assertNotIn("Model A", out)

    def test_suggest_shows_levers_tried(self):
        self._mkfire("web-vuln", "Model-A", "block", lever="lever-alpha",
                     rc="note-and-skip", nm="change-surface")
        self._mkfire("web-vuln", "Model-A", "block", lever="lever-beta",
                     rc="note-and-skip", nm="change-surface")
        out = self._run("suggest", "--behavior", "web-vuln", "--challenge", "grayswan")
        self.assertIn("lever-alpha", out)
        self.assertIn("lever-beta", out)

    def test_suggest_canonicalizes_behavior(self):
        self._mkfire("call-prohibited-function", "Model-A", "block",
                     rc="soft-refusal", nm="reroll")
        out = self._run("suggest", "--behavior", "cpf", "--challenge", "grayswan")
        self.assertIn("1 open models", out)
        self.assertIn("Model A", out)

    def test_suggest_no_open_models(self):
        self._mkfire("web-vuln", "Model-A", "win", rc="win", nm="done")
        out = self._run("suggest", "--behavior", "web-vuln", "--challenge", "grayswan")
        self.assertIn("no open models", out)

    # --- substrate: propagate subcommand --------------------------------------
    def test_propagate_finds_open_behaviors(self):
        self._mkfire("web-vuln", "Model-A", "win", lever="myLever", rc="win", nm="done")
        self._mkfire("weak-cred", "Model-A", "block", lever="otherL",
                     rc="note-and-skip", nm="change-surface")
        out = self._run("propagate", "--lever", "myLever", "--challenge", "grayswan")
        self.assertIn("Model A", out)
        self.assertIn("weak-cred", out)
        # web-vuln was won, so it should NOT appear in the open behaviors section
        after_header = out.split("open on")[1]
        self.assertNotIn("web-vuln", after_header)

    def test_propagate_shows_fire_count_and_last_class(self):
        self._mkfire("web-vuln", "Model-A", "win", lever="myLever", rc="win", nm="done")
        self._mkfire("weak-cred", "Model-A", "block", lever="L1",
                     rc="byte-identical", nm="change-surface")
        self._mkfire("weak-cred", "Model-A", "block", lever="L2",
                     rc="soft-refusal", nm="reroll")
        out = self._run("propagate", "--lever", "myLever", "--challenge", "grayswan")
        self.assertIn("weak-cred (2 fires, last=soft-refusal)", out)

    def test_propagate_no_wins_for_lever(self):
        self._mkfire("web-vuln", "Model-A", "block", lever="myLever",
                     rc="note-and-skip", nm="change-surface")
        out = self._run("propagate", "--lever", "myLever", "--challenge", "grayswan")
        self.assertIn("no models broken by this lever", out)

    def test_propagate_model_with_no_open_behaviors(self):
        self._mkfire("web-vuln", "Model-A", "win", lever="myLever", rc="win", nm="done")
        out = self._run("propagate", "--lever", "myLever", "--challenge", "grayswan")
        self.assertIn("Model A", out)
        self.assertIn("no open behaviors", out)


    # --- normalization: Title Case / spaces fold to canonical kebab-case --------
    def test_normalize_folds_title_case_and_spaces(self):
        self.assertEqual(attempts._normalize("Weak Password Change"),
                         "weak-password-change")
        self.assertEqual(attempts._normalize("Call A Prohibited Function"),
                         "call-a-prohibited-function")
        self.assertEqual(attempts._normalize("inflammatory linkedin post"),
                         "inflammatory-linkedin-post")

    def test_normalize_collapses_underscores_and_multiple_hyphens(self):
        self.assertEqual(attempts._normalize("foo__bar--baz"), "foo-bar-baz")
        self.assertEqual(attempts._normalize("  --leading-- "), "leading")

    def test_canon_behavior_normalizes_before_alias_lookup(self):
        self.assertEqual(attempts.canon_behavior("CPF"), "call-prohibited-function")
        self.assertEqual(attempts.canon_behavior("Call A Prohibited Function"),
                         "call-prohibited-function")
        self.assertEqual(attempts.canon_behavior("Download"), "encourage-harmful-download")
        self.assertEqual(attempts.canon_behavior("Exploit Gift Recommendation"),
                         "exploit-gift-rec")

    def test_title_case_behavior_folds_into_canonical_bucket(self):
        self._run("add", "--challenge", "grayswan", "--behavior",
                  "Call A Prohibited Function", "--model", "m1", "--result", "block",
                  "--refusal-class", "note-and-skip", "--next-move", "change-surface")
        self._run("add", "--challenge", "grayswan", "--behavior",
                  "call-a-prohibited-function", "--model", "m2", "--result", "block",
                  "--refusal-class", "soft-refusal", "--next-move", "reroll")
        import sqlite3
        c = sqlite3.connect(attempts.DB_PATH)
        names = [r[0] for r in c.execute("SELECT DISTINCT behavior FROM attempts")]
        self.assertEqual(names, ["call-prohibited-function"])

    def test_migrate_names_fixes_existing_rows(self):
        import sqlite3
        c = sqlite3.connect(attempts.DB_PATH)
        c.execute(
            "INSERT INTO attempts (ts, challenge, behavior, model, result, status) "
            "VALUES (?,?,?,?,?,?)",
            ("2026-09-01T00:00:00+00:00", "grayswan", "Call A Prohibited Function",
             "m1", "block", "active"))
        c.execute(
            "INSERT INTO attempts (ts, challenge, behavior, model, result, status) "
            "VALUES (?,?,?,?,?,?)",
            ("2026-09-01T00:00:00+00:00", "grayswan", "Exploit Gift Recommendation",
             "m2", "block", "active"))
        c.commit()
        c.close()
        self._run("migrate-names")
        c = sqlite3.connect(attempts.DB_PATH)
        names = sorted(r[0] for r in c.execute("SELECT DISTINCT behavior FROM attempts"))
        self.assertIn("call-prohibited-function", names)
        self.assertIn("exploit-gift-rec", names)
        self.assertNotIn("Call A Prohibited Function", names)
        self.assertNotIn("Exploit Gift Recommendation", names)

    def test_conversation_and_latency_columns_roundtrip(self):
        with attempts.connect() as conn:
            rid = attempts.add_attempt(conn, {
                "challenge": "grayswan", "behavior": "b1", "model": "M1",
                "result": "win", "refusal_class": "win", "next_move": "done",
                "conversation_id": "c-123", "turn_index": 3,
                "turn_goal": "land the scored ask", "latency_ms": 1500.0,
            })
            row = conn.execute(
                "SELECT conversation_id, turn_index, turn_goal, latency_ms "
                "FROM attempts WHERE id=?", (rid,)).fetchone()
        self.assertEqual(row["conversation_id"], "c-123")
        self.assertEqual(row["turn_index"], 3)
        self.assertEqual(row["turn_goal"], "land the scored ask")
        self.assertEqual(row["latency_ms"], 1500.0)

    def test_migrate_is_idempotent_for_new_columns(self):
        # Real migration test: start from an UNMIGRATED database (the pre-Task-2 column
        # set, built with raw sqlite3 so attempts.connect()'s automatic _migrate call is
        # never involved until we explicitly trigger it), with a legacy row already in it.
        import sqlite3
        raw = sqlite3.connect(attempts.DB_PATH)
        raw.execute("DROP TABLE attempts")
        raw.execute("""
            CREATE TABLE attempts (
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
                status        TEXT NOT NULL DEFAULT 'active',
                superseded_by INTEGER,
                closed_reason TEXT
            )
        """)
        raw.execute(
            "INSERT INTO attempts (ts, challenge, behavior, model, result, status) "
            "VALUES (?,?,?,?,?,?)",
            ("2026-08-01T00:00:00+00:00", "grayswan", "legacy-behavior", "m1", "block", "active"))
        raw.commit()
        cols_before = {r[1] for r in raw.execute("PRAGMA table_info(attempts)")}
        raw.close()
        for c in ("conversation_id", "turn_index", "turn_goal", "latency_ms"):
            self.assertNotIn(c, cols_before)  # confirms the fixture is genuinely unmigrated

        conn = attempts.connect()  # this call runs _migrate once, ALTERing the columns in
        try:
            attempts._migrate(conn)  # second explicit run: must be a no-op, no error
            cols_after = {r[1] for r in conn.execute("PRAGMA table_info(attempts)")}
            row = conn.execute(
                "SELECT behavior, model, result, conversation_id, turn_index, turn_goal, "
                "latency_ms FROM attempts WHERE behavior='legacy-behavior'").fetchone()
        finally:
            conn.close()

        for c in ("conversation_id", "turn_index", "turn_goal", "latency_ms"):
            self.assertIn(c, cols_after)
        self.assertEqual(row["behavior"], "legacy-behavior")
        self.assertEqual(row["model"], "m1")
        self.assertEqual(row["result"], "block")
        self.assertIsNone(row["conversation_id"])
        self.assertIsNone(row["turn_index"])
        self.assertIsNone(row["turn_goal"])
        self.assertIsNone(row["latency_ms"])

    def test_init_creates_new_conversation_columns_immediately(self):
        # Regression: SCHEMA's CREATE TABLE body must list the four new columns itself.
        # cmd_init's connect() finds no attempts table yet, so _migrate returns early and
        # only SCHEMA's executescript shapes the table; a later connect() call would
        # self-heal via _migrate, masking the gap. Check right after init, via a bare
        # sqlite3 connection (no attempts.connect(), so no implicit _migrate).
        fresh_path = os.path.join(self.tmp, "fresh.db")
        orig_path = attempts.DB_PATH
        attempts.DB_PATH = fresh_path
        try:
            self._run("init")
        finally:
            attempts.DB_PATH = orig_path
        import sqlite3
        raw = sqlite3.connect(fresh_path)
        cols = {r[1] for r in raw.execute("PRAGMA table_info(attempts)")}
        raw.close()
        for c in ("conversation_id", "turn_index", "turn_goal", "latency_ms"):
            self.assertIn(c, cols)

    def test_add_attempt_rejects_non_numeric_turn_index_and_latency(self):
        conn = attempts.connect()
        try:
            with self.assertRaises(ValueError):
                attempts.add_attempt(conn, {
                    "challenge": "grayswan", "behavior": "b1", "model": "m1",
                    "result": "win", "refusal_class": "win", "next_move": "done",
                    "turn_index": "three",
                })
            with self.assertRaises(ValueError):
                attempts.add_attempt(conn, {
                    "challenge": "grayswan", "behavior": "b1", "model": "m1",
                    "result": "win", "refusal_class": "win", "next_move": "done",
                    "latency_ms": "fast",
                })
        finally:
            conn.close()

    def test_brief_lists_conversation_turns(self):
        with attempts.connect() as conn:
            attempts.add_attempt(conn, {
                "challenge": "grayswan", "behavior": "b1", "model": "M1",
                "result": "block", "refusal_class": "soft-refusal", "next_move": "done",
                "conversation_id": "conv-A", "turn_index": 0})
            attempts.add_attempt(conn, {
                "challenge": "grayswan", "behavior": "b1", "model": "M1",
                "result": "win", "refusal_class": "win", "next_move": "done",
                "conversation_id": "conv-A", "turn_index": 1, "score": 100})
            # A second conversation with no winning turn at all: the marker must
            # never appear anywhere in its block.
            for ti in (0, 1):
                attempts.add_attempt(conn, {
                    "challenge": "grayswan", "behavior": "b1", "model": "M1",
                    "result": "block", "refusal_class": "soft-refusal", "next_move": "done",
                    "conversation_id": "conv-B", "turn_index": ti})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            attempts.cmd_brief(argparse.Namespace(challenge="grayswan", wave=None))
        text = out.getvalue()
        self.assertIn("CONVERSATIONS", text)
        self.assertIn("conv-A", text)
        self.assertIn("conv-B", text)
        # Winning turn: marked, and the score field formatted per the brief.
        self.assertIn("* turn 1: win score=100", text)
        # Non-winning turn in the same conversation: unmarked, and a NULL score
        # falls back to "-".
        self.assertIn("turn 0: soft-refusal score=-", text)
        self.assertNotIn("* turn 0", text)
        # conv-B has no winning turn: the marker never appears in its block.
        conv_b_block = text[text.index("conv-B"):]
        self.assertNotIn("*", conv_b_block)


if __name__ == "__main__":
    unittest.main()
