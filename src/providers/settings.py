"""Provider selection shared by configuration and runtime."""

from dataclasses import dataclass, field
from urllib.parse import urlsplit


DEFAULT_MODELS = {
    "groq": "openai/gpt-oss-20b",
    "openai": "gpt-4.1-mini",
    "anthropic": "claude-sonnet-4-6",
}
KEY_NAMES = {
    "groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY", "openai_compatible": "LLM_API_KEY",
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
        raise ValueError("Provider must be groq, openai, anthropic, or openai_compatible.")
    if search and name == "openai_compatible":
        raise ValueError("Set SEARCH_PROVIDER to groq, openai, or anthropic for live discovery.")
    key_name = KEY_NAMES[name]
    key = (values.get(key_name) or "").strip() or None
    if require_key and key is None:
        raise ValueError(f"Set {key_name} in the environment or .env file.")
    model = (values.get("LLM_MODEL") or "").strip() if not search else ""
    model = model or DEFAULT_MODELS.get(name, "")
    search_model = (values.get("SEARCH_MODEL") or "").strip()
    search_model = search_model or ("openai/gpt-oss-120b" if name == "groq" else DEFAULT_MODELS.get(name, ""))
    base_url = None
    if name == "openai_compatible":
        base_url = (values.get("LLM_BASE_URL") or "").strip()
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Set LLM_BASE_URL to an HTTP(S) API URL without credentials, query, or fragment.")
        if not model:
            raise ValueError("Set LLM_MODEL for the OpenAI-compatible endpoint.")
    return ProviderSettings(name, model, key, search_model, base_url)
