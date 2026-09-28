"""Run local Notary and VC searches through OutreachService."""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

from agents.discovery import OutreachContext
from services.outreach_service import OutreachService
from storage.sqlite import SQLiteStorage


def build_parser() -> argparse.ArgumentParser:
    """Build shared search and recovery commands."""
    paths = argparse.ArgumentParser(add_help=False)
    paths.add_argument("--env-file", type=Path, default=argparse.SUPPRESS, help="Environment file (default: .env).")
    paths.add_argument("--database", type=Path, default=argparse.SUPPRESS, help="Override DATABASE_PATH.")
    paths.add_argument("--checkpoint-path", type=Path, default=argparse.SUPPRESS, help="Override CHECKPOINT_PATH.")
    parser = argparse.ArgumentParser(description=__doc__, parents=[paths])
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search", parents=[paths], help="Create and run a search.")
    search.add_argument("--type", dest="target_type", choices=("notary", "vc"), required=True)
    search.add_argument("--location", required=True)
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--company-type", choices=("UG", "GmbH"))
    search.add_argument("--description", dest="startup_description")
    search.add_argument("--industry")
    search.add_argument("--stage", dest="funding_stage")
    for name, help_text in (
        ("run", "Start a saved job without a checkpoint."),
        ("resume", "Resume a checkpoint or reload completed results."),
        ("show", "Show saved results and review history."),
    ):
        command = commands.add_parser(name, parents=[paths], help=help_text)
        command.add_argument("job_id")
    commands.add_parser("jobs", parents=[paths], help="List saved searches.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Print service results as JSON; execution errors return exit code 1."""
    parser = build_parser()
    args = parser.parse_args(argv)
    context = None
    if args.command == "search":
        if not 1 <= args.limit <= 100:
            parser.error("--limit must be between 1 and 100.")
        try:
            context = OutreachContext(
                target_type=args.target_type, location=args.location, company_type=args.company_type,
                startup_description=args.startup_description, industry=args.industry, funding_stage=args.funding_stage,
            )
        except ValueError:
            parser.error("Supply a nonempty location; Notary requires --company-type, "
                         "VC requires --description, --industry and --stage. Do not mix target-specific options.")
    job_id = getattr(args, "job_id", None)
    try:
        env_file = getattr(args, "env_file", Path(".env"))
        settings = {**dotenv_values(env_file), **os.environ}
        storage = SQLiteStorage(getattr(args, "database", None) or settings.get("DATABASE_PATH") or "data/outreach.db")
        service = OutreachService(
            storage, env_file=env_file,
            checkpoint_path=getattr(args, "checkpoint_path", None) or settings.get("CHECKPOINT_PATH") or "runs/checkpoints.sqlite3",
        )
        if args.command == "search":
            job_id = service.create_job(**context.model_dump(), target_count=args.limit)
            print(f"Saved job: {job_id}", file=sys.stderr)
            result = service.run_job(job_id)
        elif args.command == "run":
            result = service.run_job(job_id)
        elif args.command == "resume":
            result = service.resume_job(job_id)
        elif args.command == "show":
            result = service.load_results(job_id)
        else:
            result = service.list_jobs()
    except KeyError:
        print("Job was not found in the selected local database.", file=sys.stderr)
        return 1
    except Exception:
        print("Command failed. Check your provider settings and API key, connection, and local storage. "
              "If another workflow is running, wait for it to finish.", file=sys.stderr)
        if job_id is not None:
            print(f"Job: {job_id}. Use show to inspect it; resume an existing checkpoint or run an unstarted job.", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
