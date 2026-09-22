"""Local job and draft review operations for the UI and CLI."""

from __future__ import annotations

from typing import Any, Literal

from storage.sqlite import SQLiteStorage


class OutreachService:
    """Manage saved results; workflow execution is connected separately."""

    def __init__(self, storage: SQLiteStorage | None = None) -> None:
        self.storage = storage if storage is not None else SQLiteStorage()
