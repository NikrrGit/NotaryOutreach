"""Run the notary outreach pipeline: connect, discover, verify, persist."""

import argparse
import sys
from pathlib import Path

from groq import APIError as GroqAPIError
from postgrest.exceptions import APIError as PostgrestAPIError

from .config import ConfigurationError, load_config
from .database import connect, inspect_notaries
from .discovery import build_client as build_groq_client
from .discovery import discover_notaries
from .models import Candidate, CandidateStatus, ValidationError
from .verification import verify_candidates


def phase1_connectivity(env_file: Path):
    """Confirm the Supabase connection is usable; return the client for later phases."""
    settings = load_config(env_file)
    client = connect(settings)
    inspect_notaries(client)  # Raises on failure; the columns themselves aren't needed here.
    return client


def phase2_discovery(env_file: Path, city: str, limit: int) -> list[Candidate]:
    """Find raw notary candidates for `city` via Groq web search."""
    with build_groq_client(env_file) as groq_client:
        return discover_notaries(groq_client, city, limit)


def phase3_verification(env_file: Path, candidates: list[Candidate]) -> list[Candidate]:
    """Assess UG-formation suitability for each discovered candidate."""
    with build_groq_client(env_file) as groq_client:
        return verify_candidates(groq_client, candidates)


def phase4_persist(db_client, candidates: list[Candidate]) -> int:
    """Upsert non-errored candidates into public.notaries, keyed on source_url."""
    payload = [c.to_dict() for c in candidates if c.status != CandidateStatus.ERROR]
    if not payload:
        return 0
    response = db_client.table("notaries").upsert(payload, on_conflict="source_url").execute()
    return len(response.data or [])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("city", nargs="?", default="Stuttgart", help="City to search (default: Stuttgart).")
    parser.add_argument("--limit", type=int, default=10, help="Max candidates to discover.")
    parser.add_argument("--skip-persist", action="store_true", help="Run discovery and verification only.")
    parser.add_argument(
        "--env-file", type=Path, default=Path(".env"),
        help="Environment file (default: .env in the current directory).",
    )
    args = parser.parse_args()

    try:
        print(f"Phase 1: checking Supabase connectivity for '{args.city}' run...")
        db_client = phase1_connectivity(args.env_file)
        print("Phase 1 OK: public.notaries is reachable.")
    except ConfigurationError as exc:
        print(f"Phase 1 failed: configuration error: {exc}", file=sys.stderr)
        return 1
    except PostgrestAPIError as exc:
        print(f"Phase 1 failed: Supabase rejected the query ({exc.code}).", file=sys.stderr)
        return 1

    try:
        print(f"Phase 2: discovering up to {args.limit} candidate(s) in {args.city}...")
        candidates = phase2_discovery(args.env_file, args.city, args.limit)
        print(f"Phase 2 OK: {len(candidates)} candidate(s) discovered.")
    except ConfigurationError as exc:
        print(f"Phase 2 failed: configuration error: {exc}", file=sys.stderr)
        return 1
    except (ValidationError, GroqAPIError) as exc:
        print(f"Phase 2 failed: {exc}", file=sys.stderr)
        return 1
    if not candidates:
        print("No candidates discovered; stopping before verification.")
        return 0

    try:
        print(f"Phase 3: verifying {len(candidates)} candidate(s)...")
        verified = phase3_verification(args.env_file, candidates)
        relevant = sum(1 for c in verified if c.status == CandidateStatus.RELEVANT)
        print(f"Phase 3 OK: {relevant} of {len(verified)} candidate(s) look relevant.")
    except ConfigurationError as exc:
        print(f"Phase 3 failed: configuration error: {exc}", file=sys.stderr)
        return 1
    except GroqAPIError as exc:
        print(f"Phase 3 failed: {exc}", file=sys.stderr)
        return 1

    if args.skip_persist:
        print("Skipping persistence (--skip-persist).")
        return 0

    try:
        print("Phase 4: persisting verified candidates...")
        inserted = phase4_persist(db_client, verified)
        print(f"Phase 4 OK: {inserted} row(s) upserted into public.notaries.")
    except PostgrestAPIError as exc:
        print(f"Phase 4 failed: Supabase rejected the upsert ({exc.code}).", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())