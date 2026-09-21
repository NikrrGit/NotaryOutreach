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

    def test_records_and_job_progress_survive_reopening(self):
        self.storage.update_job("notary", status="ready_for_review")
        before = {
            (table, record["id"]): self.storage.get_record(table, record["id"])
            for table, record in self.records
        }
        reopened = SQLiteStorage(self.path)
        self.assertTrue(self.path.is_file())
        for table, record in self.records:
            with self.subTest(table=table, record_id=record["id"]):
                stored = reopened.get_record(table, record["id"])
                self.assertEqual(stored, before[table, record["id"]])
                for field, expected in record.items():
                    self.assertEqual(stored[field], expected)
                target = record["id"].split("-")[-1]
                self.assertEqual(reopened.list_records(table, job_id=target), [stored])
        self.assertEqual(reopened.get_record("jobs", "notary")["status"], "ready_for_review")
        self.assertIs(reopened.get_record("verifications", "verification-vc")["eligible"], True)
        self.assertIs(reopened.get_record("evaluations", "evaluation-vc")["passed"], True)

    def test_replaying_stable_ids_does_not_duplicate_records(self):
        before = {table: self.storage.list_records(table) for table, _ in self.records}
        reopened = SQLiteStorage(self.path)
        for _ in range(2):
            for table, record in self.records:
                with self.subTest(table=table, record_id=record["id"]):
                    self.assertEqual(reopened.save_record(table, record), record["id"])
        for table, expected in before.items():
            with self.subTest(table=table):
                self.assertEqual(reopened.list_records(table), expected)
