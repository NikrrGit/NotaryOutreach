"""Exercise the compiled graph with local, deterministic agent doubles."""

import unittest
from unittest.mock import Mock

from agents.discovery import Candidate
from agents.email_writer import EmailDraft
from agents.verifier import VerificationResult
from graph.state import EvaluationResult
from graph.workflow import build_workflow, create_initial_state


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.candidate = Candidate(name="Office", city="Berlin", source_url="https://example.com")
        self.result = VerificationResult(
            candidate=self.candidate, company_type="UG", status="supported",
            confidence=0.9, reasoning="Offers formation services.",
            evidence_quote="UG formation", source_url="https://example.com",
        )
        self.discovery = Mock()
        self.discovery.discover.return_value = [self.candidate]
        self.verifier = Mock()
        self.verifier.verify.return_value = self.result
        self.writer = Mock()
        self.writer.write_verified.return_value = EmailDraft(subject="Appointment", body="Please meet us.")
        self.evaluator = Mock(side_effect=lambda draft, verification: EvaluationResult(
            draft_id=draft.draft_id, passed=True, reasoning="All checks passed.",
        ))

    def run_graph(self):
        return build_workflow(
            discovery_agent=self.discovery, verification_agent=self.verifier,
            email_writer=self.writer, evaluator=self.evaluator,
        ).invoke(create_initial_state(location="Berlin", company_type="UG"))

    def test_success(self):
        state = self.run_graph()
        self.assertEqual(state["status"], "ready_for_review")
        self.assertEqual(len(state["email_drafts"]), 1)
        self.assertEqual(state["evaluations"][0].draft_id, state["email_drafts"][0].draft_id)
        self.assertEqual(state["errors"], [])

    def test_unknown_verification_is_retried(self):
        unknown = self.result.model_copy(update={"status": "unknown"})
        self.verifier.verify.side_effect = [unknown, self.result]
        state = self.run_graph()
        self.assertEqual(state["status"], "ready_for_review")
        self.assertEqual(state["retry_counts"]["verification"], 1)
        self.assertEqual(len(state["verification_results"]), 2)

    def test_verification_failure_preserves_errors_and_stops(self):
        self.verifier.verify.side_effect = TimeoutError("private provider details")
        state = self.run_graph()
        self.assertEqual(state["status"], "manual_review")
        self.assertEqual(self.verifier.verify.call_count, 3)
        self.assertEqual(len(state["errors"]), 3)
        self.assertNotIn("private provider details", str(state["errors"]))
        self.writer.write_verified.assert_not_called()

    def test_failed_drafts_are_regenerated_with_new_ids(self):
        self.evaluator.side_effect = lambda draft, verification: EvaluationResult(
            draft_id=draft.draft_id, passed=False, reasoning="Needs revision.",
        )
        state = self.run_graph()
        self.assertEqual(state["status"], "manual_review")
        self.assertEqual(state["retry_counts"]["email_generation"], 2)
        self.assertEqual(len({d.draft_id for d in state["email_drafts"]}), 3)
        self.assertEqual(len(state["evaluations"]), 3)
        self.discovery.discover.assert_called_once()
        self.verifier.verify.assert_called_once()

    def test_evaluation_exception_is_recorded(self):
        self.evaluator.side_effect = ValueError("bad response")
        state = self.run_graph()
        self.assertEqual(state["status"], "manual_review")
        self.assertEqual(len(state["errors"]), 3)

    def test_unsupported_candidate_is_not_drafted(self):
        self.verifier.verify.return_value = self.result.model_copy(update={"status": "unsupported"})
        self.assertEqual(self.run_graph()["status"], "manual_review")
        self.writer.write_verified.assert_not_called()

    def test_invalid_settings_are_rejected(self):
        for changes in ({"location": " "}, {"company_type": "Other"},
                        {"target_results": 0}, {"target_results": True}, {"target_results": 101}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                create_initial_state(**{ "location": "Berlin", "company_type": "UG", **changes})
