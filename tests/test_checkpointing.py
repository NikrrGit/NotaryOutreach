"""Checkpoint configuration and connection lifetime checks (no agent calls)."""

from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from graph.checkpointing import checkpoint_config, open_checkpointer


class CheckpointingTests(unittest.TestCase):
    def test_thread_id_is_stable_and_required(self):
        self.assertEqual(checkpoint_config("job-1"), {"configurable": {"thread_id": "job-1"}})
        for value in (None, 12, "", "  "):
            with self.subTest(value=value), self.assertRaises(ValueError):
                checkpoint_config(value)

    def test_nonpersistent_paths_are_rejected(self):
        for path in ("", " ", ":memory:", "file:test?mode=memory&cache=shared"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                with open_checkpointer(path):
                    self.fail("Nonpersistent path was accepted")

    def test_creates_parent_and_closes_connection_on_exception(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "checkpoints.sqlite3"
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                with open_checkpointer(path) as saver:
                    self.assertTrue(path.is_file())
                    self.assertIsNone(saver.get_tuple(checkpoint_config("new-job")))
                    raise RuntimeError("interrupted")
            with self.assertRaises(sqlite3.ProgrammingError):
                saver.conn.execute("SELECT 1")
            with open_checkpointer(path) as reopened:
                self.assertIsNone(reopened.get_tuple(checkpoint_config("new-job")))
