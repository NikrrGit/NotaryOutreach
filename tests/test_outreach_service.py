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

    def test_reviews_require_latest_passing_evaluation_and_preserve_history(self):
        for target in self.settings:
            with self.subTest(target=target):
                draft_id = f"draft-{target}"
                with self.assertRaises(ValueError):
                    self.service.approve_draft(target, draft_id)
                self.assertEqual(self.service.load_results(target)["reviews"], [])
                self.service.reject_draft(target, draft_id, review_id=f"rejected-{target}")
                self.storage.save_record("evaluations", {
                    "id": f"pass-{target}", "draft_id": draft_id, "passed": True,
                })
                for _ in range(2):
                    self.assertEqual(
                        self.service.approve_draft(target, draft_id, review_id=f"approved-{target}"),
                        f"approved-{target}",
                    )
                before = self.service.load_results(target)["reviews"]
                self.assertEqual([review["decision"] for review in before], ["rejected", "approved"])
                for review in before:
                    self.assertEqual(review["draft_id"], draft_id)
                    self.assertEqual(review["final_subject"], "Enquiry")
                    self.assertEqual(review["final_body"], "Could we arrange a meeting?")
                self.storage.save_record("evaluations", {
                    "id": f"fail-{target}", "draft_id": draft_id, "passed": False,
                })
                with self.assertRaises(ValueError):
                    self.service.approve_draft(target, draft_id)
                with self.assertRaises(ValueError):
                    self.service.reject_draft(target, draft_id, review_id=f"approved-{target}")
                reopened = OutreachService(SQLiteStorage(self.path))
                self.assertEqual(reopened.load_results(target)["reviews"], before)

    def test_edits_create_unapproved_versions_without_changing_originals(self):
        for target in self.settings:
            with self.subTest(target=target):
                draft_id = f"draft-{target}"
                self.storage.save_record("evaluations", {
                    "draft_id": draft_id, "passed": True,
                })
                self.service.approve_draft(target, draft_id)
                before = self.service.load_results(target)
                for _ in range(2):
                    edited_id = self.service.edit_email(
                        target, draft_id, subject=" Updated enquiry ", body=" Updated body ",
                        new_draft_id=f"edited-{target}",
                    )
                    self.assertEqual(edited_id, f"edited-{target}")
                after = self.service.load_results(target)
                self.assertEqual(len(after["drafts"]), 2)
                self.assertEqual(after["drafts"][0], before["drafts"][0])
                self.assertEqual(after["drafts"][1]["subject"], "Updated enquiry")
                self.assertEqual(after["drafts"][1]["body"], "Updated body")
                self.assertEqual(after["evaluations"], before["evaluations"])
                self.assertEqual(after["reviews"], before["reviews"])
                with self.assertRaises(ValueError):
                    self.service.approve_draft(target, edited_id)
                for changes in ({"subject": " "}, {"body": " "}, {"new_draft_id": draft_id}):
                    with self.subTest(changes=changes), self.assertRaises(ValueError):
                        self.service.edit_email(target, draft_id, **{
                            **dict(subject="Updated enquiry", body="Updated body"), **changes,
                        })
                reopened = OutreachService(SQLiteStorage(self.path))
                self.assertEqual(reopened.load_results(target), after)

    def test_missing_jobs_and_cross_job_drafts_cannot_be_reviewed_or_edited(self):
        before = {target: self.service.load_results(target) for target in self.settings}
        for load in (self.service.load_job, self.service.load_results):
            with self.subTest(operation=load.__name__), self.assertRaises(KeyError):
                load("missing")
        for job_id, draft_id in (
            ("notary", "draft-vc"), ("vc", "draft-notary"),
            ("notary", "missing"), ("missing", "draft-notary"),
        ):
            for review in (self.service.approve_draft, self.service.reject_draft):
                with self.subTest(job=job_id, draft=draft_id, operation=review.__name__):
                    with self.assertRaises(KeyError):
                        review(job_id, draft_id)
            with self.subTest(job=job_id, draft=draft_id, operation="edit"):
                with self.assertRaises(KeyError):
                    self.service.edit_email(job_id, draft_id, subject="Changed", body="Changed")
        for target, expected in before.items():
            self.assertEqual(self.service.load_results(target), expected)


class WorkflowServiceTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import Mock
        from agents.discovery import Candidate
        from agents.email_writer import EmailDraft
        from agents.verification import VerificationResult
        from graph.state import EvaluationResult

        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "outreach.db"
        self.checkpoint_path = Path(directory.name) / "checkpoints.sqlite3"
        self.storage = SQLiteStorage(self.path)
        self.discovery, self.verifier, self.writer, self.evaluator = Mock(), Mock(), Mock(), Mock()
        self.discovery.discover.side_effect = lambda **settings: [Candidate(
            name="Example", city="Berlin", target_type=settings["target_type"],
            website="https://example.org", source_url="https://example.org",
        )]
        self.verifier.verify.side_effect = lambda candidate, **context: VerificationResult(
            candidate=candidate, **context, status="supported", confidence=0.95,
            reasoning="Supported by official evidence", evidence_quote="Relevant services",
            source_url="https://example.org",
        )
        self.writer.write_verified.return_value = EmailDraft(subject="Enquiry", body="Could we discuss a UG appointment?")
        self.evaluator.side_effect = lambda draft, verification: EvaluationResult(
            draft_id=draft.draft_id, passed=True, score=0.9, claims_supported=True, reasoning="Supported",
        )
        self.service = OutreachService(
            self.storage, checkpoint_path=self.checkpoint_path,
            discovery_agent=self.discovery, verification_agent=self.verifier,
            email_writer=self.writer, evaluator=self.evaluator,
        )
