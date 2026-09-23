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

    def test_jobs_and_results_survive_reopening_without_duplicates(self):
        for target, settings in self.settings.items():
            with self.subTest(target=target):
                self.storage.update_job(target, status="running")
                self.assertEqual(self.service.create_job(**settings, job_id=target), target)
                job = self.service.load_job(target)
                self.assertEqual(job["status"], "running")
                self.assertEqual(job["location"], settings["location"].strip())
                for field, value in settings.items():
                    self.assertEqual(job[field], value.strip())
                results = self.service.load_results(target)
                for table, prefix in (("candidates", "candidate"), ("verifications", "verification"), ("drafts", "draft")):
                    self.assertEqual([row["id"] for row in results[table]], [f"{prefix}-{target}"])
                self.assertEqual(results["verifications"][0]["evidence"], "Relevant services")
                self.assertEqual(results["evaluations"], [])
                self.assertEqual(results["reviews"], [])
                reopened = OutreachService(SQLiteStorage(self.path))
                self.assertEqual(reopened.load_results(target), results)
        self.assertEqual([job["id"] for job in self.service.list_jobs()], ["vc", "notary"])
        generated_id = self.service.create_job(**self.settings["notary"])
        self.assertNotIn(generated_id, self.settings)
        self.assertEqual(self.service.load_job(generated_id)["status"], "pending")

    def test_invalid_search_settings_leave_jobs_unchanged(self):
        before = self.service.list_jobs()
        cases = [
            ("notary", {"target_type": "other"}),
            ("notary", {"location": " "}),
            ("notary", {"location": None}),
            ("notary", {"target_count": True}),
            ("notary", {"target_count": 0}),
            ("notary", {"target_count": 101}),
            ("notary", {"company_type": "LLC"}),
            ("notary", {"company_type": None}),
            ("notary", {"industry": "Security"}),
            ("vc", {"company_type": "UG"}),
            ("vc", {"startup_description": " "}),
            ("vc", {"industry": None}),
            ("vc", {"funding_stage": None}),
        ]
        for target, changes in cases:
            with self.subTest(target=target, changes=changes), self.assertRaises(ValueError):
                self.service.create_job(**{**self.settings[target], **changes})
        with self.assertRaises(ValueError):
            self.service.create_job(**{**self.settings["notary"], "location": "Munich"}, job_id="notary")
        self.assertEqual(self.service.list_jobs(), before)
