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
