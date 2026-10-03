"""Tavily API contracts, sourced lead extraction, and private diagnostics."""

import json
import unittest
from unittest.mock import Mock

import httpx

from agents.discovery import DiscoveryAgent
from providers.errors import failure_message
from providers.settings import provider_settings
from providers.tavily import TavilyProvider


def office_result():
    return {"title": "Notar Example | Berlin", "url": "https://example.org/kontakt",
            "content": "Notar Example. 10115 Berlin\nTelefon: 030 / 1234567\nE-Mail: office@example.org",
            "raw_content": "Wir begleiten Unternehmensgründungen."}


class TavilyProviderTests(unittest.TestCase):
    def provider(self, responder, *, formatter=None):
        client = httpx.Client(base_url="https://api.tavily.com", transport=httpx.MockTransport(responder))
        self.addCleanup(client.close)
        settings = provider_settings({"LLM_PROVIDER": "none", "SEARCH_PROVIDER": "tavily",
                                      "TAVILY_API_KEY": "test-secret"}, search=True, require_key=True)
        return TavilyProvider(settings, client=client, formatter=formatter)

    def test_notary_search_extracts_only_sourced_contacts_and_filters_directories(self):
        requests = []

        def respond(request):
            self.assertEqual(str(request.url), "https://api.tavily.com/search")
            self.assertEqual(request.headers["Authorization"], "Bearer test-secret")
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"results": [office_result(),
                {**office_result(), "url": "https://gelbeseiten.de/office"},
                {**office_result(), "url": "https://user:password@example.org"},
                {**office_result(), "title": "Company incorporation information"},
                {**office_result(), "title": "Notar Elsewhere", "content": "No city available"},
                {**office_result(), "title": "Notar Second", "url": "https://second.org", "content": "Berlin, emails from third parties: unrelated@example.org"},
                "malformed result", {"title": "Missing URL"},
            ]})

        provider = self.provider(respond)
        candidates = DiscoveryAgent(provider=provider).discover("Berlin", "UG", limit=2)
        self.assertEqual(len(candidates), 2)
        candidate = candidates[0]
        self.assertEqual((candidate.name, candidate.city), ("Notar Example", "Berlin"))
        self.assertEqual(candidate.email, "office@example.org")
        self.assertEqual(candidate.phone, "030 / 1234567")
        self.assertEqual(candidate.source_url, "https://example.org/kontakt")
        self.assertEqual(candidate.website, "https://example.org")
        self.assertEqual(candidate.metadata["search_provider"], "tavily")
        self.assertIsNone(candidates[1].email)
        self.assertEqual(len(requests), 1)
        self.assertIn("UG", requests[0]["query"])
        self.assertNotIn("Return JSON", requests[0]["query"])
        self.assertTrue(requests[0]["include_raw_content"])
        self.assertFalse(requests[0]["include_answer"])

    def test_vc_query_preserves_context_and_discovers_firms(self):
        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"results": [{"title": "Example Ventures",
                "url": "https://example.org", "content": "Berlin venture capital fund. Cybersecurity seed investments."}]})

        candidates = DiscoveryAgent(provider=self.provider(respond)).discover(
            "Berlin", target_type="vc", startup_description="Security software",
            industry="Cybersecurity", funding_stage="Seed", limit=1,
        )
        self.assertEqual(candidates[0].target_type, "vc")
        self.assertIn("Cybersecurity Seed", requests[0]["query"])

    def test_empty_results_do_not_call_formatter(self):
        formatter = Mock()
        candidates = DiscoveryAgent(provider=self.provider(
            lambda request: httpx.Response(200, json={"results": []}), formatter=formatter,
        )).discover("Berlin", "UG", limit=1)
        self.assertEqual(candidates, [])
        formatter.assess.assert_not_called()

    def test_optional_model_formats_research_without_native_web_search(self):
        formatter = Mock()
        formatter.assess.return_value = json.dumps({"candidates": [{"name": "Example", "city": "Berlin",
            "source_url": "https://example.org", "email": "office@example.org"}]})
        provider = self.provider(lambda request: httpx.Response(200, json={"results": [office_result()]}), formatter=formatter)
        candidates = DiscoveryAgent(provider=provider).discover("Berlin", "UG", limit=1)
        self.assertEqual(candidates[0].email, "office@example.org")
        research = json.loads(formatter.assess.call_args.args[1])["research"]
        self.assertEqual(research[0]["url"], "https://example.org/kontakt")
        formatter.search.assert_not_called()

    def test_api_errors_and_timeouts_are_actionable_without_private_bodies(self):
        for status, expected in ((401, "API key"), (432, "Tavily"), (433, "Tavily"), (429, "usage limit")):
            with self.subTest(status=status):
                provider = self.provider(lambda request: httpx.Response(status, json={"detail": "private-secret"}))
                with self.assertRaises(Exception) as caught:
                    DiscoveryAgent(provider=provider).discover("Berlin", "UG", limit=1)
                message = failure_message(caught.exception)
                self.assertIn(expected, message)
                self.assertNotIn("private-secret", message)

        def timeout(request):
            raise httpx.ReadTimeout("private-secret", request=request)

        with self.assertRaises(Exception) as caught:
            DiscoveryAgent(provider=self.provider(timeout)).discover("Berlin", "UG", limit=1)
        self.assertIn("could not be reached", failure_message(caught.exception))
        self.assertNotIn("private-secret", failure_message(caught.exception))

    def test_invalid_results_fail_instead_of_silently_succeeding(self):
        for payload in ([], {}, {"results": "wrong type"}):
            with self.subTest(payload=payload), self.assertRaises(Exception):
                DiscoveryAgent(provider=self.provider(
                    lambda request: httpx.Response(200, json=payload),
                )).discover("Berlin", "UG", limit=1)
