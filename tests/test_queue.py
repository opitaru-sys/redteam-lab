import argparse
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "queuemod",
    os.path.join(os.path.dirname(__file__), "..", "learn", "queue.py"),
)
queuemod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queuemod)


class TurnStructuredEntry(unittest.TestCase):
    """Interface contract: an entry may carry a turn-structured plan."""

    def test_entry_preserves_turns_list(self):
        db = {
            "meta": {"target": None, "event_url": None, "updated": "2026-09-18"},
            "entries": [],
        }
        entry = {
            "id": "e1",
            "behavior": "b1",
            "turns": [
                {"turn_index": 0, "goal": "establish context"},
                {"turn_index": 1, "goal": "land the scored ask"},
            ],
        }
        db["entries"].append({**entry, "status": "pending", "fired_batches": [], "notes": ""})
        self.assertEqual(len(db["entries"][0]["turns"]), 2)
        self.assertEqual(db["entries"][0]["turns"][1]["turn_index"], 1)


class CmdAddTurns(unittest.TestCase):
    """cmd_add must carry the turns field through untouched (default to [])."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.queue_path = os.path.join(self.tmp, "firing-queue.json")
        self._orig_queue = queuemod.QUEUE
        queuemod.QUEUE = self.queue_path

    def tearDown(self):
        queuemod.QUEUE = self._orig_queue

    def _add(self, entries):
        src = os.path.join(self.tmp, "incoming.json")
        with open(src, "w", encoding="utf-8") as f:
            json.dump(entries, f)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            queuemod.cmd_add(argparse.Namespace(file=src))
        with open(self.queue_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_cmd_add_normalizes_missing_turns(self):
        db = self._add([{"id": "e1", "behavior": "b1"}])
        self.assertEqual(db["entries"][0]["turns"], [])

    def test_cmd_add_preserves_supplied_turns(self):
        turns = [
            {"turn_index": 0, "goal": "establish context"},
            {"turn_index": 1, "goal": "land the scored ask"},
        ]
        db = self._add([{"id": "e2", "behavior": "b2", "turns": turns}])
        self.assertEqual(db["entries"][0]["turns"], turns)


if __name__ == "__main__":
    unittest.main()
