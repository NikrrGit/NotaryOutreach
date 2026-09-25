import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pydantic import ValidationError

from agents.discovery import Candidate, DiscoveryAgent, DiscoveryError
from notaryoutreach.providers.groq import GroqSearchProvider


def candidate(name="Office A", **fields):
    return {"name": name, "city": "Stuttgart", "source_url": "https://directory.example/notary", **fields}


def agent_for(*batches, **settings):
    provider = Mock()
    provider.search.side_effect = [
        batch if isinstance(batch, Exception) else json.dumps({"candidates": batch})
        for batch in batches
    ]
    return DiscoveryAgent(provider=provider, **settings), provider


class DiscoveryTests(unittest.TestCase):
    def test_batches_and_domain_exclusion(self):
        agent, provider = agent_for(
            [candidate(website="https://www.office.example/path")],
            [candidate("Office B", website="https://second.example")],
        )
        self.assertEqual(len(agent.discover(" Stuttgart ", "UG", 2)), 2)
        self.assertIn("office.example", provider.search.call_args.kwargs["prompt"])
        self.assertEqual(provider.search.call_count, 2)

    def test_deduplication_uses_all_identity_fields(self):
        agent, _ = agent_for([
            candidate(website="https://www.office.example:443", email="contact@office.example"),
            candidate("Alternate name", website="http://office.example/"),
            candidate("Third name", email="CONTACT@office.example"),
            candidate("  OFFICE   A "),
            candidate("Office B"),
        ], [])
        self.assertEqual([c.name for c in agent.discover("Stuttgart", "GmbH")], ["Office A", "Office B"])

    def test_duplicate_only_batch_stops(self):
        agent, provider = agent_for([candidate()], [candidate()], [candidate("B")])
        self.assertEqual(len(agent.discover("Stuttgart", "UG")), 1)
        self.assertEqual(provider.search.call_count, 2)

    def test_empty_batch_stops(self):
        agent, provider = agent_for([])
        self.assertEqual(agent.discover("Stuttgart", "UG"), [])
        self.assertEqual(provider.search.call_count, 1)

    def test_limit_and_attempt_cap(self):
        agent, provider = agent_for([candidate("A"), candidate("B")])
        self.assertEqual(len(agent.discover("Stuttgart", "UG", 1)), 1)
        self.assertEqual(provider.search.call_count, 1)
        agent, provider = agent_for([candidate()], max_attempts=1)
        self.assertEqual(len(agent.discover("Stuttgart", "UG", 50)), 1)
        self.assertEqual(provider.search.call_count, 1)

    def test_zero_limit_needs_no_credentials(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(DiscoveryAgent().discover("Stuttgart", "UG", 0), [])

    def test_invalid_input_does_not_search(self):
        agent, provider = agent_for()
        for location, company_type, limit in [(" ", "UG", 1), ("A", "AG", 1), ("A", "UG", -1), ("A", "UG", True), ("A", "UG", 1.5)]:
            with self.subTest(values=(location, company_type, limit)):
                with self.assertRaises(ValueError):
                    agent.discover(location, company_type, limit)
        provider.search.assert_not_called()

    def test_invalid_constructor_settings(self):
        for settings in ({"batch_size": 0}, {"max_attempts": -1}, {"batch_size": True}):
            with self.assertRaises(ValueError):
                DiscoveryAgent(**settings)

    def test_partial_results_survive_failure(self):
        agent, _ = agent_for([candidate()], RuntimeError("Service unavailable"))
        with self.assertRaises(DiscoveryError) as caught:
            agent.discover("Stuttgart", "UG")
        self.assertEqual(caught.exception.candidates[0].name, "Office A")

    def test_invalid_responses_are_errors_not_empty_results(self):
        for content in ("", "not json", "{}", '[]', '{"candidates": [{}]}'):
            with self.subTest(content=content):
                provider = Mock()
                provider.search.return_value = content
                with self.assertRaises(DiscoveryError):
                    DiscoveryAgent(provider=provider).discover("Stuttgart", "UG")

    def test_candidate_validation(self):
        for fields in ({"name": " "}, {"city": ""}, {"source_url": "javascript:alert(1)"}, {"source_url": "https://user:pass@example.com"}, {"website": "https://example.com:bad"}, {"email": "invalid"}, {"supports_ug": True}):
            with self.subTest(fields=fields):
                with self.assertRaises(ValidationError):
                    Candidate.model_validate(candidate(**fields))
        value = Candidate.model_validate(candidate())
        self.assertIsNone(value.website)
        self.assertEqual(value.model_dump()["source_url"], "https://directory.example/notary")


class ProviderTests(unittest.TestCase):
    def response(self, content='{"candidates": []}', finish_reason="stop"):
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason=finish_reason, message=SimpleNamespace(content=content),
        )])

    def test_request_uses_documented_tools_and_header(self):
        client = Mock()
        client.chat.completions.create.return_value = self.response()
        provider = GroqSearchProvider(client=client)
        self.assertEqual(provider.search(system_prompt="system", prompt="query"), '{"candidates": []}')
        request = client.chat.completions.create.call_args.kwargs
        self.assertEqual(request["extra_headers"], {"Groq-Model-Version": "latest"})
        self.assertEqual(request["compound_custom"]["tools"]["enabled_tools"], ["web_search", "visit_website"])

    def test_missing_or_incomplete_completion(self):
        for response in (SimpleNamespace(choices=[]), self.response(None), self.response("", "length")):
            client = Mock()
            client.chat.completions.create.return_value = response
            with self.assertRaises(ValueError):
                GroqSearchProvider(client=client).search(system_prompt="system", prompt="query")

    def test_default_client_loads_env_and_closes(self):
        with patch("notaryoutreach.providers.groq.build_client") as build:
            client = build.return_value.__enter__.return_value
            client.chat.completions.create.return_value = self.response()
            GroqSearchProvider(env_file="custom.env").search(system_prompt="system", prompt="query")
            self.assertEqual(str(build.call_args.args[0]), "custom.env")
            build.return_value.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()


class VCDiscoveryTests(unittest.TestCase):
    def test_vc_context_candidates_and_deduplication(self):
        provider = Mock()
        provider.search.return_value = json.dumps({"candidates": [
            {"name": "Example Capital", "city": "Berlin", "organization": "Example Capital",
             "website": "https://vc.example", "source_url": "https://vc.example/thesis",
             "metadata": {"role": "Fund", "investment_focus": "Cybersecurity"}},
        ] * 2})
        agent = DiscoveryAgent(provider=provider)
        leads = agent.discover("Germany", target_type="vc", startup_description="Security software",
                               industry="Cybersecurity", funding_stage="Seed", limit=2)
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].target_type, "vc")
        self.assertEqual(leads[0].organization, "Example Capital")
        self.assertEqual(leads[0].metadata["investment_focus"], "Cybersecurity")
        self.assertTrue(leads[0].id)
        prompt = json.loads(provider.search.call_args.kwargs["prompt"])
        self.assertEqual(prompt["startup_description"], "Security software")
        self.assertIn("investment thesis", provider.search.call_args.kwargs["system_prompt"])
        provider.reset_mock()
        with self.assertRaises(ValueError):
            agent.discover("Germany", target_type="vc")
        provider.search.assert_not_called()
