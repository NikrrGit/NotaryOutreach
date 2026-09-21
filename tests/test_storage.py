"""SQLite persistence and replay safety."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from storage.sqlite import SQLiteStorage


class StorageTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "data" / "outreach.db"
        self.storage = SQLiteStorage(self.path)
        self.records = []
        for target in ("notary", "vc"):
            job = {
                "id": target, "target_type": target,
                "location": "Berlin", "target_count": 10,
            }
            job.update(
                {"company_type": "UG"} if target == "notary" else {
                    "startup_description": "Security software for SMEs",
                    "industry": "Cybersecurity", "funding_stage": "Seed",
                }
            )
            self.records.extend([
                ("jobs", job),
                ("candidates", {
                    "id": f"candidate-{target}", "job_id": target,
                    "target_type": target, "name": "Müller & Partners",
                    "source_url": "https://example.org",
                    "metadata_json": {"language": "de", "tags": ["Berlin"]},
                }),
                ("verifications", {
                    "id": f"verification-{target}", "candidate_id": f"candidate-{target}",
                    "eligible": True, "confidence": 0.9, "reason": "Evidence found",
                    "evidence": "Relevant services", "source_url": "https://example.org/services",
                }),
                ("drafts", {
                    "id": f"draft-{target}", "candidate_id": f"candidate-{target}",
                    "subject": "Anfrage", "body": "Können wir einen Termin vereinbaren?",
                }),
                ("evaluations", {
                    "id": f"evaluation-{target}", "draft_id": f"draft-{target}",
                    "passed": True, "score": 0.9, "issues_json": [],
                }),
                ("reviews", {
                    "id": f"review-{target}", "draft_id": f"draft-{target}",
                    "decision": "approved", "final_subject": "Anfrage",
                    "final_body": "Können wir einen Termin vereinbaren?",
                }),
            ])
        for table, record in self.records:
            self.storage.save_record(table, record)
