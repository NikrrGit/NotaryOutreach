"""Streamlit execution and recovery checks without network calls."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

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
