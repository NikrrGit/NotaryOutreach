"""Local account persistence and connection checks without real email."""

import os
import smtplib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from services.email_delivery import DeliveryError, MailSettings, check_mail_connection, load_mail_settings, save_mail_settings


class MailSetupTests(unittest.TestCase):
    def test_connection_checks_authenticate_and_close_without_sending(self):
        for security, port, factory_name in (("starttls", 587, "SMTP"), ("ssl", 465, "SMTP_SSL")):
            settings = MailSettings("smtp.example.org", port, security, "sender@example.org", "username", "private-password")
            with self.subTest(security=security), patch(f"services.email_delivery.smtplib.{factory_name}") as factory:
                client = factory.return_value
                check_mail_connection(settings)
                client.login.assert_called_once_with("username", "private-password")
                client.close.assert_called_once()
                client.send_message.assert_not_called()
                if security == "starttls":
                    self.assertEqual([call[0] for call in client.method_calls[:4]], ["ehlo", "starttls", "ehlo", "login"])
                else:
                    client.starttls.assert_not_called()
                client.login.side_effect = smtplib.SMTPAuthenticationError(535, b"private-server-response")
                with self.assertRaises(DeliveryError) as caught:
                    check_mail_connection(settings)
                self.assertIn("sign-in failed", str(caught.exception))
                self.assertNotIn("private", str(caught.exception))
                self.assertEqual(client.close.call_count, 2)
                client.send_message.assert_not_called()
                factory.side_effect = TimeoutError("private-connection-details")
                with self.assertRaises(DeliveryError) as caught:
                    check_mail_connection(settings)
                self.assertIn("Could not connect", str(caught.exception))
                self.assertNotIn("private", str(caught.exception))

    def test_failed_account_updates_preserve_existing_settings(self):
        settings = MailSettings("smtp.gmail.com", 587, "starttls", "sender@example.org", "sender@example.org", "secret")
        with TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=True):
            path = Path(directory) / ".env"
            original = "LLM_PROVIDER=openrouter\nSMTP_FROM=previous@example.org\n"
            path.write_text(original)
            with patch.dict("os.environ", {"SMTP_HOST": ""}), self.assertRaisesRegex(ValueError, "override"):
                save_mail_settings(settings, path)
            self.assertEqual(path.read_text(), original)
            with patch("services.email_delivery.os.replace", side_effect=OSError("No space")), self.assertRaises(OSError):
                save_mail_settings(settings, path)
            self.assertEqual(path.read_text(), original)
            self.assertEqual(list(Path(directory).glob(".env.*.tmp")), [])
            link = Path(directory) / "linked.env"
            link.symlink_to(path)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                save_mail_settings(settings, link)
            self.assertEqual(path.read_text(), original)

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
