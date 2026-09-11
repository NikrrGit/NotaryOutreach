"""Authenticate with Supabase and test the Lovable database connection."""

import argparse
import os
import sys
from pathlib import Path

from dotenv import dotenv_values
from postgrest.exceptions import APIError
from supabase import Client, create_client
from supabase.client import ClientOptions

from .config import ConfigurationError, Settings, load_config

TEST_ID = "cb46ccad-fb67-4a26-b38e-b29ac039530d"
TEST_NAME = "[TEST] Local Python connection"

EXPECTED_COLUMNS = (
    "id", "name", "city", "website", "email", "phone", "source_url",
    "personalised_email", "status", "created_at",
)

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


def connect(settings: Settings, env_file: str | Path = ".env") -> Client:
    values = {**dotenv_values(env_file), **os.environ}
    email = (values.get("SUPABASE_EMAIL") or "").strip()
    password = values.get("SUPABASE_PASSWORD")
    if not email or not password:
        raise ConfigurationError("Set SUPABASE_EMAIL and SUPABASE_PASSWORD for your dashboard account in .env.")
    client = create_client(
        settings.supabase_url,
        settings.supabase_key,
        options=ClientOptions(
            schema="public",
            postgrest_client_timeout=15,
            auto_refresh_token=False,
            persist_session=False,
        ),
    )
    try:
        response = client.auth.sign_in_with_password({"email": email, "password": password})
    except Exception as exc:
        raise ConfigurationError("Supabase sign-in failed. Check dashboard credentials, email confirmation, and network.") from exc
    if response.session is None:
        raise ConfigurationError("Supabase sign-in returned no session.")
    return client


def inspect_notaries(client: Client) -> list[str] | None:
    response = client.table("notaries").select("*").limit(1).execute()
    return sorted(response.data[0]) if response.data else None


def insert_test_notary(client: Client) -> str:
    rows = client.table("notaries").select("id").eq("id", TEST_ID).execute().data
    if not rows:
        client.table("notaries").insert({
            "id": TEST_ID,
            "name": TEST_NAME,
            "city": "Stuttgart",
            "personalised_email": "Technischer Verbindungstest. Keine echte Anfrage; bitte nicht versenden.",
            "status": "pending",
        }).execute()
    rows = client.table("notaries").select("id").eq("id", TEST_ID).execute().data
    if not rows:
        raise RuntimeError("Test record could not be read back.")
    return str(rows[0]["id"])


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
    parser.add_argument("--test-insert", action="store_true", help="Create or reuse one labelled test record and read it back.")
    args = parser.parse_args()
    if args.schema_sql and args.test_insert:
        parser.error("Choose either --schema-sql or --test-insert.")
    if args.schema_sql:
        print(SCHEMA_SQL)
        return 0

    try:
        client = connect(load_config(args.env_file), args.env_file)
        columns = inspect_notaries(client)
        if args.test_insert:
            record_id = insert_test_notary(client)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    except APIError as exc:
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

    print("Authenticated Supabase connection OK: public.notaries SELECT succeeded.")
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
    if args.test_insert:
        print(f"Test record ready and readable: {record_id}")
        print(f"Refresh the Lovable dashboard and look for: {TEST_NAME}")
    else:
        print("No data changed. Run with --test-insert to test dashboard integration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
