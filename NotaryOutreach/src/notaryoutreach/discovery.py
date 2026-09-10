"""Discover notary candidates via Groq's web-search-enabled model."""

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from groq import APIError, Groq

from .config import ConfigurationError
from .models import Candidate, ValidationError

MODEL = "groq/compound-mini"
FIELDS = ("name", "city", "source_url", "website", "email", "phone")

SYSTEM_PROMPT = (
    "You find real, currently practicing notaries in a given city. "
    "Search the web and return only verifiable, real businesses. "
    "Search for Notar UG Gründung, GmbH Gründung, and Gesellschaftsrecht. "
    "Prefer the official German notary directory and official notary websites. "
    "Respond with a JSON array only, no prose, no markdown fences. "
    "Each item: {name, city, source_url, website, email, phone}. "
    "Require a verified name, city and HTTP(S) source_url; omit entries missing these. "
    "Use null for optional fields you cannot verify. Do not invent data."
)


def build_client(env_file: Path) -> Groq:
    key = os.environ.get("GROQ_API_KEY", dotenv_values(env_file).get("GROQ_API_KEY"))
    if not key or not key.strip():
        raise ConfigurationError("Missing GROQ_API_KEY in the environment or .env file.")
    return Groq(api_key=key.strip(), timeout=60, max_retries=1)


def discover_notaries(client: Groq, city: str, limit: int = 10) -> list[Candidate]:
    if not isinstance(city, str) or not city.strip():
        raise ValidationError("City must be nonempty text.")
    if type(limit) is not int or limit < 1:
        raise ValidationError("Limit must be a positive integer.")
    city = city.strip()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Find up to {limit} notaries in {city}."},
        ],
        temperature=0,
        compound_custom={"tools": {"enabled_tools": ["web_search"]}},
    )
    if not response.choices or not response.choices[0].message.content:
        raise ValidationError("Model returned no content.")
    content = response.choices[0].message.content.strip()
    if content.startswith("```") and content.endswith("```"):
        content = "\n".join(content.splitlines()[1:-1])
    try:
        raw_items = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValidationError("Model did not return valid JSON.") from exc
    if not isinstance(raw_items, list):
        raise ValidationError("Expected a JSON array of candidate objects.")

    candidates: list[Candidate] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            data = {field: item.get(field) for field in FIELDS}
            if any(value is not None and not isinstance(value, str) for value in data.values()):
                continue
            for field in ("source_url", "website"):
                value = data[field]
                if field == "website" and value is None:
                    continue
                if not value or any(character.isspace() for character in value):
                    raise ValidationError("Invalid URL.")
                url = urlsplit(value)
                if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
                    raise ValidationError("Invalid URL.")
                _ = url.port
            candidates.append(Candidate.from_dict(data))
        except (ValidationError, ValueError):
            continue
        if len(candidates) == limit:
            break
    if raw_items and not candidates:
        raise ValidationError("No usable candidates in the model response.")
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("city", nargs="?", default="Stuttgart", help="City to search (default: Stuttgart).")
    parser.add_argument("--limit", type=int, default=10, help="Max candidates to request.")
    parser.add_argument(
        "--env-file", type=Path, default=Path(".env"),
        help="Environment file (default: .env in the current directory).",
    )
    args = parser.parse_args()

    try:
        with build_client(args.env_file) as client:
            candidates = discover_notaries(client, args.city, args.limit)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    except APIError:
        print("Groq request failed. Check the API key, quota, and network.", file=sys.stderr)
        return 1
    except ValidationError as exc:
        print(f"Discovery failed: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("Discovery failed due to an unexpected error.", file=sys.stderr)
        return 1

    print(json.dumps([c.to_dict() for c in candidates], indent=2, ensure_ascii=False))
    print(f"Discovered {len(candidates)} candidate(s) in {args.city}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
