"""Tavily-only searches persist contacts and keep template editing available."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import httpx

from providers.settings import provider_settings
from providers.tavily import TavilyProvider
from services.email_delivery import default_email
from services.outreach_service import OutreachService
from storage.sqlite import SQLiteStorage


class TavilyServiceTests(unittest.TestCase):
    def test_search_only_runs_both_targets_and_resumes_without_extra_requests(self):
        for target in ("notary", "vc"):
            with self.subTest(target=target), TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=True):
                path = Path(directory)
                env = path / ".env"
                env.write_text("LLM_PROVIDER=none\nSEARCH_PROVIDER=tavily\nTAVILY_API_KEY=secret\n")
                service = OutreachService(SQLiteStorage(path / "data.db"), checkpoint_path=path / "checkpoint.db", env_file=env)
                requests, clients = [], []

                def respond(request):
                    requests.append(json.loads(request.content))
                    return httpx.Response(200, json={"results": [{
                        "title": "Notar Example | Berlin" if target == "notary" else "Example Ventures",
                        "url": "https://example.org/kontakt", "content": "10115 Berlin\noffice@example.org\nVenture capital investments.",
                    }]})

                def factory(settings, **kwargs):
                    self.assertEqual(settings.name, "tavily")
                    client = httpx.Client(base_url="https://api.tavily.com", transport=httpx.MockTransport(respond))
                    clients.append(client)
                    return TavilyProvider(settings, client=client, **kwargs)

                fields = {"company_type": "UG"} if target == "notary" else {
                    "startup_description": "Security software", "industry": "Cybersecurity", "funding_stage": "Seed",
                }
                job = service.create_job(target_type=target, location="Berlin", target_count=1, **fields)
                with patch("providers.clients.create_provider", side_effect=factory):
                    results = service.run_job(job)
                    self.assertEqual(results["job"]["status"], "manual_review")
                    self.assertEqual(len(results["candidates"]), 1)
                    self.assertEqual(results["workflow_errors"], [])
                    self.assertEqual(results["verifications"], [])
                    self.assertEqual(results["evaluations"], [])
                    self.assertTrue(results["has_checkpoint"])
                    self.assertEqual(len(service.resume_job(job)["candidates"]), 1)
                self.assertEqual(len(requests), 1)
                self.assertTrue(all(client.is_closed for client in clients))
                candidate = results["candidates"][0]
                subject, body = default_email(results["job"], candidate)
                draft = service.create_draft(job, candidate["id"], subject=subject, body=body)
                service.edit_email(job, draft, subject="Edited", body="Reviewed message")
                self.assertEqual(len(service.load_results(job)["drafts"]), 2)
                with self.assertRaises(ValueError):
                    service.approve_draft(job, draft)

    def test_search_failures_are_saved_and_client_is_closed(self):
        with TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=True):
            path = Path(directory)
            env = path / ".env"
            env.write_text("LLM_PROVIDER=none\nSEARCH_PROVIDER=tavily\nTAVILY_API_KEY=secret\n")
            service = OutreachService(SQLiteStorage(path / "data.db"), checkpoint_path=path / "checkpoint.db", env_file=env)
            settings = provider_settings({"SEARCH_PROVIDER": "tavily", "TAVILY_API_KEY": "secret"}, search=True)
            client = httpx.Client(base_url="https://api.tavily.com", transport=httpx.MockTransport(
                lambda request: httpx.Response(401, json={"detail": "private-secret"}),
            ))
            provider = TavilyProvider(settings, client=client)
            job = service.create_job(target_type="notary", location="Berlin", company_type="UG", target_count=1)
            with patch("providers.clients.create_provider", return_value=provider):
                results = service.run_job(job)
            self.assertEqual(results["candidates"], [])
            self.assertIn("API key", results["workflow_errors"][0]["message"])
            self.assertNotIn("private-secret", str(results["workflow_errors"]))
            self.assertTrue(client.is_closed)

    def test_optional_generation_provider_is_shared_with_tavily_formatter(self):
        values = {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "model-secret",
                  "SEARCH_PROVIDER": "tavily", "TAVILY_API_KEY": "search-secret"}
        with TemporaryDirectory() as directory, patch.dict("os.environ", values, clear=True):
            service = OutreachService(SQLiteStorage(Path(directory) / "data.db"), env_file=Path(directory) / "missing.env")
            model = Mock(settings=provider_settings(values))
            search = Mock()
            with patch("providers.clients.create_provider", side_effect=[model, search]) as factory:
                with service._agent_session() as (discovery, verifier, writer, evaluator):
                    self.assertIs(discovery.provider, search)
                    self.assertIs(writer.provider, model)
                self.assertEqual(factory.call_args.kwargs, {"formatter": model})
            model.close.assert_called_once()
            search.close.assert_called_once()
