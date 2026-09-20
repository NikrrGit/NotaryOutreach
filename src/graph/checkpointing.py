"""Durable, local SQLite checkpoints for synchronous workflow runs.

Keep the context open while invoking or streaming the compiled graph. Reopen
its database after a restart and resume with the same thread ID and None input.
An interrupted node can run again; external effects must be idempotent.
"""

from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

from agents.discovery import Candidate
from agents.email_writer import EmailDraft
from agents.verifier import VerificationFailure, VerificationResult

from .state import CandidateEmailDraft, EvaluationResult, SearchSettings, WorkflowError

DEFAULT_CHECKPOINT_PATH = Path("runs/checkpoints.sqlite3")


@contextmanager
def open_checkpointer(
    path: str | Path = DEFAULT_CHECKPOINT_PATH,
) -> Iterator[SqliteSaver]:
    """Open a file-backed saver, creating parent directories as necessary.

    The connection is closed even if execution fails. In-memory databases and
    SQLite URI strings are rejected so callers cannot accidentally lose jobs
    on process exit. Use a persistent filesystem and one active run per thread.
    """
    raw_path = str(path)
    if not raw_path.strip() or raw_path == ":memory:" or raw_path.startswith("file:"):
        raise ValueError("Checkpoint path must name a persistent SQLite file.")
    database = Path(path).expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    serde = JsonPlusSerializer(allowed_msgpack_modules=[
        Candidate, EmailDraft, VerificationFailure, VerificationResult,
        CandidateEmailDraft, EvaluationResult, SearchSettings, WorkflowError,
    ])
    with closing(sqlite3.connect(
        str(database), timeout=30, check_same_thread=False,
    )) as connection:
        connection.execute("PRAGMA synchronous=FULL")
        yield SqliteSaver(connection, serde=serde)


def checkpoint_config(thread_id: str) -> RunnableConfig:
    """Use a stable job ID for both the initial invocation and every resume."""
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError("thread_id must be a non-empty string.")
    return {"configurable": {"thread_id": thread_id}}
