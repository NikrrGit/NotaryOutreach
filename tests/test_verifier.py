import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agents.verifier import Assessment, PageContent, VerificationAgent
from agents.discovery import Candidate
from notaryoutreach.providers.groq import assess_formation


def candidate(**overrides):
    return Candidate.model_validate({
        "name": "Office A", "city": "Stuttgart", "website": "https://office.example",
        "source_url": "https://directory.example/notary", **overrides,
    })


def assessment_json(**overrides):
    return json.dumps({
        "status": "supported", "confidence": 0.95,
        "reasoning": "The office explicitly offers UG formation.",
        "evidence_quote": "Wir begleiten die Gründung Ihrer UG.",
        "source_url": "https://office.example/services", **overrides,
    })


class ConfigurationTests(unittest.TestCase):
    def test_defaults_need_no_credentials(self):
        with patch.dict("os.environ", {}, clear=True):
            agent = VerificationAgent()
        self.assertEqual(agent.max_pages, 4)
        self.assertEqual(agent.minimum_confidence, 0.8)

    def test_invalid_settings(self):
        for settings in (
            {"max_pages": 0}, {"max_pages": 11}, {"max_pages": True},
            {"minimum_confidence": -1}, {"minimum_confidence": float("nan")},
            {"minimum_confidence": True}, {"provider": Mock(), "client": Mock()},
        ):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                VerificationAgent(**settings)

    def test_injected_dependencies(self):
        provider, reader = Mock(), Mock()
        agent = VerificationAgent(provider=provider, page_reader=reader)
        self.assertIs(agent.provider, provider)
        self.assertIs(agent.page_reader, reader)


class AssessmentProviderTests(unittest.TestCase):
    def test_structured_request_and_client_ownership(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[
            SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="{}")),
        ])
        self.assertEqual(assess_formation("system", "input", Assessment.model_json_schema(), client=client), "{}")
        request = client.chat.completions.create.call_args.kwargs
        self.assertEqual(request["model"], "openai/gpt-oss-120b")
        self.assertTrue(request["response_format"]["json_schema"]["strict"])
        client.close.assert_not_called()

    def test_missing_and_incomplete_responses(self):
        for choices in ([], [SimpleNamespace(finish_reason="length")],
                        [SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=None))]):
            client = Mock()
            client.chat.completions.create.return_value = SimpleNamespace(choices=choices)
            with self.assertRaises(ValueError):
                assess_formation("system", "input", {}, client=client)

    def test_owned_client_is_closed(self):
        with patch("notaryoutreach.providers.groq.build_client") as build:
            client = build.return_value.__enter__.return_value
            client.chat.completions.create.return_value = SimpleNamespace(choices=[])
            with self.assertRaises(ValueError):
                assess_formation("system", "input", {}, env_file="custom.env")
            self.assertEqual(str(build.call_args.args[0]), "custom.env")
            build.return_value.__exit__.assert_called_once()


class PageReadingTests(unittest.TestCase):
    def test_reads_homepage_and_service_links_only_on_office_site(self):
        def read(url, links):
            if url == "https://office.example":
                links.extend([
                    "https://office.example/gesellschaftsrecht",
                    "https://other.example/gesellschaftsrecht",
                    "https://user:password@office.example/private",
                    "javascript:alert(1)", "https://office.example#top",
                ])
            return "Website text"

        reader = Mock(side_effect=read)
        pages, errors = VerificationAgent(page_reader=reader)._read_pages(candidate())
        self.assertEqual([page.source_url for page in pages], [
            "https://office.example", "https://office.example/gesellschaftsrecht",
        ])
        self.assertEqual(errors, [])
        self.assertEqual(reader.call_count, 2)

    def test_official_source_survives_homepage_failure(self):
        reader = Mock(side_effect=[TimeoutError("secret"), "We form companies."])
        pages, errors = VerificationAgent(page_reader=reader)._read_pages(
            candidate(source_url="https://www.office.example/services"),
        )
        self.assertEqual(pages[0].source_url, "https://www.office.example/services")
        self.assertEqual(errors[0].stage, "fetch")
        self.assertNotIn("secret", errors[0].message)

    def test_limits_fetch_attempts_including_failures(self):
        def read(url, links):
            links.extend(f"https://office.example/service/{number}" for number in range(20))
            if url.endswith("/1"):
                raise TimeoutError()
            return "Text"

        reader = Mock(side_effect=read)
        pages, errors = VerificationAgent(page_reader=reader, max_pages=3)._read_pages(candidate())
        self.assertEqual(reader.call_count, 3)
        self.assertEqual(len(pages), 2)
        self.assertEqual(len(errors), 1)

    def test_missing_website_does_not_use_directory(self):
        reader = Mock()
        pages, errors = VerificationAgent(page_reader=reader)._read_pages(candidate(website=None))
        self.assertEqual(pages, [])
        self.assertEqual(errors[0].stage, "fetch")
        reader.assert_not_called()

    def test_blank_page_is_a_failure(self):
        pages, errors = VerificationAgent(page_reader=Mock(return_value=" "))._read_pages(candidate())
        self.assertEqual(pages, [])
        self.assertEqual(errors[0].source_url, "https://office.example")


class EvidenceAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.pages = [PageContent(
            source_url="https://office.example/services",
            text="Willkommen. Wir begleiten die Gründung Ihrer UG. Kontaktieren Sie uns.",
        )]

    def test_supported_quote_and_requested_company_type(self):
        for company_type in ("UG", "GmbH"):
            quote = f"Wir begleiten die Gründung Ihrer {company_type}."
            self.pages[0].text = quote
            provider = Mock(return_value=assessment_json(evidence_quote=quote))
            result = VerificationAgent(provider=provider)._assess(self.pages, company_type)
            self.assertEqual(result.status, "supported")
            sent = json.loads(provider.call_args.args[1])
            self.assertEqual(sent["company_type"], company_type)
            self.assertEqual(sent["pages"][0]["text"], self.pages[0].text)

    def test_explicit_unsupported_with_source(self):
        quote = "Wir übernehmen keine UG-Gründungen."
        self.pages[0].text = quote
        provider = Mock(return_value=assessment_json(
            status="unsupported", evidence_quote=quote, reasoning="The office explicitly declines UG formation.",
        ))
        self.assertEqual(VerificationAgent(provider=provider)._assess(self.pages, "UG").status, "unsupported")

    def test_unknown_without_quote_is_preserved(self):
        provider = Mock(return_value=assessment_json(
            status="unknown", evidence_quote=None, source_url=None, reasoning="No formation details.",
        ))
        self.assertEqual(VerificationAgent(provider=provider)._assess(self.pages, "UG").status, "unknown")

    def test_unfounded_claims_are_rejected(self):
        for changes in (
            {"evidence_quote": None}, {"evidence_quote": "invented"},
            {"source_url": "https://other.example/services"},
            {"source_url": None}, {"status": "unsupported", "evidence_quote": "invented"},
            {"status": "unknown", "source_url": None},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                VerificationAgent(provider=Mock(return_value=assessment_json(**changes)))._assess(self.pages, "UG")

    def test_quote_must_match_its_specific_page(self):
        self.pages.append(PageContent(source_url="https://office.example/other", text="Contact"))
        with self.assertRaises(ValueError):
            VerificationAgent(provider=Mock(return_value=assessment_json(
                source_url="https://office.example/other",
            )))._assess(self.pages, "UG")

    def test_low_confidence_remains_unknown_with_evidence(self):
        provider = Mock(return_value=assessment_json(confidence=0.4))
        result = VerificationAgent(provider=provider)._assess(self.pages, "UG")
        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.confidence, 0.4)
        self.assertIsNotNone(result.evidence_quote)

    def test_invalid_model_output_is_rejected(self):
        for output in ("", "not json", "{}", assessment_json(confidence=2),
                       assessment_json(confidence=True), assessment_json(reasoning=" "),
                       assessment_json(confidence=float("nan")), assessment_json(status="yes")):
            with self.subTest(output=output), self.assertRaises(ValueError):
                VerificationAgent(provider=Mock(return_value=output))._assess(self.pages, "UG")

    def test_invalid_request_does_not_call_provider(self):
        provider = Mock()
        agent = VerificationAgent(provider=provider)
        with self.assertRaises(ValueError):
            agent._assess([], "UG")
        with self.assertRaises(ValueError):
            agent._assess(self.pages, "AG")
        provider.assert_not_called()
