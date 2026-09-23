"""Local job and draft review behavior."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from services.outreach_service import OutreachService
from storage.sqlite import SQLiteStorage


class OutreachServiceTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "outreach.db"
        self.storage = SQLiteStorage(self.path)
        self.service = OutreachService(self.storage)
        self.settings = {
            "notary": dict(target_type="notary", location=" Berlin ", company_type="UG"),
            "vc": dict(target_type="vc", location=" Germany ",
                       startup_description="Security software", industry="Cybersecurity",
                       funding_stage="Seed"),
        }
        for target, settings in self.settings.items():
            self.service.create_job(**settings, job_id=target)
            self.storage.save_record("candidates", {
                "id": f"candidate-{target}", "job_id": target, "target_type": target,
                "name": "Example office", "source_url": "https://example.org",
            })
            self.storage.save_record("verifications", {
                "id": f"verification-{target}", "candidate_id": f"candidate-{target}",
                "eligible": True, "confidence": 0.9, "reason": "Evidence found",
                "evidence": "Relevant services", "source_url": "https://example.org/services",
            })
            self.storage.save_record("drafts", {
                "id": f"draft-{target}", "candidate_id": f"candidate-{target}",
                "subject": "Enquiry", "body": "Could we arrange a meeting?",
            })
