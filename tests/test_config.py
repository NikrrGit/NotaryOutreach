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
