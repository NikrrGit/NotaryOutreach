"""Local job and draft review operations for the UI and CLI."""

from __future__ import annotations

from typing import Any, Literal

from storage.sqlite import SQLiteStorage


class OutreachService:
    """Manage saved results; workflow execution is connected separately."""

    def __init__(self, storage: SQLiteStorage | None = None) -> None:
        self.storage = storage if storage is not None else SQLiteStorage()

    def create_job(
        self, *, target_type: Literal["notary", "vc"], location: str,
        target_count: int = 10, company_type: str | None = None,
        startup_description: str | None = None, industry: str | None = None,
        funding_stage: str | None = None, job_id: str | None = None,
    ) -> str:
        """Validate search settings and save a pending job; return its ID."""
        if target_type not in ("notary", "vc"):
            raise ValueError("target_type must be notary or vc.")
        if type(target_count) is not int or not 1 <= target_count <= 100:
            raise ValueError("target_count must be an integer from 1 to 100.")
        record: dict[str, Any] = {"target_type": target_type, "target_count": target_count}
        for field, value in {
            "location": location, "company_type": company_type,
            "startup_description": startup_description, "industry": industry,
            "funding_stage": funding_stage,
        }.items():
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{field} must be nonempty text.")
            record[field] = value.strip() if value is not None else None
        if record["location"] is None:
            raise ValueError("location is required.")
        if target_type == "notary":
            if record["company_type"] not in ("UG", "GmbH"):
                raise ValueError("Notary searches require UG or GmbH.")
            if any(record[field] is not None for field in ("startup_description", "industry", "funding_stage")):
                raise ValueError("Startup settings apply only to VC searches.")
        elif record["company_type"] is not None or any(
            record[field] is None for field in ("startup_description", "industry", "funding_stage")
        ):
            raise ValueError("VC searches require startup description, industry and funding stage, without company type.")
        if job_id is not None:
            record["id"] = job_id
        return self.storage.save_record("jobs", record)

    def load_job(self, job_id: str) -> dict[str, Any]:
        """Load a saved job or raise KeyError."""
        job = self.storage.get_record("jobs", job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def list_jobs(self) -> list[dict[str, Any]]:
        """List saved jobs, newest first."""
        return list(reversed(self.storage.list_records("jobs")))

    def load_results(self, job_id: str) -> dict[str, Any]:
        """Load a job and its evidence, draft, evaluation and review history."""
        results = {"job": self.load_job(job_id)}
        for table in ("candidates", "verifications", "drafts", "evaluations", "reviews"):
            results[table] = self.storage.list_records(table, job_id=job_id)
        return results
