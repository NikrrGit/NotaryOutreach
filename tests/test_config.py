"""Local settings and secret handling."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from notaryoutreach.config import ConfigurationError, load_config


class ConfigurationTests(unittest.TestCase):
    def test_local_defaults_need_no_credentials(self):
        with TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=True):
            path = Path(directory) / ".env"
            settings = load_config(path)
            self.assertIsNone(settings.groq_api_key)
            self.assertEqual(settings.database_path, Path("data/outreach.db"))
            self.assertEqual(settings.checkpoint_path, Path("runs/checkpoints.sqlite3"))
            with self.assertRaises(ConfigurationError):
                load_config(path, require_api_key=True)
            self.assertFalse(path.exists())

    def test_environment_overrides_file_and_key_is_hidden(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("GROQ_API_KEY=file-key\nDATABASE_PATH=file.db\nCHECKPOINT_PATH=file-checkpoints.db\n")
            with patch.dict("os.environ", {"GROQ_API_KEY": " env-key ", "DATABASE_PATH": "env.db"}, clear=True):
                settings = load_config(path, require_api_key=True)
                self.assertEqual(settings.groq_api_key, "env-key")
                self.assertEqual(settings.database_path, Path("env.db"))
                self.assertEqual(settings.checkpoint_path, Path("file-checkpoints.db"))
                self.assertNotIn("env-key", repr(settings))
            with patch.dict("os.environ", {"GROQ_API_KEY": " "}, clear=True):
                with self.assertRaises(ConfigurationError):
                    load_config(path, require_api_key=True)

    def test_invalid_database_paths_are_rejected(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "missing.env"
            for values in (
                {"DATABASE_PATH": ":memory:"}, {"CHECKPOINT_PATH": "file:test?mode=memory"},
                {"DATABASE_PATH": " "}, {"DATABASE_PATH": "same.db", "CHECKPOINT_PATH": "same.db"},
            ):
                with self.subTest(values=values), patch.dict("os.environ", values, clear=True):
                    with self.assertRaises(ConfigurationError):
                        load_config(path)
