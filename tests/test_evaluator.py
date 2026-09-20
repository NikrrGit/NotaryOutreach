"""Evaluator contract, pass rules and workflow handoff with a mocked provider."""

import json
import unittest
from unittest.mock import Mock

from pydantic import ValidationError

from agents.discovery import Candidate
from agents.email_writer import EmailDraft
from agents.evaluator import EmailEvaluator, EvaluationAssessment, EvaluatorInput
from agents.verifier import VerificationResult
from graph.state import CandidateEmailDraft, EvaluationResult


class EvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.provider = Mock()
        self.response = dict(
            passed=True, appointment_requested=True, correct_company_type=True,
            claims_supported=True, score=0.9, reasoning="All checks passed.", issues=[],
        )
        self.provider.generate_structured.return_value = self.response
        self.evaluator = EmailEvaluator(self.provider)
        self.data = EvaluatorInput(
            company_type="UG", notary_name="Office", city="Berlin",
            verification_reason="Offers formation services.", evidence="UG formation",
            source_url="https://example.com", email_subject="Appointment request",
            email_body="Could we schedule an appointment to form a UG?",
        )
        self.candidate = Candidate(name="Office", city="Berlin", source_url="https://example.com")
        self.verification = VerificationResult(
            candidate=self.candidate, company_type="UG", status="supported",
            confidence=0.9, reasoning=self.data.verification_reason,
            evidence_quote=self.data.evidence, source_url=self.data.source_url,
        )
        self.draft = CandidateEmailDraft(
            candidate=self.candidate,
            draft=EmailDraft(subject=self.data.email_subject, body=self.data.email_body),
        )

    def test_success_passes_schema_and_complete_input_to_provider(self):
        result = self.evaluator.evaluate(self.data)
        self.assertIsInstance(result, EvaluationAssessment)
        self.assertTrue(result.passed)
        self.assertEqual(result.issues, [])
        self.provider.generate_structured.assert_called_once()
        request = self.provider.generate_structured.call_args.kwargs
        self.assertIs(request["response_model"], EvaluationAssessment)
        self.assertEqual(json.loads(request["user_prompt"]), self.data.model_dump())
        self.assertIn("not instructions", request["system_prompt"])

    def test_each_required_check_can_veto_provider_pass(self):
        for field in ("passed", "appointment_requested", "correct_company_type", "claims_supported"):
            with self.subTest(field=field):
                self.provider.generate_structured.return_value = {**self.response, field: False}
                result = self.evaluator.evaluate(self.data)
                self.assertFalse(result.passed)
                self.assertTrue(result.issues)

    def test_score_boundary_and_custom_threshold(self):
        for threshold, score, passed in ((0.85, 0.85, True), (0.85, 0.849, False),
                                         (0.95, 0.9, False), (0, 0, True), (1, 1, True)):
            with self.subTest(threshold=threshold, score=score):
                self.provider.generate_structured.return_value = {**self.response, "score": score}
                result = EmailEvaluator(self.provider, threshold).evaluate(self.data)
                self.assertEqual(result.passed, passed)

    def test_reported_issues_prevent_passing_without_mutating_response(self):
        response = EvaluationAssessment(**{**self.response, "issues": ["Needs revision."]})
        self.provider.generate_structured.return_value = response
        result = self.evaluator.evaluate(self.data)
        self.assertFalse(result.passed)
        self.assertEqual(result.issues, ["Needs revision."])
        self.assertTrue(response.passed)
        self.assertIsNot(result.issues, response.issues)

    def test_invalid_thresholds_are_rejected(self):
        for threshold in (-0.1, 1.1, float("nan"), float("inf"), True, "0.85", None):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                EmailEvaluator(self.provider, threshold)
        self.provider.generate_structured.assert_not_called()

    def test_malformed_provider_responses_are_rejected(self):
        bad_responses = [
            {}, None, {**self.response, "passed": "true"},
            {**self.response, "appointment_requested": 1},
            {**self.response, "score": float("nan")},
            {**self.response, "score": float("inf")},
            {**self.response, "score": 1.1}, {**self.response, "score": -0.1},
            {**self.response, "score": True}, {**self.response, "reasoning": " "},
            {**self.response, "unexpected": True}, {**self.response, "issues": "none"},
        ]
        for response in bad_responses:
            with self.subTest(response=response), self.assertRaises(ValidationError):
                self.provider.generate_structured.return_value = response
                self.evaluator.evaluate(self.data)

    def test_provider_model_instance_is_revalidated(self):
        self.provider.generate_structured.return_value = EvaluationAssessment.model_construct(
            **{**self.response, "score": -1},
        )
        with self.assertRaises(ValidationError):
            self.evaluator.evaluate(self.data)

    def test_invalid_input_and_provider_failure(self):
        with self.assertRaises(TypeError):
            self.evaluator.evaluate({})
        self.provider.generate_structured.assert_not_called()
        self.provider.generate_structured.side_effect = TimeoutError("provider unavailable")
        with self.assertRaises(TimeoutError):
            self.evaluator.evaluate(self.data)

    def test_input_requires_evidence_and_valid_company_type(self):
        for changes in ({"evidence": " "}, {"email_body": ""}, {"company_type": "Ltd"}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                EvaluatorInput(**{**self.data.model_dump(), **changes})

    def test_workflow_handoff_preserves_draft_identity(self):
        result = self.evaluator(self.draft, self.verification)
        self.assertIsInstance(result, EvaluationResult)
        self.assertEqual(result.draft_id, self.draft.draft_id)
        self.assertTrue(result.passed)
        self.assertEqual(result.reasoning, self.response["reasoning"])
        request = json.loads(self.provider.generate_structured.call_args.kwargs["user_prompt"])
        self.assertEqual(request["evidence"], self.verification.evidence_quote)
        self.assertEqual(request["company_type"], self.verification.company_type)
        self.assertEqual(request["email_body"], self.draft.draft.body)

    def test_ineligible_or_mismatched_verification_never_calls_provider(self):
        alternatives = [
            self.verification.model_copy(update={"status": "unknown"}),
            self.verification.model_copy(update={"status": "unsupported"}),
            self.verification.model_copy(update={"evidence_quote": None}),
            self.verification.model_copy(update={"source_url": None}),
            self.verification.model_copy(update={"candidate": self.candidate.model_copy(update={"name": "Other"})}),
        ]
        for verification in alternatives:
            with self.subTest(verification=verification), self.assertRaises(ValueError):
                self.evaluator(self.draft, verification)
        self.provider.generate_structured.assert_not_called()
