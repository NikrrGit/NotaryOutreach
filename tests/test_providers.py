"""Provider contracts, selection, and complete offline workflows."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import httpx
from anthropic import Anthropic
from openai import OpenAI
from groq import Groq
from pydantic import ValidationError

from agents.email_writer import EmailDraft
from notaryoutreach.config import ConfigurationError, load_config
from providers.clients import AnthropicProvider, CompatibleProvider, OpenAIProvider, create_provider
from providers.settings import provider_settings
from services.outreach_service import OutreachService
from storage.sqlite import SQLiteStorage


class ProviderTests(unittest.TestCase):
    def test_settings_select_keys_models_and_keep_secrets_private(self):
        for name, key_name in (("groq", "GROQ_API_KEY"), ("openai", "OPENAI_API_KEY"),
                               ("anthropic", "ANTHROPIC_API_KEY"), ("openrouter", "OPENROUTER_API_KEY")):
            with self.subTest(provider=name), TemporaryDirectory() as directory:
                env = Path(directory) / ".env"
                env.write_text(f"LLM_PROVIDER={name}\n{key_name}=file-secret\n")
                with patch.dict("os.environ", {key_name: "env-secret", "LLM_MODEL": "custom-model"}, clear=True):
                    config = load_config(env, require_api_key=True)
                self.assertEqual(config.llm.name, name)
                self.assertEqual(config.llm.api_key, "env-secret")
                self.assertEqual(config.llm.model, "custom-model")
                self.assertEqual(config.search.name, name)
                self.assertNotIn("secret", repr(config))

    def test_invalid_provider_settings_and_missing_selected_key(self):
        cases = [
            {"LLM_PROVIDER": "unknown"},
            {"LLM_PROVIDER": "openai", "GROQ_API_KEY": "wrong-provider"},
            {"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": " "},
            {"LLM_PROVIDER": "openai_compatible", "LLM_API_KEY": "key"},
            {"LLM_PROVIDER": "openai_compatible", "LLM_API_KEY": "key", "LLM_MODEL": "local",
             "LLM_BASE_URL": "http://localhost:1234/v1"},
            {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "key", "SEARCH_PROVIDER": "anthropic"},
        ]
        for values in cases:
            with self.subTest(values=values), patch.dict("os.environ", values, clear=True):
                with self.assertRaises(ConfigurationError):
                    load_config("/nonexistent/provider-test.env", require_api_key=True)
        for url in ("file:///tmp/api", "https://user:secret@example.com/v1", "https://example.com?key=secret"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                provider_settings({"LLM_PROVIDER": "openai_compatible", "LLM_MODEL": "test", "LLM_BASE_URL": url})

    def test_generation_validates_schema_and_rejects_incomplete_output(self):
        for name, adapter in (("openai", OpenAIProvider), ("anthropic", AnthropicProvider)):
            client = Mock()
            provider = adapter(provider_settings({"LLM_PROVIDER": name}), client)
            for text in ('{}', '[]', 'not json', '{"subject":"","body":"x"}'):
                client.responses.create.return_value = NS(status="completed", output_text=text)
                client.messages.create.return_value = NS(stop_reason="end_turn", content=[NS(type="text", text=text)])
                with self.subTest(provider=name, text=text), self.assertRaises((ValueError, ValidationError)):
                    provider.generate_structured(system_prompt="Write", user_prompt="Draft", response_model=EmailDraft)
            client.responses.create.return_value = NS(status="incomplete", output_text='{"subject":"x","body":"y"}')
            client.messages.create.return_value = NS(stop_reason="max_tokens", content=[NS(type="text", text="partial")])
            with self.assertRaises(RuntimeError):
                provider.generate_structured(system_prompt="Write", user_prompt="Draft", response_model=EmailDraft)
            client.responses.create.return_value = NS(status="completed", output_text=" ")
            client.messages.create.return_value = NS(stop_reason="end_turn", content=[])
            with self.assertRaises(RuntimeError):
                provider.generate_structured(system_prompt="Write", user_prompt="Draft", response_model=EmailDraft)

    def test_openai_requires_completed_live_search(self):
        client = Mock()
        provider = OpenAIProvider(provider_settings({"LLM_PROVIDER": "openai"}), client)
        for output in ([], [NS(type="web_search_call", status="failed")]):
            client.responses.create.return_value = NS(status="completed", output_text="Unsourced", output=output)
            with self.assertRaises(RuntimeError):
                provider.search(system_prompt="Discover", prompt="Berlin")
        self.assertEqual(client.responses.create.call_args.kwargs["tool_choice"], "required")

    def test_anthropic_search_errors_and_continuation_are_bounded(self):
        client = Mock()
        provider = AnthropicProvider(provider_settings({"LLM_PROVIDER": "anthropic"}), client)
        error = Mock()
        error.model_dump.return_value = {"type": "web_search_tool_result", "content": {"type": "web_search_tool_result_error"}}
        for response in (NS(stop_reason="end_turn", content=[error]), NS(stop_reason="end_turn", content=[])):
            client.messages.create.return_value = response
            with self.assertRaises(RuntimeError):
                provider.search(system_prompt="Discover", prompt="Berlin")
        paused = Mock()
        paused.model_dump.return_value = {"type": "text", "text": "Continuing"}
        client.reset_mock()
        client.messages.create.return_value = NS(stop_reason="pause_turn", content=[paused])
        with self.assertRaisesRegex(RuntimeError, "continuation limit"):
            provider.search(system_prompt="Discover", prompt="Berlin")
        self.assertEqual(client.messages.create.call_count, 3)
        self.assertEqual(len(client.messages.create.call_args.kwargs["messages"]), 4)

    def test_anthropic_paused_search_resumes_with_original_content(self):
        client = Mock()
        provider = AnthropicProvider(provider_settings({"LLM_PROVIDER": "anthropic"}), client)
        paused_data = {"type": "server_tool_use", "id": "tool-1", "name": "web_search", "input": {"query": "Berlin"}}
        result_data = {"type": "web_search_tool_result", "tool_use_id": "tool-1", "content": []}
        paused, result = Mock(), Mock()
        paused.model_dump.return_value = paused_data
        result.model_dump.return_value = result_data
        client.messages.create.side_effect = [
            NS(stop_reason="pause_turn", content=[paused]),
            NS(stop_reason="end_turn", content=[result]),
            NS(stop_reason="end_turn", content=[NS(type="text", text='{"candidates": []}')]),
        ]
        self.assertEqual(json.loads(provider.search(system_prompt="Discover", prompt="Berlin")), {"candidates": []})
        continued = client.messages.create.call_args_list[1].kwargs
        self.assertEqual(continued["messages"][1], {"role": "assistant", "content": [paused_data]})
        self.assertIn("tools", continued)
        self.assertNotIn("tools", client.messages.create.call_args.kwargs)
        self.assertIn("web_search_tool_result", client.messages.create.call_args.kwargs["messages"][0]["content"])

    def test_native_api_failures_propagate_without_fallback(self):
        for name, adapter in (("openai", OpenAIProvider), ("anthropic", AnthropicProvider)):
            client = Mock()
            client.responses.create.side_effect = TimeoutError("offline")
            client.messages.create.side_effect = TimeoutError("offline")
            provider = adapter(provider_settings({"LLM_PROVIDER": name}), client)
            with self.subTest(provider=name), self.assertRaises(TimeoutError):
                provider.search(system_prompt="Discover", prompt="Berlin")
            with self.assertRaises(TimeoutError):
                provider.generate_structured(system_prompt="Write", user_prompt="Draft", response_model=EmailDraft)

    def test_custom_endpoint_generation_and_explicit_search_provider(self):
        values = {"LLM_PROVIDER": "openai_compatible", "LLM_API_KEY": "custom-secret",
                  "LLM_MODEL": "custom-model", "LLM_BASE_URL": "http://localhost:1234/v1",
                  "SEARCH_PROVIDER": "openai", "OPENAI_API_KEY": "search-secret", "SEARCH_MODEL": "search-model"}
        settings = provider_settings(values, require_key=True)
        with patch("openai.OpenAI") as factory:
            provider = create_provider(settings)
        self.assertIsInstance(provider, CompatibleProvider)
        self.assertEqual(factory.call_args.kwargs["base_url"], values["LLM_BASE_URL"])
        self.assertEqual(factory.call_args.kwargs["api_key"], "custom-secret")
        provider.client.chat.completions.create.return_value = NS(choices=[NS(
            finish_reason="stop", message=NS(content='```json\n{"subject":"Hello","body":"World"}\n```'))])
        result = provider.generate_structured(system_prompt="Write", user_prompt="Draft", response_model=EmailDraft)
        self.assertEqual(result.subject, "Hello")
        request = provider.client.chat.completions.create.call_args.kwargs
        self.assertEqual(request["model"], "custom-model")
        self.assertNotIn("tools", request)
        self.assertNotIn("response_format", request)
        self.assertEqual(provider_settings(values, search=True, require_key=True).api_key, "search-secret")
        with self.assertRaises(ValueError):
            provider.search(system_prompt="Discover", prompt="Berlin")
        provider.close()
        provider.client.close.assert_called_once()

    def test_session_closes_clients_on_failure_and_skips_unused_search_key(self):
        values = {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "key", "SEARCH_PROVIDER": "anthropic"}
        with TemporaryDirectory() as directory, patch.dict("os.environ", values, clear=True):
            service = OutreachService(SQLiteStorage(Path(directory) / "data.db"), env_file=Path(directory) / "missing.env")
            provider = Mock(settings=provider_settings(values))
            with patch("providers.clients.create_provider", return_value=provider):
                with self.assertRaises(ValueError):
                    with service._agent_session():
                        self.fail("Missing search key must prevent execution")
                provider.close.assert_called_once()
                provider.reset_mock()
                with service._agent_session(evaluation_only=True) as agents:
                    self.assertIs(agents[3].provider, provider)
                provider.close.assert_called_once()

    def test_all_providers_run_both_targets_and_preserve_reviews(self):
        for name in ("groq", "openai", "anthropic", "openai_compatible", "openrouter"):
            for target in ("notary", "vc"):
                with self.subTest(provider=name, target=target):
                    self._run_workflow(name, target)

    def test_groq_formatting_recovery_completes_a_real_agent_workflow(self):
        self._run_workflow("groq", "notary", format_error=True)

    def _run_workflow(self, name, target, *, format_error=False):
        quote = "Wir begleiten UG-Gründungen." if target == "notary" else "We invest in European cybersecurity startups at seed stage."
        candidate = {"name": "Example", "city": "Berlin", "website": "https://example.org",
                     "source_url": "https://example.org", "email": "office@example.org", "target_type": target}
        assessment = dict(status="supported", confidence=0.95, reasoning="Evidence matches",
                          evidence_quote=quote, source_url="https://example.org")
        draft = dict(subject="Anfrage", body="Wann wäre ein Termin zur UG-Gründung möglich?" if target == "notary"
                     else "Wir entwickeln Sicherheitssoftware. Hätten Sie Zeit für ein kurzes Gespräch?")
        evaluation = dict(passed=True, claims_supported=True, score=0.95, reasoning="Grounded", issues=[],
                          **(dict(appointment_requested=True, correct_company_type=True) if target == "notary" else
                             dict(conversation_requested=True, startup_represented_correctly=True, investment_fit_supported=True)))
        payloads = iter([{"candidates": [candidate]}, assessment, draft, evaluation, evaluation])
        requests = []

        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            if format_error and len(requests) == 2:
                return httpx.Response(400, json={"error": {
                    "code": "tool_use_failed", "message": "Tool choice is none, but model called a tool",
                }})
            research = "tools" in body or "plugins" in body
            if name == "openrouter":
                self.assertEqual(str(request.url), "https://openrouter.ai/api/v1/chat/completions")
                self.assertEqual(request.headers["authorization"], "Bearer test-key")
            content = json.dumps({"candidates": [candidate]} if research else next(payloads))
            if request.url.path.endswith("/responses"):
                output = [{"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                           "content": [{"type": "output_text", "text": content, "annotations": []}]}]
                if research:
                    output.insert(0, {"type": "web_search_call", "id": "ws_1", "status": "completed",
                                      "action": {"type": "search", "query": "Berlin"}})
                data = {"id": "resp_1", "object": "response", "created_at": 1, "status": "completed",
                        "model": body["model"], "output": output}
            elif request.url.path.endswith("/messages"):
                blocks = [{"type": "text", "text": content}]
                if research:
                    blocks.insert(0, {"type": "web_search_tool_result", "tool_use_id": "srv_1", "content": [
                        {"type": "web_search_result", "url": "https://example.org", "title": "Example", "encrypted_content": "result"}]})
                data = {"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
                        "content": blocks, "stop_reason": "end_turn", "stop_sequence": None,
                        "usage": {"input_tokens": 10, "output_tokens": 10}}
            else:
                data = {"id": "chat_1", "object": "chat.completion", "created": 1, "model": body["model"],
                        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}]}
                if name == "openrouter" and research:
                    data["choices"][0]["message"]["annotations"] = [{"type": "url_citation", "url_citation": {
                        "url": "https://example.org", "title": "Example", "content": quote,
                        "start_index": 0, "end_index": 7,
                    }}]
            return httpx.Response(200, json=data)

        sdk, patch_path = {"openai": (OpenAI, "openai.OpenAI"), "anthropic": (Anthropic, "anthropic.Anthropic"),
                           "groq": (Groq, "providers.groq.Groq"),
                           "openrouter": (OpenAI, "openai.OpenAI"),
                           "openai_compatible": (OpenAI, "openai.OpenAI")}[name]
        clients = []

        def client_factory(**kwargs):
            client = sdk(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(respond)))
            clients.append(client)
            return client

        values = {"LLM_PROVIDER": name, f"{name.upper()}_API_KEY": "test-key"}
        if name == "openai_compatible":
            values.update(LLM_API_KEY="endpoint-key", LLM_MODEL="local-model", LLM_BASE_URL="http://localhost:1234/v1",
                          SEARCH_PROVIDER="openai", OPENAI_API_KEY="search-key")
        with TemporaryDirectory() as directory, patch.dict("os.environ", values, clear=True), patch(
            patch_path, side_effect=client_factory,
        ), patch("agents.verification.fetch_page_text", return_value=quote):
            path = Path(directory)
            service = OutreachService(SQLiteStorage(path / "data.db"), checkpoint_path=path / "checkpoints.db", env_file=path / "missing.env")
            fields = dict(company_type="UG") if target == "notary" else dict(
                startup_description="Security software", industry="Cybersecurity", funding_stage="Seed")
            job = service.create_job(target_type=target, location="Berlin", target_count=1, **fields)
            results = service.run_job(job)
            self.assertEqual(results["job"]["status"], "ready_for_review", results["workflow_errors"])
            self.assertEqual(results["verifications"][0]["evidence"], quote)
            original = results["drafts"][0]
            service.approve_draft(job, original["id"])
            edited = service.edit_email(job, original["id"], subject="Updated", body=original["body"])
            with self.assertRaises(ValueError):
                service.approve_draft(job, edited)
            service.evaluate_draft(job, edited)
            service.approve_draft(job, edited)
            before_resume = len(requests)
            self.assertEqual(len(service.resume_job(job)["reviews"]), 2)
            self.assertEqual(len(requests), before_resume)
            self.assertTrue(all(client.is_closed() for client in clients))
            self.assertEqual(len(clients), 3 if name == "openai_compatible" else 2)
            if name == "groq":
                self.assertEqual(requests[0]["tools"], [{"type": "browser_search"}])
                self.assertNotIn("response_format", requests[0])
            elif name == "openrouter":
                self.assertEqual(requests[0]["plugins"], [{"id": "web", "engine": "exa", "max_results": 10}])
                self.assertTrue(all("plugins" not in request and "tools" not in request for request in requests[1:]))
                self.assertIn("https://example.org", requests[1]["messages"][1]["content"])
                self.assertTrue(all(request["model"] == "openai/gpt-4.1-mini" for request in requests))
            else:
                self.assertIn("tools", requests[0])
                self.assertTrue(all("tools" not in request for request in requests[1:]))
