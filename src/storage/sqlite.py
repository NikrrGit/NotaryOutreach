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
