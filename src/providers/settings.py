"""Provider selection shared by configuration and runtime."""

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .errors import ProviderConfigurationError


DEFAULT_MODELS = {
    "grok": "grok-4.7",
    "groq": "openai/gpt-oss-20b",
    "openai": "gpt-4.1-mini",
    "anthropic": "claude-sonnet-4-6",
    "openrouter": "openai/gpt-4.1-mini",
}
KEY_NAMES = {
    "grok": "GROK_API_KEY",
    "groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY", "openai_compatible": "LLM_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


@dataclass(frozen=True)
class ProviderSettings:
    name: str
    model: str
    api_key: str | None = field(repr=False)
    search_model: str
    base_url: str | None = None


def provider_settings(values: dict, *, search: bool = False, require_key: bool = False) -> ProviderSettings:
    """Resolve one provider without reading global environment or opening clients."""
    primary = (values.get("LLM_PROVIDER") or "groq").strip().lower()
    name = ((values.get("SEARCH_PROVIDER") or primary) if search else primary).strip().lower()
    if name not in KEY_NAMES:
        raise ProviderConfigurationError("Set LLM_PROVIDER and SEARCH_PROVIDER to grok, groq, openai, anthropic, openrouter, or openai_compatible.")
    if search and name == "openai_compatible":
        raise ProviderConfigurationError("Set SEARCH_PROVIDER to grok, groq, openai, anthropic, or openrouter for live discovery.")
    key_name = KEY_NAMES[name]
    key = (values.get(key_name) or "").strip() or None
    if name == "grok" and key is None:
        key = (values.get("XAI_API_KEY") or "").strip() or None
    if require_key and key is None:
        raise ProviderConfigurationError(
            f"Set {key_name} in the environment or .env file. "
            "If you use another provider, change LLM_PROVIDER and SEARCH_PROVIDER to match your key."
        )
    model = (values.get("LLM_MODEL") or "").strip() if not search or (name == primary == "openrouter") else ""
    model = model or DEFAULT_MODELS.get(name, "")
    search_model = (values.get("SEARCH_MODEL") or "").strip()
    search_model = search_model or ("openai/gpt-oss-120b" if name == "groq" else DEFAULT_MODELS.get(name, ""))
    if name == "openrouter":
        search_model = (values.get("SEARCH_MODEL") or "").strip() or model
        if ":online" in model:
            raise ProviderConfigurationError("Remove :online from LLM_MODEL; OpenRouter web search is enabled only for discovery.")
    base_url = "https://api.x.ai/v1" if name == "grok" else None
    if name == "openrouter":
        base_url = "https://openrouter.ai/api/v1"
    if name == "openai_compatible":
        base_url = (values.get("LLM_BASE_URL") or "").strip()
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ProviderConfigurationError("Set LLM_BASE_URL to an HTTP(S) API URL without credentials, query, or fragment.")
        if not model:
            raise ProviderConfigurationError("Set LLM_MODEL for the OpenAI-compatible endpoint.")
    return ProviderSettings(name, model, key, search_model, base_url)
