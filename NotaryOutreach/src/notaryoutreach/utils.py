"""URL normalization, contact extraction, and office deduplication."""

import re
from html import unescape
from urllib.parse import urlsplit, urlunsplit

from .models import Candidate, ValidationError

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\w+])(?:\+49|0049|\(?0)[0-9 ()/\t-]*[0-9](?!\w)")
DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_url(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ValidationError("URL must be nonempty text.")
    url = url.strip()
    if any(character.isspace() for character in url):
        raise ValidationError("URL must not contain whitespace.")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValidationError("Invalid URL host or port.") from exc
    if parsed.scheme not in DEFAULT_PORTS or not parsed.hostname:
        raise ValidationError("URL must have an HTTP(S) scheme and a host.")
    if parsed.username is not None or parsed.password is not None:
        raise ValidationError("URL must not contain credentials.")
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = host if port in (None, DEFAULT_PORTS[scheme]) else f"{host}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))


def extract_contacts(text: str) -> dict[str, str | None]:
    if not isinstance(text, str):
        raise ValidationError("Text must be a string.")
    text = unescape(text)
    email_match = EMAIL_RE.search(text)
    phone = next(
        (match.group(0).strip() for match in PHONE_RE.finditer(text)
         if 7 <= len(re.sub(r"\D", "", match.group(0))) <= 15
         and not re.fullmatch(r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}", match.group(0))),
        None,
    )
    return {
        "email": email_match.group(0) if email_match else None,
        "phone": phone,
    }


def _dedupe_keys(candidate: Candidate) -> set[tuple[str, ...]]:
    name = " ".join(candidate.name.casefold().split())
    city = " ".join(candidate.city.casefold().split())
    keys = {("name_city", name, city)}
    if isinstance(candidate.email, str) and EMAIL_RE.fullmatch(candidate.email.strip()):
        keys.add(("email", candidate.email.strip().casefold()))
    if candidate.website:
        try:
            host = urlsplit(normalize_url(candidate.website)).hostname
            keys.add(("domain", host.removeprefix("www.").rstrip(".")))
        except ValidationError:
            pass
    return keys


def dedupe_candidates(candidates: list[Candidate]) -> list[Candidate]:
    groups: list[tuple[set[tuple[str, ...]], Candidate]] = []
    for candidate in candidates:
        keys = _dedupe_keys(candidate)
        matches = [index for index, (known, _) in enumerate(groups) if keys & known]
        if not matches:
            groups.append((keys, candidate))
        else:
            first = matches[0]
            for index in matches:
                keys.update(groups[index][0])
            groups[first] = (keys, groups[first][1])
            for index in reversed(matches[1:]):
                groups.pop(index)
    return [candidate for _, candidate in groups]
