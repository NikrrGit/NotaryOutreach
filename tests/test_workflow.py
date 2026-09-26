"""Exercise the compiled graph with local, deterministic agent doubles."""

import unittest
from unittest.mock import Mock

from agents.discovery import Candidate
from agents.email_writer import EmailDraft
from agents.verification import VerificationResult
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

    def test_real_evaluator_adapts_provider_assessment_to_workflow(self):
        from agents.evaluator import EmailEvaluator

        provider = Mock()
        provider.generate_structured.return_value = {
            "passed": True, "appointment_requested": True,
            "correct_company_type": True, "claims_supported": True,
            "score": 0.95, "reasoning": "All checks passed.", "issues": [],
        }
        self.writer.write_verified.return_value = EmailDraft(
            subject="UG appointment", body="Could we arrange a UG formation appointment?",
        )
        self.evaluator = EmailEvaluator(provider)
        state = self.run_graph()
        self.assertEqual(state["status"], "ready_for_review")
        self.assertEqual(state["evaluations"][0].draft_id, state["email_drafts"][0].draft_id)
        provider.generate_structured.assert_called_once()


class VCWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.context = dict(target_type="vc", location="Germany", startup_description="Security software",
                            industry="Cybersecurity", funding_stage="Seed")
        self.candidate = Candidate(name="Example VC", city="Berlin", target_type="vc",
                                   source_url="https://vc.example")
        self.verification = VerificationResult(
            candidate=self.candidate, **self.context, status="supported", confidence=0.95,
            reasoning="Sector, stage and geography match.",
            evidence_quote="European cybersecurity investments at seed stage.", source_url="https://vc.example",
        )
        self.discovery, self.verifier, self.writer = Mock(), Mock(), Mock()
        self.discovery.discover.return_value = [self.candidate]
        self.verifier.verify.return_value = self.verification
        self.writer.write_verified.return_value = EmailDraft(subject="Security software", body="Could we have a conversation?")
        self.provider = Mock()
        self.provider.generate_structured.return_value = dict(
            passed=True, conversation_requested=True, startup_represented_correctly=True,
            investment_fit_supported=True, claims_supported=True, score=0.95, reasoning="Supported", issues=[],
        )

    def build(self, checkpointer=None):
        from agents.evaluator import EmailEvaluator

        return build_workflow(discovery_agent=self.discovery, verification_agent=self.verifier,
                              email_writer=self.writer, evaluator=EmailEvaluator(self.provider),
                              checkpointer=checkpointer)

    def test_vc_success_preserves_context_and_review_gate(self):
        state = self.build().invoke(create_initial_state(**self.context, target_results=3))
        self.assertEqual(state["status"], "ready_for_review")
        self.assertEqual(state["settings"].agent_context(), {**self.context, "company_type": None})
        self.discovery.discover.assert_called_once_with(**self.context, company_type=None, limit=3)
        self.verifier.verify.assert_called_once_with(self.candidate, **self.context, company_type=None)
        self.assertEqual(len(state["email_drafts"]), 1)
        self.assertTrue(state["evaluations"][0].passed)
        self.assertEqual(state["errors"], [])

    def test_vc_retries_unknown_results_and_failed_drafts(self):
        self.verifier.verify.side_effect = [
            self.verification.model_copy(update={"status": "unknown"}), self.verification,
        ]
        passed = self.provider.generate_structured.return_value
        self.provider.generate_structured.side_effect = [
            {**passed, "investment_fit_supported": False}, passed,
        ]
        state = self.build().invoke(create_initial_state(**self.context))
        self.assertEqual(state["status"], "ready_for_review")
        self.assertEqual(state["retry_counts"]["verification"], 1)
        self.assertEqual(state["retry_counts"]["email_generation"], 1)
        self.assertEqual(len({draft.draft_id for draft in state["email_drafts"]}), 2)
        self.assertEqual([result.passed for result in state["evaluations"]], [False, True])

    def test_vc_mismatched_context_is_rejected_with_bounded_retries(self):
        for changes in ({"funding_stage": "Series A"}, {"startup_description": "A different startup"},
                        {"target_type": "notary", "company_type": "UG"}):
            with self.subTest(changes=changes):
                self.verifier.verify.reset_mock()
                self.verifier.verify.return_value = self.verification.model_copy(update=changes)
                state = self.build().invoke(create_initial_state(**self.context))
                self.assertEqual(state["status"], "manual_review")
                self.assertEqual(state["verification_results"], [])
                self.assertEqual(self.verifier.verify.call_count, 3)
                self.assertEqual(state["email_drafts"], [])
        self.writer.write_verified.assert_not_called()

    def test_vc_negative_evidence_and_wrong_target_discovery(self):
        self.verifier.verify.return_value = self.verification.model_copy(update={"status": "unsupported"})
        state = self.build().invoke(create_initial_state(**self.context))
        self.assertEqual(state["status"], "manual_review")
        self.assertEqual(state["retry_counts"]["verification"], 0)
        self.writer.write_verified.assert_not_called()
        self.verifier.verify.return_value = self.verification.model_copy(update={
            "status": "unsupported", "evidence_quote": None,
        })
        state = self.build().invoke(create_initial_state(**self.context))
        self.assertEqual(state["retry_counts"]["verification"], 2)
        self.writer.write_verified.assert_not_called()
        self.discovery.discover.return_value = [self.candidate.model_copy(update={"target_type": "notary"})]
        state = self.build().invoke(create_initial_state(**self.context))
        self.assertEqual(state["candidates"], [])
        self.assertEqual(state["errors"][0].node, "discover")

    def test_vc_checkpoint_resume_preserves_context_without_replaying_agents(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from graph.checkpointing import open_checkpointer, start_job, resume_job

        with TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoints.sqlite3"
            with open_checkpointer(path) as saver:
                original = start_job(self.build(saver), thread_id="vc-job",
                                     initial_state=create_initial_state(**self.context))
            with open_checkpointer(path) as saver:
                resumed = resume_job(self.build(saver), thread_id="vc-job")
            self.assertEqual(resumed, original)
            self.assertEqual(resumed["settings"].target_type, "vc")
            self.assertEqual(resumed["verification_results"][0].startup_description, "Security software")
            self.discovery.discover.assert_called_once()
            self.verifier.verify.assert_called_once()
            self.writer.write_verified.assert_called_once()

    def test_vc_settings_and_standalone_routing(self):
        from graph.routing import route_after_evaluation

        for changes in ({"startup_description": None}, {"industry": " "},
                        {"funding_stage": None}, {"company_type": "UG"}, {"target_type": "other"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                create_initial_state(**{**self.context, **changes})
        state = {"target_type": "vc", "evaluation": self.provider.generate_structured.return_value,
                 "retry_counts": {"email_generation": 0}}
        self.assertEqual(route_after_evaluation(state), "continue")
        state["evaluation"] = {**state["evaluation"], "conversation_requested": False}
        self.assertEqual(route_after_evaluation(state), "retry")
        state["retry_counts"]["email_generation"] = 2
        self.assertEqual(route_after_evaluation(state), "manual_review")

    def test_vc_graph_connects_real_agents_with_mocked_providers(self):
        import json
        from agents.discovery import DiscoveryAgent
        from agents.verification import VerificationAgent
        from agents.email_writer import EmailWriter
        from agents.evaluator import EmailEvaluator

        search = Mock()
        search.search.return_value = json.dumps({"candidates": [{
            "name": "Example VC", "city": "Berlin", "website": "https://vc.example",
            "source_url": "https://vc.example",
        }]})
        assessment = Mock(return_value=json.dumps({
            "status": "supported", "confidence": 0.95, "reasoning": "Stage and sector fit in Europe.",
            "evidence_quote": self.verification.evidence_quote, "source_url": "https://vc.example",
        }))
        writer = Mock()
        writer.generate_structured.return_value = self.writer.write_verified.return_value
        graph = build_workflow(
            discovery_agent=DiscoveryAgent(provider=search),
            verification_agent=VerificationAgent(provider=assessment, page_reader=Mock(return_value=self.verification.evidence_quote)),
            email_writer=EmailWriter(writer), evaluator=EmailEvaluator(self.provider),
        )
        state = graph.invoke(create_initial_state(**self.context, target_results=1))
        self.assertEqual(state["status"], "ready_for_review")
        self.assertEqual(state["errors"], [])
        self.assertEqual(state["candidates"][0].target_type, "vc")
        for provider in (writer, self.provider):
            request = json.loads(provider.generate_structured.call_args.kwargs["user_prompt"])
            self.assertEqual(request["startup_description"], self.context["startup_description"])
            self.assertEqual(request["funding_stage"], "Seed")

    def test_vc_resume_after_verification_does_not_repeat_research(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from graph.checkpointing import checkpoint_config, open_checkpointer, resume_job

        with TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoints.sqlite3"
            with open_checkpointer(path) as saver:
                graph = self.build(saver)
                graph.invoke(create_initial_state(**self.context), checkpoint_config("paused-vc"),
                             interrupt_after=["verify"], durability="sync")
                self.writer.write_verified.assert_not_called()
            with open_checkpointer(path) as saver:
                state = resume_job(self.build(saver), thread_id="paused-vc")
            self.assertEqual(state["status"], "ready_for_review")
            self.discovery.discover.assert_called_once()
            self.verifier.verify.assert_called_once()
            self.writer.write_verified.assert_called_once()
