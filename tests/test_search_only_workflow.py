"""Search-only runs preserve contacts without claiming AI verification."""

import unittest
from unittest.mock import Mock

from agents.discovery import Candidate
from graph.workflow import build_workflow, create_initial_state


class SearchOnlyWorkflowTests(unittest.TestCase):
    def test_both_targets_finish_discovery_without_model_calls(self):
        for target in ("notary", "vc"):
            with self.subTest(target=target):
                discovery, verifier, writer, evaluator = Mock(), Mock(), Mock(), Mock()
                candidate = Candidate(target_type=target, name="Example", city="Berlin", source_url="https://example.org")
                discovery.discover.return_value = [candidate]
                fields = {"company_type": "UG"} if target == "notary" else {
                    "startup_description": "Security software", "industry": "Cybersecurity", "funding_stage": "Seed",
                }
                initial = create_initial_state(target_type=target, location="Berlin", target_results=1, **fields)
                graph = build_workflow(discovery_agent=discovery, verification_agent=verifier,
                                       email_writer=writer, evaluator=evaluator, search_only=True)
                result = graph.invoke(initial)
                self.assertEqual(result["status"], "manual_review")
                self.assertEqual(result["candidates"], [candidate])
                self.assertEqual(result["verification_results"], [])
                self.assertEqual(result["email_drafts"], [])
                self.assertEqual(result["errors"], [])
                self.assertEqual(result["retry_counts"]["verification"], 0)
                verifier.verify.assert_not_called()
                writer.write_verified.assert_not_called()
                evaluator.assert_not_called()
