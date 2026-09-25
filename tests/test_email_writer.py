"""Verification-to-drafting handoff checks without network calls."""

import json
import unittest
from unittest.mock import Mock

from agents.discovery import Candidate
from agents.email_writer import EmailDraft, EmailWriter, EmailWriterInput
from agents.verification import VerificationAgent, VerificationResult


def verified(**overrides):
    fields = dict(
        candidate=Candidate(
            name="Test Office", city="Berlin", website="https://example.com",
            email="office@example.com", source_url="https://example.com/contact",
            company_type_hint="GmbH",
        ),
        company_type="UG", status="supported", confidence=0.9,
        reasoning="The page confirms UG formation services.",
        evidence_quote="Wir begleiten UG-Gründungen.",
        source_url="https://example.com/services",
        pages_reviewed=["https://example.com/services"],
    )
    return VerificationResult(**(fields | overrides))


class EmailWriterHandoffTests(unittest.TestCase):
    def setUp(self):
        self.provider = Mock()
        self.provider.generate_structured.return_value = EmailDraft(
            subject="Terminanfrage", body="Wann ist ein Termin möglich?",
        )
        self.writer = EmailWriter(self.provider)

    def test_mapping_uses_verification_evidence_and_company_type(self):
        result = verified()
        data = EmailWriterInput.from_verification(
            result, sender_name="Alex", company_name="Example",
        )
        self.assertEqual(data.notary_name, result.candidate.name)
        self.assertEqual(data.city, result.candidate.city)
        self.assertEqual(data.email, result.candidate.email)
        self.assertEqual(data.company_type, "UG")
        self.assertEqual(data.evidence, result.evidence_quote)
        self.assertEqual(data.verification_reason, result.reasoning)
        self.assertEqual(data.source_url, result.source_url)
        self.assertNotEqual(data.source_url, result.candidate.source_url)
        self.assertEqual(data.sender_name, "Alex")
        self.assertEqual(data.company_name, "Example")

    def test_supported_result_generates_draft_with_sender_details(self):
        draft = self.writer.write_verified(verified(), sender_name="Alex", company_name="Example")
        self.assertIs(draft, self.provider.generate_structured.return_value)
        self.provider.generate_structured.assert_called_once()
        prompt = self.provider.generate_structured.call_args.kwargs["user_prompt"]
        for text in ("Alex", "Example", "Wir begleiten UG-Gründungen.", "https://example.com/services"):
            self.assertIn(text, prompt)

    def test_ineligible_results_never_call_provider(self):
        for overrides in (
            {"status": "unknown"}, {"status": "unsupported"},
            {"evidence_quote": None}, {"evidence_quote": "  "},
            {"source_url": None}, {"source_url": "  "},
            {"source_url": "not-a-url"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    self.writer.write_verified(verified(**overrides))
        self.provider.generate_structured.assert_not_called()

    def test_wrong_input_type_is_rejected(self):
        with self.assertRaises(TypeError):
            self.writer.write_verified({"status": "supported"})
        self.provider.generate_structured.assert_not_called()

    def test_missing_contact_and_sender_do_not_prevent_drafting(self):
        result = verified()
        result.candidate.email = None
        data = EmailWriterInput.from_verification(result)
        self.assertIsNone(data.email)
        self.assertIsNone(data.sender_name)
        self.assertIsNone(data.company_name)
        self.assertIsInstance(self.writer.write_verified(result), EmailDraft)

    def test_real_verifier_handoff_respects_its_evidence_and_confidence_gate(self):
        candidate = verified().candidate
        for confidence, quote, should_draft in (
            (0.9, "Wir begleiten UG-Gründungen.", True),
            (0.7, "Wir begleiten UG-Gründungen.", False),
            (0.9, "Invented evidence", False),
        ):
            with self.subTest(confidence=confidence, quote=quote):
                verifier = VerificationAgent(
                    provider=Mock(return_value=json.dumps(dict(
                        status="supported", confidence=confidence,
                        reasoning="UG formation is offered.", evidence_quote=quote,
                        source_url=candidate.website,
                    ))),
                    page_reader=Mock(return_value="Wir begleiten UG-Gründungen."),
                )
                result = verifier.verify(candidate, "UG")
                self.provider.reset_mock()
                if should_draft:
                    self.writer.write_verified(result)
                    self.provider.generate_structured.assert_called_once()
                else:
                    with self.assertRaises(ValueError):
                        self.writer.write_verified(result)
                    self.provider.generate_structured.assert_not_called()


class VCEmailWriterTests(unittest.TestCase):
    def test_vc_draft_uses_verified_fit_and_startup_context(self):
        verification = VerificationResult(
            candidate=Candidate(name="Example VC", organization="Example Capital", city="Berlin",
                                target_type="vc", source_url="https://vc.example"),
            target_type="vc", location="Germany", startup_description="Security software for SMEs",
            industry="Cybersecurity", funding_stage="Seed", status="supported", confidence=0.9,
            reasoning="Sector and stage match", evidence_quote="European seed cybersecurity investments",
            source_url="https://vc.example/thesis",
        )
        provider = Mock()
        provider.generate_structured.return_value = EmailDraft(subject="Austausch", body="Hätten Sie Zeit für ein Gespräch?")
        writer = EmailWriter(provider)
        writer.write_verified(verification, sender_name="Alex", company_name="SecureCo")
        request = provider.generate_structured.call_args.kwargs
        data = json.loads(request["user_prompt"])
        self.assertEqual(data["target_type"], "vc")
        self.assertEqual(data["organization"], "Example Capital")
        self.assertEqual(data["startup_description"], verification.startup_description)
        self.assertEqual(data["evidence"], verification.evidence_quote)
        self.assertEqual(data["company_name"], "SecureCo")
        self.assertIn("short conversation", request["system_prompt"])
        self.assertNotIn("earliest possible notary appointment", request["system_prompt"])
        provider.reset_mock()
        for change in ({"status": "unknown"}, {"evidence_quote": None}, {"startup_description": None}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                writer.write_verified(verification.model_copy(update=change))
        provider.generate_structured.assert_not_called()
