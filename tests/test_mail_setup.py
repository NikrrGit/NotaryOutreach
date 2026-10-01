"""Local account persistence and connection checks without real email."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from services.email_delivery import MailSettings, load_mail_settings, save_mail_settings


class MailSetupTests(unittest.TestCase):
    def test_saved_account_round_trips_and_preserves_other_configuration(self):
        with TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=True):
            path = Path(directory) / ".env"
            original = "# Keep these settings\nLLM_PROVIDER=openrouter\nOPENROUTER_API_KEY=unchanged\n"
            path.write_text(original)
            settings = MailSettings("smtp.example.org", 465, "ssl", "sender@example.org",
                                    "sender@example.org", "literal'${SMTP_FROM}\\password")
            save_mail_settings(settings, path)
            self.assertEqual(load_mail_settings(path), settings)
            self.assertTrue(path.read_text().startswith(original))
            self.assertNotIn(settings.password, repr(settings))
            if os.name == "posix":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            replacement = MailSettings("smtp.gmail.com", 587, "starttls", "new@example.org", "new@example.org", "new-secret")
            save_mail_settings(replacement, path)
            self.assertEqual(load_mail_settings(path), replacement)
            self.assertEqual(path.read_text().count("SMTP_FROM="), 1)
            self.assertEqual(list(Path(directory).glob(".env.*.tmp")), [])
            fresh = Path(directory) / "new" / ".env"
            save_mail_settings(settings, fresh)
            self.assertEqual(load_mail_settings(fresh), settings)
