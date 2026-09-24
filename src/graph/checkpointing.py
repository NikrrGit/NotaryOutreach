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
from agents.verification import VerificationFailure, VerificationResult

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
        ("agents.verifier", "VerificationFailure"),
        ("agents.verifier", "VerificationResult"),
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


def start_job(graph, *, thread_id: str, initial_state: dict) -> dict:
    """Start a new job and flush each checkpoint before advancing.

    Reject reused IDs: submitting initial state twice would append duplicate
    records to the graph's history channels. The caller must serialize runs
    for a given thread; this existence check is not a distributed lock.
    """
    config = checkpoint_config(thread_id)
    snapshot = graph.get_state(config)
    if snapshot.created_at is not None:
        raise ValueError("This thread already exists; use resume_job instead.")
    return graph.invoke(initial_state, config, durability="sync")


def resume_job(graph, *, thread_id: str) -> dict:
    """Resume the latest saved checkpoint without re-submitting initial state.

    Rebuild the graph with the same agents and database after a restart. A
    completed job returns its stored state without replaying any agent calls.
    This helper handles failed/statically interrupted runs, not dynamic
    human-input interrupts that require LangGraph's Command(resume=...).
    """
    config = checkpoint_config(thread_id)
    snapshot = graph.get_state(config)
    if snapshot.created_at is None:
        raise ValueError("No checkpoint exists for this thread.")
    if not snapshot.next:
        return snapshot.values
    return graph.invoke(None, config, durability="sync")
