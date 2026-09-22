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

    def edit_email(
        self, job_id: str, draft_id: str, *, subject: str, body: str,
        new_draft_id: str | None = None,
    ) -> str:
        """Save a new draft version requiring fresh evaluation and approval."""
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("subject must be nonempty text.")
        if not isinstance(body, str) or not body.strip():
            raise ValueError("body must be nonempty text.")
        results = self.load_results(job_id)
        original = next((draft for draft in results["drafts"] if draft["id"] == draft_id), None)
        if original is None:
            raise KeyError(draft_id)
        if new_draft_id == draft_id:
            raise ValueError("An edit requires a new draft ID.")
        record = {
            "candidate_id": original["candidate_id"],
            "subject": subject.strip(), "body": body.strip(),
        }
        if new_draft_id is not None:
            record["id"] = new_draft_id
        return self.storage.save_record("drafts", record)

    def _review_draft(
        self, job_id: str, draft_id: str, *, decision: Literal["approved", "rejected"],
        review_id: str | None = None,
    ) -> str:
        """Append a review of the exact saved draft text."""
        if decision not in ("approved", "rejected"):
            raise ValueError("Review decision must be approved or rejected.")
        results = self.load_results(job_id)
        draft = next((item for item in results["drafts"] if item["id"] == draft_id), None)
        if draft is None:
            raise KeyError(draft_id)
        if decision == "approved":
            evaluations = [item for item in results["evaluations"] if item["draft_id"] == draft_id]
            if not evaluations or not evaluations[-1]["passed"]:
                raise ValueError("Approval requires a passing evaluation for this draft version.")
        record = {
            "draft_id": draft_id, "decision": decision,
            "final_subject": draft["subject"], "final_body": draft["body"],
        }
        if review_id is not None:
            record["id"] = review_id
        return self.storage.save_record("reviews", record)

    def approve_draft(self, job_id: str, draft_id: str, *, review_id: str | None = None) -> str:
        """Record human approval; no email is sent."""
        return self._review_draft(job_id, draft_id, decision="approved", review_id=review_id)
