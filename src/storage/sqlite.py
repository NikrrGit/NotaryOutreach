"""Local persistence shared by Notary and VC outreach.

Use save_record(table, record) for all six record types. Supply stable IDs when
replaying workflow output: an identical replay is a no-op, a changed payload
with the same ID is an error. Drafts, evaluations and reviews are append-only;
edits and new review decisions need new IDs. Only job status is mutable.

Records use the SQL column names. metadata_json and issues_json accept Python
dict/list values on write and are decoded on read. This module has no agent,
Groq, Streamlit or Supabase dependencies.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import uuid4


_FIELDS = {
    "jobs": {"id", "target_type", "location", "target_count", "company_type",
             "startup_description", "industry", "funding_stage", "status"},
    "candidates": {"id", "job_id", "target_type", "name", "organization",
                   "city", "website", "email", "phone", "source_url", "metadata_json"},
    "verifications": {"id", "candidate_id", "eligible", "confidence", "reason",
                      "evidence", "source_url"},
    "drafts": {"id", "candidate_id", "subject", "body"},
    "evaluations": {"id", "draft_id", "passed", "score", "reasoning", "issues_json"},
    "reviews": {"id", "draft_id", "decision", "final_subject", "final_body"},
}
_JSON_FIELDS = {"metadata_json": dict, "issues_json": list}


class SQLiteStorage:
    """File-backed storage; each operation opens and closes its own connection."""

    def __init__(self, path: str | Path = "data/outreach.db") -> None:
        """Create the parent directory and apply the initial schema once.

        Relative paths resolve against the current working directory. In-memory
        databases are unsupported because operations use separate connections.
        """
        if str(path) == ":memory:":
            raise ValueError("Use a file path, not an in-memory database.")
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        migration = Path(__file__).resolve().parents[1] / "db/migrations/001_initial.sql"
        with closing(sqlite3.connect(self.path, timeout=30)) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                connection.executescript(migration.read_text(encoding="utf-8"))
            elif version != 1:
                raise ValueError(f"Unsupported database schema version: {version}")

    def _connect(self) -> sqlite3.Connection:
        """Open a connection with foreign keys enforced and named columns."""
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        """Return a detached record with JSON and tri-state booleans decoded."""
        record = dict(row)
        for field in _JSON_FIELDS:
            if field in record:
                record[field] = json.loads(record[field])
        for field in ("eligible", "passed"):
            if field in record and record[field] is not None:
                record[field] = bool(record[field])
        return record

    def save_record(self, table: str, record: dict[str, Any]) -> str:
        """Insert a job, candidate, verification, draft, evaluation or review.

        Returns the supplied ID, or a generated UUID for a new record. Reuse
        that ID for retries. Conflicting replays raise ValueError without
        changing existing data. Required fields and relationships are enforced
        by SQLite. Each write is atomic; related parent records must exist.
        """
        if table not in _FIELDS:
            raise ValueError(f"Unknown table: {table}")
        values = dict(record)
        if values.keys() - _FIELDS[table]:
            raise ValueError("Unknown or read-only record fields.")
        values.setdefault("id", str(uuid4()))
        if not isinstance(values["id"], str) or not values["id"].strip():
            raise ValueError("Record ID must be a nonempty string.")
        for field, expected in _JSON_FIELDS.items():
            if field in values:
                value = values[field]
                if isinstance(value, str):
                    value = json.loads(value)
                if not isinstance(value, expected):
                    raise ValueError(f"{field} must contain a JSON {expected.__name__}.")
                values[field] = json.dumps(value, sort_keys=True, allow_nan=False)
        for field in ("eligible", "passed"):
            if field in values and values[field] is not None:
                if type(values[field]) not in (bool, int) or values[field] not in (0, 1):
                    raise ValueError(f"{field} must be a boolean.")
        if "target_count" in values and type(values["target_count"]) is not int:
            raise ValueError("target_count must be an integer.")
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        # Identifiers above come exclusively from the allowlist; all caller
        # values are bound parameters. BEGIN IMMEDIATE serializes replay checks.
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                f"SELECT * FROM {table} WHERE id = ?", (values["id"],),
            ).fetchone()
            if existing is not None:
                for field, value in values.items():
                    stored = existing[field]
                    if field in _JSON_FIELDS:
                        stored, value = json.loads(stored), json.loads(value)
                    if stored != value:
                        raise ValueError(f"Conflicting replay for {table} record {values['id']}.")
            else:
                connection.execute(
                    f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                    tuple(values.values()),
                )
        return values["id"]

    def update_job(self, job_id: str, *, status: str) -> None:
        """Update job status and timestamp; missing jobs raise KeyError.

        Search settings are immutable: use a new job for a different search.
        This records lifecycle state, not a lock or a workflow resume operation.
        """
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "UPDATE jobs SET status = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                (status, job_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(job_id)

    def get_record(self, table: str, record_id: str) -> dict[str, Any] | None:
        """Load a single record, returning None when the ID does not exist."""
        if table not in _FIELDS:
            raise ValueError(f"Unknown table: {table}")
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"SELECT * FROM {table} WHERE id = ?", (record_id,),
            ).fetchone()
            return None if row is None else self._decode(row)
