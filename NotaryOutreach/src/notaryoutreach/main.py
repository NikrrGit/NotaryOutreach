"""Run one outreach stage at a time and output candidates as JSON."""

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import ConfigurationError, load_config
from .database import SCHEMA_SQL, connect, inspect_notaries
from .discovery import build_client, discover_notaries
from .email_generator import generate_emails
from .models import Candidate, CandidateStatus, ValidationError
from .utils import dedupe_candidates
from .verification import verify_candidates


def read_candidates(path: Path | None) -> list[Candidate]:
    try:
        raw = path.read_text(encoding="utf-8") if path else sys.stdin.read()
        items = json.loads(raw)
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValidationError("Expected an array of candidate objects.")
        return [Candidate.from_dict(item) for item in items]
    except (ValueError, TypeError, OSError) as exc:
        raise ValidationError("Supply a valid UTF-8 JSON array of candidate objects.") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage", choices=("connect", "discover", "dedupe", "verify", "email"),
        default="connect", help="Stage to run (default: connect).",
    )
    parser.add_argument("city", nargs="?", default="Stuttgart")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--input", type=Path, help="Candidate JSON file (default: stdin).")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--schema-sql", action="store_true", help="Print read-only schema inspection SQL.")
    args = parser.parse_args()
    if args.limit < 1 or not args.city.strip():
        parser.error("City must be nonempty and --limit must be positive.")
    if args.input and args.stage in {"connect", "discover"}:
        parser.error("--input is only used by dedupe, verify, and email.")
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.ERROR)
    if args.schema_sql:
        print(SCHEMA_SQL)
        return 0

    try:
        if args.stage == "connect":
            columns = inspect_notaries(connect(load_config(args.env_file)))
            print("Supabase connection OK: public.notaries SELECT succeeded.")
            if columns is None:
                print("No rows visible: the table may be empty or filtered by RLS.")
            else:
                print("Existing columns: " + ", ".join(columns))
            print("No data changed. Schema, insert permissions, and Lovable visibility still need checking.")
            return 0

        if args.stage == "discover":
            with build_client(args.env_file) as client:
                results = discover_notaries(client, args.city, args.limit)
        else:
            candidates = read_candidates(args.input)
            if args.stage == "dedupe":
                results = dedupe_candidates(candidates)
            elif not candidates:
                results = []
            else:
                with build_client(args.env_file) as client:
                    results = (
                        verify_candidates(client, candidates) if args.stage == "verify"
                        else generate_emails(client, candidates)
                    )
    except (ConfigurationError, ValidationError) as exc:
        print(f"Stage {args.stage} failed: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print(f"Stage {args.stage} failed. Check credentials, service access, and network.", file=sys.stderr)
        return 1

    print(json.dumps([candidate.to_dict() for candidate in results], indent=2, ensure_ascii=False))
    errors = sum(candidate.status == CandidateStatus.ERROR for candidate in results)
    print(f"Stage {args.stage}: {len(results)} candidate(s); {errors} error(s).", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
