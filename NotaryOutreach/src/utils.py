"""URL normalization, contact extraction, and office deduplication."""
 
import re
from urllib.parse import urlsplit, urlunsplit
 
from .models import Candidate, ValidationError
 
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s.-]?)?(?:\(?\d{2,5}\)?[\s.-]?){2,5}\d{2,4}")
DEFAULT_PORTS = {"http": 80, "https": 443}
 
 
def normalize_url(url: str) -> str:
    """Lowercase scheme/host, drop default ports, query, fragment, and trailing slash."""
    if not isinstance(url, str) or not url.strip():
        raise ValidationError("URL must be nonempty text.")
    parsed = urlsplit(url.strip())
    if parsed.scheme not in DEFAULT_PORTS or not parsed.hostname:
        raise ValidationError(f"Invalid URL: {url!r}")
    if parsed.username or parsed.password:
        raise ValidationError(f"URL must not contain credentials: {url!r}")
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    port = parsed.port
    netloc = host if port in (None, DEFAULT_PORTS[scheme]) else f"{host}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))


def extract_contacts(text: str) -> dict[str, str | None]:
    """Return the first email and phone-like string found in `text`, or None each."""
    if not isinstance(text, str):
        raise ValidationError("Text must be a string.")
    email_match = EMAIL_RE.search(text)
    phone_match = PHONE_RE.search(text)
    return {
        "email": email_match.group(0) if email_match else None,
        "phone": phone_match.group(0).strip() if phone_match else None,
    }
 
 
def _dedupe_key(candidate: Candidate) -> str:
    """Prefer a normalized website; fall back to normalized name+city."""
    if candidate.website:
        try:
            return normalize_url(candidate.website)
        except ValidationError:
            pass
    return f"{candidate.name.strip().lower()}|{candidate.city.strip().lower()}"
 
 
def dedupe_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Drop candidates that share a normalized website or name+city, keeping the first."""
    seen: set[str] = set()
    deduped: list[Candidate] = []
    for candidate in candidates:
        key = _dedupe_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped
 