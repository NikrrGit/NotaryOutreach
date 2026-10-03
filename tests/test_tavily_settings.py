"""Search-only credentials and optional model selection."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from notaryoutreach.config import ConfigurationError, load_config
from providers.settings import provider_settings


class TavilySettingsTests(unittest.TestCase):
    def test_search_only_requires_tavily_key_and_ignores_stale_models(self):
        with TemporaryDirectory() as directory:
            env = Path(directory) / ".env"
            env.write_text("LLM_PROVIDER=none\nSEARCH_PROVIDER=tavily\nTAVILY_API_KEY=file-secret\nLLM_MODEL=old-model\nSEARCH_MODEL=old-model\n")
            with patch.dict("os.environ", {}, clear=True):
                settings = load_config(env, require_api_key=True)
            self.assertEqual(settings.llm.name, "none")
            self.assertIsNone(settings.llm.api_key)
            self.assertEqual(settings.search.name, "tavily")
            self.assertEqual(settings.search.api_key, "file-secret")
            self.assertEqual(settings.search.search_model, "")
            self.assertNotIn("file-secret", repr(settings))
            with patch.dict("os.environ", {"TAVILY_API_KEY": " "}, clear=True):
                with self.assertRaisesRegex(ConfigurationError, "TAVILY_API_KEY"):
                    load_config(env, require_api_key=True)

    def test_tavily_requires_search_selection_and_allows_model_generation(self):
        with self.assertRaisesRegex(ValueError, "SEARCH_PROVIDER=tavily"):
            provider_settings({"LLM_PROVIDER": "tavily"})
        values = {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "model-secret",
                  "SEARCH_PROVIDER": "tavily", "TAVILY_API_KEY": "search-secret"}
        self.assertEqual(provider_settings(values, require_key=True).name, "openai")
        self.assertEqual(provider_settings(values, search=True, require_key=True).api_key, "search-secret")
