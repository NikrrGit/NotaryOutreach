"""Actionable diagnostics without provider response bodies or credentials."""


def failure_message(exc: Exception) -> str:
    current = exc
    for _ in range(5):
        if current.__cause__ is None:
            break
        current = current.__cause__
    status = getattr(current, "status_code", None)
    if status in (401, 403):
        return "The provider rejected access. Check the selected API key and model permissions."
    if status == 404:
        return "The selected model is unavailable. Check LLM_MODEL and SEARCH_MODEL, then retry the search."
    if status == 429:
        return "The provider's usage limit was reached. Check API quota or wait before retrying."
    if isinstance(current, (TimeoutError, ConnectionError)) or type(current).__name__ in {"APITimeoutError", "APIConnectionError"}:
        return "The provider could not be reached. Check your connection and retry."
    if status == 400:
        return "The provider rejected the request. Check that the selected model supports the configured search or JSON features."
    if isinstance(current, ValueError):
        return "The provider returned an invalid result. Retry the search or choose another model."
    return f"Operation failed ({type(current).__name__}). Check provider settings and retry."
