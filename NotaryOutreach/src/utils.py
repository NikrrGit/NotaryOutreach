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
