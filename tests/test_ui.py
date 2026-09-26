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
