import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agents.verifier import Assessment, VerificationAgent
from notaryoutreach.providers.groq import assess_formation


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
