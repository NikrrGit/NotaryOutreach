"""Read-only Supabase connection check for Phase 1."""

import argparse
import sys
from pathlib import Path

from postgrest.exceptions import APIError
from supabase import Client, create_client
from supabase.client import ClientOptions

from .config import ConfigurationError, Settings, load_config


EXPECTED_COLUMNS = (
    "id", "name", "city", "website", "email", "phone", "source_url",
    "company_formation_supported", "confidence", "verification_reason",
    "personalised_email", "status", "created_at", "updated_at",
)

# Run in the Supabase SQL Editor: the Data API cannot inspect SQL constraints.
SCHEMA_SQL = """-- Read-only inspection of the existing public.notaries table.
SELECT column_name, data_type, udt_name, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'notaries'
ORDER BY ordinal_position;

SELECT conname AS constraint_name, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = to_regclass('public.notaries');

SELECT c.relrowsecurity AS row_level_security_enabled
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = 'notaries';

SELECT policyname, roles, cmd, qual, with_check
FROM pg_policies
WHERE schemaname = 'public' AND tablename = 'notaries';
"""


def connect(settings: Settings) -> Client:
    """Create a client; a successful query is needed to confirm connectivity."""
    return create_client(
        settings.supabase_url,
        settings.supabase_key,
        options=ClientOptions(
            schema="public",
            postgrest_client_timeout=15,
            auto_refresh_token=False,
            persist_session=False,
        ),
    )


def inspect_notaries(client: Client) -> list[str] | None:
    """Return column names from at most one visible row, without printing data.

    None means no row was visible, which can mean an empty table or an RLS
    policy filtering results. This does not establish write permissions.
    """
    response = client.table("notaries").select("*").limit(1).execute()
    return sorted(response.data[0]) if response.data else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file", type=Path, default=Path(".env"),
        help="Environment file (default: .env in the current directory).",
    )
    parser.add_argument(
        "--schema-sql", action="store_true",
        help="Print read-only SQL to run in Supabase SQL Editor, then exit.",
    )
    args = parser.parse_args()
    if args.schema_sql:
        print(SCHEMA_SQL)
        return 0

    try:
        columns = inspect_notaries(connect(load_config(args.env_file)))
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    except APIError as exc:
        # Avoid printing response bodies, URLs, credentials, or record values.
        if exc.code in {"PGRST205", "42P01"}:
            message = "public.notaries was not found in the accessible API schema."
        elif exc.code in {"42501", "PGRST301", "PGRST302", "PGRST303"}:
            message = "Access failed. Check the API key, table grants, and RLS policies."
        else:
            message = "The Data API rejected the query. Check the project, key, and table access."
        print(f"Database check failed: {message}", file=sys.stderr)
        return 1
    except Exception:
        print(
            "Database check failed. Check the project URL, API key, network, "
            "and whether the Supabase project is running.",
            file=sys.stderr,
        )
        return 1

    print("Supabase connection OK: public.notaries SELECT succeeded.")
    if columns is None:
        print("No rows visible: the table may be empty or filtered by RLS.")
        print("Column names could not be inspected from a row.")
    else:
        print("Existing columns: " + ", ".join(columns))
        missing = sorted(set(EXPECTED_COLUMNS) - set(columns))
        if missing:
            print("Expected names not found (check for equivalent fields): " + ", ".join(missing))
        else:
            print("All expected column names are present.")
    print("Run with --schema-sql, then paste the printed SQL into Supabase SQL Editor.")
    print("No data changed. Insert permissions and Lovable visibility are not yet verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
