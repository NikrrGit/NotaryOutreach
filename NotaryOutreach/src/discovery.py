"""Discover notary candidates via Groq's web-search-enabled model."""

import argparse
import json
import sys
from pathlib import Path

from groq import APIError, Groq

from .config import ConfigurationError, Settings, load_config
from .models import Candidate, ValidationError

MODEL = "groq/compound"  # Groq's agentic model with built-in web search.

SYSTEM_PROMPT = (
    "You find real, currently practicing notaries in a given city. "
    "Search the web and return only verifiable, real businesses. "
    "Respond with a JSON array only, no prose, no markdown fences. "
    "Each item: {name, city, source_url, website, email, phone}. "
    "Use null for any field you cannot verify. Do not invent data."
)


def build_client(settings: Settings) -> Groq:
    return Groq(api_key=settings.groq_api_key)


def discover_notaries(client: Groq, city: str, limit: int = 20) -> list[Candidate]:
    """Query Groq for candidate notaries in `city`; return parsed, validated Candidates."""
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Find up to {limit} notaries in {city}."},
        ],
        temperature=0,
    )
    content = response.choices[0].message.content.strip()
    try:
        raw_items = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Model did not return valid JSON: {exc}") from exc
    if not isinstance(raw_items, list):
        raise ValidationError("Expected a JSON array of candidate objects.")

    candidates: list[Candidate] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item.setdefault("city", city)
        try:
            candidates.append(Candidate.from_dict(item))
        except ValidationError:
            continue  # Skip malformed entries rather than failing the whole batch.
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("city", help="City to search for notaries in.")
    parser.add_argument("--limit", type=int, default=20, help="Max candidates to request.")
    parser.add_argument(
        "--env-file", type=Path, default=Path(".env"),
        help="Environment file (default: .env in the current directory).",
    )
    args = parser.parse_args()

    try:
        settings = load_config(args.env_file)
        candidates = discover_notaries(build_client(settings), args.city, args.limit)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    except APIError as exc:
        print(f"Groq API error: {exc}", file=sys.stderr)
        return 1
    except ValidationError as exc:
        print(f"Discovery failed: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("Discovery failed due to an unexpected error.", file=sys.stderr)
        return 1

    print(json.dumps([c.to_dict() for c in candidates], indent=2))
    print(f"Discovered {len(candidates)} candidate(s) in {args.city}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())