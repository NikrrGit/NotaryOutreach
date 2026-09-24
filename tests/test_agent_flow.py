"""Mocked agent integration checks; LangGraph routing is not implemented yet."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from agents.discovery import DiscoveryAgent, DiscoveryError
from agents.verification import VerificationAgent


def completion(payload):
    return SimpleNamespace(choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content=json.dumps(payload)),
    )])


def office(name, domain, **overrides):
    return {
        "name": name, "city": "Stuttgart", "website": f"https://{domain}",
        "source_url": f"https://{domain}/services", **overrides,
    }


def assessment(status="supported", confidence=0.9, quote="Wir begleiten UG-Gründungen."):
    return {
        "status": status, "confidence": confidence, "reasoning": "Assessment of the supplied service text.",
        "evidence_quote": quote, "source_url": "https://a.example/services",
    }


class AgentFlowTests(unittest.TestCase):
    def test_cross_batch_duplicates_are_verified_only_once(self):
        client = Mock()
        client.chat.completions.create.side_effect = [
            completion({"candidates": [office("A", "a.example", email="info@a.example")]}),
            completion({"candidates": [
                office("Alternate A", "alias.example", email="INFO@a.example"),
                office("B", "b.example"),
            ]}),
            completion(assessment()),
            completion({**assessment("unsupported", quote="Wir übernehmen keine UG-Gründungen."),
                        "source_url": "https://b.example/services"}),
        ]
        candidates = DiscoveryAgent(client=client, batch_size=1).discover("Stuttgart", "UG", 2)
        reader = Mock(side_effect=["Welcome", "Wir begleiten UG-Gründungen.",
                                   "Welcome", "Wir übernehmen keine UG-Gründungen."])
        results = VerificationAgent(client=client, page_reader=reader).verify_candidates(candidates, "UG")
        self.assertEqual([item.candidate.name for item in results], ["A", "B"])
        self.assertEqual([item.status for item in results], ["supported", "unsupported"])
        self.assertEqual(client.chat.completions.create.call_count, 4)
        self.assertEqual(reader.call_count, 4)

    def test_discovery_stops_at_attempt_limit_even_with_new_results(self):
        client = Mock()
        client.chat.completions.create.side_effect = [
            completion({"candidates": [office(str(number), f"office{number}.example")]})
            for number in range(5)
        ]
        candidates = DiscoveryAgent(client=client, max_attempts=3, batch_size=1).discover("Stuttgart", "UG", 10)
        self.assertEqual(len(candidates), 3)
        self.assertEqual(client.chat.completions.create.call_count, 3)

    def test_failed_discovery_batch_preserves_results_without_agent_retry(self):
        client = Mock()
        client.chat.completions.create.side_effect = [
            completion({"candidates": [office("A", "a.example")]}),
            TimeoutError("API unavailable"),
        ]
        with self.assertRaises(DiscoveryError) as caught:
            DiscoveryAgent(client=client, max_attempts=10).discover("Stuttgart", "UG", 2)
        self.assertEqual([item.name for item in caught.exception.candidates], ["A"])
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_evidence_and_confidence_gate_real_provider_responses(self):
        cases = [
            (assessment(confidence=0.8), "supported", False),
            (assessment(confidence=0.79), "unknown", False),
            (assessment(quote="Invented evidence"), "unknown", True),
            ({**assessment(), "source_url": "https://a.example"}, "unknown", True),
            ({**assessment("unsupported"), "evidence_quote": None}, "unknown", True),
        ]
        for response, expected_status, has_error in cases:
            with self.subTest(response=response):
                client = Mock()
                client.chat.completions.create.side_effect = [
                    completion({"candidates": [office("A", "a.example")]}), completion(response),
                ]
                candidates = DiscoveryAgent(client=client).discover("Stuttgart", "UG", 1)
                reader = Mock(side_effect=["Welcome", "Wir begleiten UG-Gründungen."])
                result = VerificationAgent(client=client, page_reader=reader).verify_candidates(candidates, "UG")[0]
                self.assertEqual(result.status, expected_status)
                self.assertEqual(bool(result.errors), has_error)
                self.assertEqual(client.chat.completions.create.call_count, 2)
