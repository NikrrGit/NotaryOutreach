"""Streamlit execution and recovery checks without network calls."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from streamlit.testing.v1 import AppTest

from agents.discovery import Candidate
from agents.email_writer import EmailDraft
from agents.verification import VerificationResult
from graph.state import EvaluationResult
from services.outreach_service import OutreachService
from storage.sqlite import SQLiteStorage


class UITests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name)
        self.storage = SQLiteStorage(path / "outreach.db")
        self.discovery, verifier, writer, self.evaluator = Mock(), Mock(), Mock(), Mock()
        self.discovery.discover.side_effect = lambda **settings: [Candidate(
            name="Example", city="Berlin", target_type=settings["target_type"], source_url="https://example.org",
        )]
        verifier.verify.side_effect = lambda candidate, **settings: VerificationResult(
            candidate=candidate, **settings, status="supported", confidence=0.9,
            reasoning="Evidence found", evidence_quote="Relevant services", source_url="https://example.org",
        )
        writer.write_verified.return_value = EmailDraft(subject="Enquiry", body="Could we arrange a meeting?")
        self.evaluator.side_effect = lambda draft, verification: EvaluationResult(
            draft_id=draft.draft_id, passed=True, score=0.9, reasoning="Supported",
        )
        self.service = OutreachService(self.storage, checkpoint_path=path / "checkpoints.sqlite3",
                                       discovery_agent=self.discovery, verification_agent=verifier,
                                       email_writer=writer, evaluator=self.evaluator)
        factory = patch("services.outreach_service.OutreachService", return_value=self.service)
        factory.start()
        self.addCleanup(factory.stop)
        environment = patch.dict("os.environ", {"DATABASE_PATH": str(self.storage.path)})
        environment.start()
        self.addCleanup(environment.stop)
        self.app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "src/ui/app.py"), default_timeout=15).run()
        self.assertFalse(self.app.exception)

    def test_new_clone_can_connect_gmail_and_send_a_reviewed_draft(self):
        from services.email_delivery import load_mail_settings

        env = self.storage.path.parent / ".env"
        env.write_text("LLM_PROVIDER=openrouter\nOPENROUTER_API_KEY=unchanged\n")
        self.service.env_file = env
        job = self.service.create_job(target_type="notary", location="Berlin", company_type="UG")
        candidate = self.storage.save_record("candidates", dict(job_id=job, target_type="notary", name="Office",
            city="Berlin", email="office@example.org", source_url="https://example.org"))
        template = f"template-{candidate}"
        app = self.app
        app.session_state["selected_job"] = job
        with patch.dict("os.environ", {"DATABASE_PATH": str(self.storage.path)}, clear=True), patch(
            "services.email_delivery.smtplib.SMTP",
        ) as factory:
            client = factory.return_value
            client.send_message.return_value = {}
            app.run()
            self.assertTrue(app.button(key=f"send-{template}").disabled)
            app.text_input(key="mail_sender").set_value("sender@gmail.com")
            app.text_input(key="mail_password").set_value("abcd efgh ijkl mnop")
            next(button for button in app.button if button.label == "Test and save email account").click().run()
            self.assertFalse(app.exception)
            client.login.assert_called_once_with("sender@gmail.com", "abcdefghijklmnop")
            client.send_message.assert_not_called()
            client.close.assert_called_once()
            self.assertEqual(app.text_input(key="mail_password").value, "")
            self.assertIn("OPENROUTER_API_KEY=unchanged", env.read_text())
            self.assertEqual(load_mail_settings(env).sender, "sender@gmail.com")
            self.assertTrue(app.button(key=f"send-{template}").disabled)
            body = app.text_area(key=f"body-{template}").value.replace("[Ihr Name]", "Test Sender")
            app.text_area(key=f"body-{template}").set_value(body).run()
            next(item for item in app.checkbox if item.label == "I reviewed this recipient and message").check().run()
            self.assertFalse(app.button(key=f"send-{template}").disabled)
            app.button(key=f"send-{template}").click().run()
            self.assertFalse(app.exception)
            client.send_message.assert_called_once()
            message = client.send_message.call_args.args[0]
            self.assertEqual(message["From"], "sender@gmail.com")
            self.assertEqual(message["To"], "office@example.org")
            self.assertEqual(message.get_content().strip(), body)
            self.assertEqual(self.service.load_results(job)["deliveries"][0]["status"], "sent")
            app.run()
            self.assertEqual(client.login.call_count, 2)
            client.send_message.assert_called_once()

    def test_missing_provider_selection_can_be_fixed_and_saved_search_restarted(self):
        env = self.storage.path.parent / ".env"
        env.write_text("OPENROUTER_API_KEY=private-test-key\n")
        self.service.env_file = env
        self.service.agents = (None, *self.service.agents[1:])
        provider = Mock()
        provider.search.return_value = '{"candidates":[{"name":"Example","city":"Berlin","source_url":"https://example.org"}]}'
        with patch.dict("os.environ", {"DATABASE_PATH": str(self.storage.path)}, clear=True), patch(
            "providers.clients.create_provider", return_value=provider,
        ) as factory:
            next(widget for widget in self.app.text_input if widget.label == "Location").set_value("Berlin")
            next(button for button in self.app.button if button.label == "Start search").click().run()
            self.assertFalse(self.app.exception)
            message = self.app.error[0].value
            self.assertIn("GROQ_API_KEY", message)
            self.assertIn("LLM_PROVIDER", message)
            self.assertNotIn("invalid result", message)
            self.assertNotIn("private-test-key", message)
            factory.assert_not_called()
            job = self.service.list_jobs()[0]
            self.assertEqual(job["status"], "failed")
            self.assertEqual(self.app.button(key=f"run-{job['id']}").label, "Start saved search")
            env.write_text("LLM_PROVIDER=openrouter\nOPENROUTER_API_KEY=private-test-key\n")
            self.app.button(key=f"run-{job['id']}").click().run()
            self.assertFalse(self.app.exception)
            self.assertFalse(self.app.error)
            self.assertEqual(factory.call_args.args[0].name, "openrouter")
            self.assertEqual(self.service.load_job(job["id"])["status"], "ready_for_review")
            self.assertEqual(len(self.service.list_jobs()), 1)
            self.assertEqual(len(self.app.dataframe[0].value), 1)
            self.assertTrue(any(widget.label == "Email" and not widget.disabled for widget in self.app.text_area))
            provider.close.assert_called_once()

    def test_both_search_modes_execute_only_on_submit(self):
        app = self.app
        for target in ("Notary", "Venture Capital"):
            with self.subTest(target=target):
                app.radio[0].set_value(target).run()
                next(widget for widget in app.text_input if widget.label.startswith("Location")).set_value("Berlin")
                if target == "Venture Capital":
                    next(widget for widget in app.text_area if widget.label == "Startup description").set_value("Security software")
                    next(widget for widget in app.text_input if widget.label == "Industry").set_value("Cybersecurity")
                next(button for button in app.button if button.label == "Start search").click().run()
                self.assertFalse(app.exception)
                job = self.service.list_jobs()[0]
                self.assertEqual(job["status"], "ready_for_review")
                self.assertEqual(job["target_type"], "notary" if target == "Notary" else "vc")
                calls = self.discovery.discover.call_count
                app.run()
                self.assertEqual(self.discovery.discover.call_count, calls)
        self.assertEqual(len(self.service.list_jobs()), 2)
        self.assertEqual(self.discovery.discover.call_count, 2)

    def test_saved_search_can_start_and_failed_execution_can_resume(self):
        app = self.app
        next(button for button in app.button if button.label == "Start search").click().run()
        self.assertTrue(app.error)
        self.assertEqual(self.service.list_jobs(), [])
        next(widget for widget in app.text_input if widget.label == "Location").set_value("Berlin")
        next(button for button in app.button if button.label == "Save search").click().run()
        job = self.service.list_jobs()[0]
        self.discovery.discover.assert_not_called()
        with patch.object(self.storage, "save_record", side_effect=OSError("private diagnostic")):
            app.button(key=f"run-{job['id']}").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.error)
        self.assertNotIn("private diagnostic", app.error[0].value)
        self.assertEqual(app.button(key=f"run-{job['id']}").label, "Resume search")
        app.button(key=f"run-{job['id']}").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(self.service.load_job(job["id"])["status"], "ready_for_review")
        self.discovery.discover.assert_called_once()
        self.assertEqual(len(self.storage.list_records("candidates")), 1)

    def test_edited_draft_requires_re_evaluation_before_approval(self):
        app = self.app
        next(widget for widget in app.text_input if widget.label == "Location").set_value("Berlin")
        next(button for button in app.button if button.label == "Start search").click().run()
        job = self.service.list_jobs()[0]
        original = self.service.load_results(job["id"])["drafts"][0]
        app.text_area(key=f"body-{original['id']}").set_value("Revised outreach").run()
        self.assertTrue(app.button(key=f"evaluate-{original['id']}").disabled)
        self.assertTrue(app.button(key=f"approve-{original['id']}").disabled)
        app.button(key=f"edit-{original['id']}").click().run()
        edited = self.service.load_results(job["id"])["drafts"][-1]
        self.assertNotEqual(edited["id"], original["id"])
        self.assertTrue(app.button(key=f"approve-{edited['id']}").disabled)
        app.button(key=f"evaluate-{edited['id']}").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.button(key=f"approve-{edited['id']}").disabled)
        app.button(key=f"approve-{edited['id']}").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.button(key=f"approve-{edited['id']}").disabled)
        reviews = self.service.load_results(job["id"])["reviews"]
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["draft_id"], edited["id"])
        self.assertEqual(reviews[0]["final_body"], "Revised outreach")
        calls = self.evaluator.call_count
        app.run()
        self.assertEqual(self.evaluator.call_count, calls)
        self.assertEqual(self.service.load_results(job["id"])["reviews"], reviews)
        self.evaluator.side_effect = TimeoutError("private provider response")
        app.button(key=f"evaluate-{edited['id']}").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.error)
        self.assertNotIn("private provider response", app.error[0].value)
        self.assertEqual(len(self.service.load_results(job["id"])["evaluations"]), 2)

    def test_failed_search_shows_error_template_and_retry_creates_results(self):
        app = self.app
        working = self.discovery.discover.side_effect
        failure = RuntimeError("private provider response")
        failure.status_code = 404
        self.discovery.discover.side_effect = failure
        next(widget for widget in app.text_input if widget.label == "Location").set_value("Stuttgart")
        next(button for button in app.button if button.label == "Start search").click().run()
        failed = self.service.list_jobs()[0]
        self.assertFalse(app.exception)
        self.assertTrue(any("model is unavailable" in item.value for item in app.error))
        self.assertFalse(any("private provider" in item.value for item in app.error))
        self.assertTrue(any("UG" in item.value for item in app.text_area))
        self.discovery.discover.side_effect = working
        app.button(key=f"retry-{failed['id']}").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.dataframe[0].value), 1)
        self.assertEqual(len(self.service.list_jobs()), 2)
        self.assertNotEqual(app.session_state["selected_job"], failed["id"])

    def test_mail_setup_and_unknown_suitability_do_not_confuse_send_controls(self):
        job = self.service.create_job(target_type="notary", location="Berlin", company_type="UG")
        candidate = self.storage.save_record("candidates", dict(job_id=job, target_type="notary", name="Office",
            city="Berlin", email="office@example.org", source_url="https://example.org"))
        self.storage.save_record("verifications", dict(candidate_id=candidate, eligible=None, confidence=0,
            reason="The website did not specify company formation services."))
        self.service.env_file = self.storage.path.parent / "missing.env"
        template = f"template-{candidate}"
        app = self.app
        app.session_state["selected_job"] = job
        with patch.dict("os.environ", {"SMTP_HOST": "", "SMTP_FROM": ""}), patch("services.email_delivery.smtplib.SMTP") as smtp:
            app.run()
            self.assertTrue(any("SMTP_HOST" in item.value for item in app.warning))
            self.assertTrue(any("did not confirm a match" in item.value for item in app.info))
            subject, body = "UG & GmbH?", "Guten Tag,\nGründung & Termin + Rückfrage"
            app.text_input(key=f"subject-{template}").set_value(subject)
            app.text_area(key=f"body-{template}").set_value(body).run()
            next(item for item in app.checkbox if item.label == "I reviewed this recipient and message").check().run()
            self.assertTrue(app.button(key=f"send-{template}").disabled)
            link = next(item for item in app.get("link_button") if item.proto.label == "Open in email app")
            url = urlsplit(link.proto.url)
            self.assertEqual((url.scheme, url.path), ("mailto", "office@example.org"))
            self.assertEqual(parse_qs(url.query), {"subject": [subject], "body": [body]})
            app.text_input(key=f"recipient-{template}").set_value("invalid-address").run()
            self.assertFalse(any(item.proto.label == "Open in email app" for item in app.get("link_button")))
            app.text_input(key=f"recipient-{template}").set_value("office@example.org").run()
            with patch.dict("os.environ", {"SMTP_HOST": "smtp.gmail.com", "SMTP_FROM": "sender@example.org",
                                          "SMTP_USERNAME": "sender@example.org", "SMTP_PASSWORD": "test-password"}):
                app.run()
                next(item for item in app.checkbox if item.label == "I reviewed this recipient and message").check().run()
                self.assertFalse(app.button(key=f"send-{template}").disabled)
            self.assertFalse(app.exception)
            self.assertEqual(self.service.load_results(job)["deliveries"], [])
            smtp.assert_not_called()

    def test_default_draft_can_be_edited_and_sent_without_ai_generation(self):
        job = self.service.create_job(target_type="vc", location="Berlin", startup_description="Security software",
                                      industry="Cybersecurity", funding_stage="Seed")
        candidate = self.storage.save_record("candidates", dict(job_id=job, target_type="vc", name="Example Capital",
                            city="Berlin", email="team@example.org", website="https://example.org", source_url="https://example.org"))
        app = self.app
        app.session_state["selected_job"] = job
        settings = dict(SMTP_HOST="smtp.example.org", SMTP_FROM="sender@example.org", SMTP_USERNAME="user", SMTP_PASSWORD="test")
        with patch.dict("os.environ", settings), patch("services.email_delivery.smtplib.SMTP") as factory:
            client = factory.return_value
            client.send_message.return_value = {}
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(app.dataframe[0].value.iloc[0]["name"], "Example Capital")
            template = f"template-{candidate}"
            original = app.text_area(key=f"body-{template}").value
            self.assertIn("Security software", original)
            self.assertTrue(app.button(key=f"send-{template}").disabled)
            edited = original.replace("[Ihr Name]", "Test Sender")
            app.text_area(key=f"body-{template}").set_value(edited).run()
            next(item for item in app.checkbox if item.label == "I reviewed this recipient and message").check().run()
            self.assertFalse(app.button(key=f"send-{template}").disabled)
            app.button(key=f"send-{template}").click().run()
            self.assertFalse(app.exception)
            records = self.service.load_results(job)
            self.assertEqual(records["drafts"][0]["body"], edited)
            self.assertEqual(records["deliveries"][0]["status"], "sent")
            self.assertEqual(client.send_message.call_args.args[0]["To"], "team@example.org")
            saved = records["drafts"][0]["id"]
            self.assertTrue(app.button(key=f"send-{saved}").disabled)
            app.run()
            client.send_message.assert_called_once()
            self.discovery.discover.assert_not_called()
            self.evaluator.assert_not_called()
