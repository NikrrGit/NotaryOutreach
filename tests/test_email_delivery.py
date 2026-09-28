"""Delivery, migration, and duplicate prevention without sending real email."""

from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import smtplib
import sqlite3
import unittest
from unittest.mock import Mock, patch

from services.email_delivery import DeliveryError, MailSettings, default_email, deliver, load_mail_settings
from services.outreach_service import OutreachService
from storage.sqlite import SQLiteStorage


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.service = OutreachService(SQLiteStorage(self.path / "data.db"), env_file=self.path / "missing.env",
                                       checkpoint_path=self.path / "checkpoints.db")
        self.job = self.service.create_job(target_type="notary", location="Berlin", company_type="UG")
        self.candidate = self.service.storage.save_record("candidates", dict(job_id=self.job, target_type="notary", name="Office",
                                                                            city="Berlin", source_url="https://example.org"))
        self.draft = self.service.create_draft(self.job, self.candidate, subject="Appointment", body="Could we arrange a meeting?")
        self.settings = MailSettings("smtp.example.org", 587, "starttls", "sender@example.org", "user", "private-password")
        config = patch("services.email_delivery.load_mail_settings", return_value=self.settings)
        config.start()
        self.addCleanup(config.stop)

    def test_smtp_encrypts_authenticates_and_submits_exact_saved_text_once(self):
        with patch("services.email_delivery.smtplib.SMTP") as factory:
            client = factory.return_value
            client.send_message.return_value = {}
            result = self.service.send_draft(self.job, self.draft, recipient="recipient@example.org", confirmed=True)
            self.assertEqual(result["status"], "sent")
            client.starttls.assert_called_once()
            client.login.assert_called_once_with("user", "private-password")
            message = client.send_message.call_args.args[0]
            self.assertEqual(message["Subject"], "Appointment")
            self.assertEqual(message["To"], "recipient@example.org")
            self.assertEqual(message.get_content().strip(), "Could we arrange a meeting?")
            reopened = OutreachService(SQLiteStorage(self.service.storage.path), env_file=self.path / "missing.env")
            with self.assertRaisesRegex(ValueError, "already submitted"):
                reopened.send_draft(self.job, self.draft, recipient="recipient@example.org", confirmed=True)
            client.send_message.assert_called_once()
            client.close.assert_called_once()

    def test_auth_failure_can_retry_but_ambiguous_submission_cannot(self):
        with patch("services.email_delivery.smtplib.SMTP") as factory:
            client = factory.return_value
            client.login.side_effect = smtplib.SMTPAuthenticationError(535, b"private credentials")
            with self.assertRaises(DeliveryError):
                self.service.send_draft(self.job, self.draft, recipient="recipient@example.org", confirmed=True)
            client.send_message.assert_not_called()
            record = self.service.load_results(self.job)["deliveries"][0]
            self.assertEqual(record["status"], "failed")
            self.assertNotIn("private", record["error"])
            client.login.side_effect = None
            client.send_message.side_effect = TimeoutError("private server")
            with self.assertRaises(DeliveryError):
                self.service.send_draft(self.job, self.draft, recipient="recipient@example.org", confirmed=True)
            self.assertEqual(self.service.load_results(self.job)["deliveries"][0]["status"], "unknown")
            with self.assertRaises(ValueError):
                self.service.send_draft(self.job, self.draft, recipient="recipient@example.org", confirmed=True)
            client.send_message.assert_called_once()

    def test_confirmation_scope_recipient_and_template_validation_precede_sending(self):
        with patch("services.email_delivery.deliver") as send:
            for recipient, confirmed in (("recipient@example.org", False), ("a@example.org\r\nBcc: b@example.org", True),
                                          ("a@example.org,b@example.org", True)):
                with self.assertRaises(ValueError):
                    self.service.send_draft(self.job, self.draft, recipient=recipient, confirmed=confirmed)
            other = self.service.create_job(target_type="notary", location="Berlin", company_type="UG")
            with self.assertRaises(KeyError):
                self.service.send_draft(other, self.draft, recipient="a@example.org", confirmed=True)
            subject, body = default_email(self.service.load_job(self.job), {})
            template = self.service.create_draft(self.job, self.candidate, subject=subject, body=body)
            with self.assertRaisesRegex(ValueError, "Replace"):
                self.service.send_draft(self.job, template, recipient="a@example.org", confirmed=True)
            send.assert_not_called()
            self.assertEqual(self.service.load_results(self.job)["deliveries"], [])

    def test_ssl_and_recipient_rejection(self):
        settings = MailSettings("smtp.example.org", 465, "ssl", "sender@example.org")
        with patch("services.email_delivery.smtplib.SMTP_SSL") as factory:
            factory.return_value.send_message.return_value = {"a@example.org": (550, b"rejected")}
            with self.assertRaises(DeliveryError) as caught:
                deliver(settings, recipient="a@example.org", subject="Subject", body="Body", message_id="<test@example.org>")
            self.assertFalse(caught.exception.uncertain)
            factory.return_value.starttls.assert_not_called()

    def test_claim_is_atomic_across_storage_instances(self):
        record = dict(id="first", draft_id=self.draft, recipient="a@example.org", sender="b@example.org", message_id="<first@example.org>")
        self.assertTrue(self.service.storage.claim_delivery(record))
        other = SQLiteStorage(self.service.storage.path)
        self.assertFalse(other.claim_delivery({**record, "id": "second"}))
        self.assertEqual(len(other.list_records("deliveries")), 1)

    def test_version_one_migration_preserves_reviews(self):
        path = self.path / "old.db"
        migration = Path(__file__).resolve().parents[1] / "src/db/migrations/001_initial.sql"
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(migration.read_text())
            db.execute("INSERT INTO jobs (id,target_type,location,target_count,company_type) VALUES ('j','notary','Berlin',1,'UG')")
            db.execute("INSERT INTO candidates (id,job_id,target_type,name,source_url) VALUES ('c','j','notary','Office','https://example.org')")
            db.execute("INSERT INTO drafts (id,candidate_id,subject,body) VALUES ('d','c','Subject','Body')")
            db.execute("INSERT INTO reviews (id,draft_id,decision,final_subject,final_body) VALUES ('r','d','approved','Subject','Body')")
        storage = SQLiteStorage(path)
        self.assertEqual(storage.get_record("reviews", "r")["decision"], "approved")
        self.assertEqual(storage.list_records("deliveries"), [])
        with closing(sqlite3.connect(path)) as db, db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)


class MailConfigTests(unittest.TestCase):
    def test_configuration_and_secret_redaction(self):
        values = dict(SMTP_HOST="smtp.example.org", SMTP_FROM="a@example.org", SMTP_USERNAME="user", SMTP_PASSWORD="secret")
        with patch.dict("os.environ", values, clear=True):
            settings = load_mail_settings("/nonexistent.env")
            self.assertEqual(settings.port, 587)
            self.assertNotIn("secret", repr(settings))
        for extra in ({"SMTP_HOST": ""}, {"SMTP_SECURITY": "none"}, {"SMTP_PORT": "bad"}, {"SMTP_USERNAME": ""}):
            with patch.dict("os.environ", {**values, **extra}, clear=True), self.assertRaises(ValueError):
                load_mail_settings("/nonexistent.env")
