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

    def test_wrong_draft_evaluation_is_rejected(self):
        self.evaluator.side_effect = lambda *_: EvaluationResult(
            draft_id="unrelated-draft", passed=True, reasoning="Wrong draft.",
        )
        state = self.run_graph()
        self.assertEqual(state["status"], "manual_review")
        self.assertEqual(state["evaluations"], [])
        self.assertEqual(len(state["errors"]), 3)

    def test_writer_failure_recovers_without_repeating_verification(self):
        draft = self.writer.write_verified.return_value
        self.writer.write_verified.side_effect = [TimeoutError(), draft]
        state = self.run_graph()
        self.assertEqual(state["status"], "ready_for_review")
        self.assertEqual(state["retry_counts"]["email_generation"], 1)
        self.assertEqual(len(state["errors"]), 1)
        self.verifier.verify.assert_called_once()

    def test_only_failed_candidates_get_new_drafts(self):
        second = self.candidate.model_copy(update={"name": "Second", "source_url": "https://second.example"})
        self.discovery.discover.return_value = [self.candidate, second]
        self.verifier.verify.side_effect = [self.result, self.result.model_copy(update={"candidate": second})]
        attempts = {}

        def evaluate(draft, verification):
            name = draft.candidate.name
            attempts[name] = attempts.get(name, 0) + 1
            return EvaluationResult(
                draft_id=draft.draft_id,
                passed=name == "Office" or attempts[name] > 1,
                reasoning="Checked draft.",
            )

        self.evaluator.side_effect = evaluate
        state = self.run_graph()
        self.assertEqual(state["status"], "ready_for_review")
        self.assertEqual(attempts, {"Office": 1, "Second": 2})
        self.assertEqual(len(state["email_drafts"]), 3)

    def test_resume_after_reopening_database_preserves_models_and_history(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from graph.checkpointing import checkpoint_config, open_checkpointer, resume_job, start_job
        from graph.state import CandidateEmailDraft, SearchSettings

        def build(saver):
            return build_workflow(
                discovery_agent=self.discovery, verification_agent=self.verifier,
                email_writer=self.writer, evaluator=self.evaluator, checkpointer=saver,
            )

        config = checkpoint_config("interrupted-job")
        with TemporaryDirectory() as directory:
            database = Path(directory) / "checkpoints.sqlite3"
            with open_checkpointer(database) as saver:
                graph = build(saver)
                graph.invoke(
                    create_initial_state(location="Berlin", company_type="UG"),
                    config, interrupt_before=["evaluate"], durability="sync",
                )
                self.assertEqual(graph.get_state(config).next, ("evaluate",))
                self.evaluator.assert_not_called()

            with open_checkpointer(database) as saver:
                graph = build(saver)
                result = resume_job(graph, thread_id="interrupted-job")
                self.assertEqual(result["status"], "ready_for_review")
                self.assertIsInstance(result["settings"], SearchSettings)
                self.assertIsInstance(result["candidates"][0], Candidate)
                self.assertIsInstance(result["verification_results"][0], VerificationResult)
                self.assertIsInstance(result["email_drafts"][0], CandidateEmailDraft)
                self.assertEqual(len(result["email_drafts"]), 1)
                self.discovery.discover.assert_called_once()
                self.verifier.verify.assert_called_once()
                self.writer.write_verified.assert_called_once()
                self.evaluator.assert_called_once()
                self.assertEqual(resume_job(graph, thread_id="interrupted-job"), result)
                self.evaluator.assert_called_once()
                with self.assertRaises(ValueError):
                    start_job(graph, thread_id="interrupted-job", initial_state={})
                with self.assertRaises(ValueError):
                    resume_job(graph, thread_id="missing-job")
                self.assertIsNone(saver.get_tuple(checkpoint_config("other-job")))

    def test_process_interruption_resumes_pending_node(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from graph.checkpointing import open_checkpointer, resume_job, start_job

        # BaseException simulates process interruption, escaping node error handling.
        class ProcessInterrupted(BaseException):
            pass

        self.writer.write_verified.side_effect = ProcessInterrupted()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoints.sqlite3"
            with open_checkpointer(path) as saver:
                graph = build_workflow(
                    discovery_agent=self.discovery, verification_agent=self.verifier,
                    email_writer=self.writer, evaluator=self.evaluator, checkpointer=saver,
                )
                with self.assertRaises(ProcessInterrupted):
                    start_job(graph, thread_id="crashed-job", initial_state=create_initial_state(
                        location="Berlin", company_type="UG",
                    ))
            self.writer.write_verified.side_effect = None
            with open_checkpointer(path) as saver:
                graph = build_workflow(
                    discovery_agent=self.discovery, verification_agent=self.verifier,
                    email_writer=self.writer, evaluator=self.evaluator, checkpointer=saver,
                )
                result = resume_job(graph, thread_id="crashed-job")
                self.assertEqual(result["status"], "ready_for_review")
                self.assertEqual(len(result["email_drafts"]), 1)
                self.discovery.discover.assert_called_once()
                self.verifier.verify.assert_called_once()
                self.assertEqual(self.writer.write_verified.call_count, 2)
