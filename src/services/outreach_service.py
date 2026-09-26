"""Local job and draft review operations for the UI and CLI."""

from __future__ import annotations

from typing import Any, Literal
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from uuid import NAMESPACE_URL, uuid5

from dotenv import dotenv_values
import os

from agents.discovery import Candidate, DiscoveryAgent, OutreachContext
from agents.email_writer import EmailDraft, EmailWriter, EmailWriterInput
from agents.evaluator import EmailEvaluator
from agents.verification import VerificationAgent, VerificationResult
from graph.checkpointing import checkpoint_config, open_checkpointer
from graph.nodes import candidate_key
from graph.state import CandidateEmailDraft, EvaluationResult
from graph.workflow import build_workflow, create_initial_state

from storage.sqlite import SQLiteStorage


_EXECUTION_LOCK = Lock()


class OutreachService:
    """Run local searches and manage draft review without sending emails."""

    def __init__(
        self, storage: SQLiteStorage | None = None, *,
        checkpoint_path: str | Path = "runs/checkpoints.sqlite3",
        discovery_agent=None, verification_agent=None, email_writer=None, evaluator=None,
        env_file: str | Path = ".env",
    ) -> None:
        self.storage = storage if storage is not None else SQLiteStorage()
        self.checkpoint_path = Path(checkpoint_path)
        self.env_file = Path(env_file)
        self.agents = (discovery_agent, verification_agent, email_writer, evaluator)

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
        results["workflow_errors"] = []
        results["verification_history"] = []
        if self.checkpoint_path.is_file():
            thread_id = str(uuid5(NAMESPACE_URL, f"{self.storage.path}:{job_id}"))
            with open_checkpointer(self.checkpoint_path) as saver:
                checkpoint = saver.get_tuple(checkpoint_config(thread_id))
            if checkpoint is not None:
                state = checkpoint.checkpoint["channel_values"]
                results["workflow_errors"] = [item.model_dump() for item in state.get("errors", [])]
                results["verification_history"] = [item.model_dump() for item in state.get("verification_results", [])]
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

    def reject_draft(self, job_id: str, draft_id: str, *, review_id: str | None = None) -> str:
        """Record rejection while preserving earlier reviews."""
        return self._review_draft(job_id, draft_id, decision="rejected", review_id=review_id)

    @contextmanager
    def _agent_session(self, *, evaluation_only: bool = False):
        """Create missing agents lazily and close owned Groq clients."""
        discovery, verifier, writer, evaluator = self.agents
        provider = None
        try:
            if evaluator is None or (not evaluation_only and writer is None):
                from providers.groq import GroqProvider

                values = {**dotenv_values(self.env_file), **os.environ}
                provider = GroqProvider(api_key=values.get("GROQ_API_KEY"))
            if evaluator is None:
                evaluator = EmailEvaluator(provider)
            if not evaluation_only:
                discovery = discovery if discovery is not None else DiscoveryAgent(env_file=str(self.env_file))
                verifier = verifier if verifier is not None else VerificationAgent(env_file=str(self.env_file))
                writer = writer if writer is not None else EmailWriter(provider)
            yield discovery, verifier, writer, evaluator
        finally:
            if provider is not None:
                provider.client.close()

    def _persist_state(self, job_id: str, state: dict) -> None:
        """Project checkpoint history into replay-safe application records."""
        candidate_ids = {}
        for candidate in state.get("candidates", []):
            key = candidate_key(candidate)
            record_id = str(uuid5(NAMESPACE_URL, f"{job_id}:candidate:{key}"))
            candidate_ids[key] = record_id
            self.storage.save_record("candidates", {
                "id": record_id, "job_id": job_id, "target_type": candidate.target_type,
                "name": candidate.name, "organization": candidate.organization, "city": candidate.city,
                "website": candidate.website, "email": candidate.email, "phone": candidate.phone,
                "source_url": candidate.source_url,
                "metadata_json": {**candidate.metadata, "_agent_record": candidate.model_dump(mode="json")},
            })
        for index, verification in enumerate(state.get("verification_results", [])):
            self.storage.save_record("verifications", {
                "id": str(uuid5(NAMESPACE_URL, f"{job_id}:verification:{index}")),
                "candidate_id": candidate_ids[candidate_key(verification.candidate)],
                "eligible": {"supported": True, "unsupported": False, "unknown": None}[verification.status],
                "confidence": verification.confidence, "reason": verification.reasoning,
                "evidence": verification.evidence_quote, "source_url": verification.source_url,
            })
        for draft in state.get("email_drafts", []):
            self.storage.save_record("drafts", {
                "id": draft.draft_id, "candidate_id": candidate_ids[candidate_key(draft.candidate)],
                "subject": draft.draft.subject, "body": draft.draft.body,
            })
        for index, evaluation in enumerate(state.get("evaluations", [])):
            self.storage.save_record("evaluations", {
                "id": str(uuid5(NAMESPACE_URL, f"{job_id}:evaluation:{index}")),
                "draft_id": evaluation.draft_id, "passed": evaluation.passed and not evaluation.issues,
                "score": getattr(evaluation, "score", None),
                "reasoning": evaluation.reasoning, "issues_json": evaluation.issues,
            })
        status = state.get("status", "pending")
        status = "running" if status == "pending" else status
        if self.load_job(job_id)["status"] != status:
            self.storage.update_job(job_id, status=status)

    def _execute(self, job_id: str, *, resume: bool) -> dict[str, Any]:
        """Run one job at a time in this process, checkpointing each graph step."""
        job = self.load_job(job_id)
        if not _EXECUTION_LOCK.acquire(blocking=False):
            raise RuntimeError("Another workflow or evaluation is already running.")
        try:
            thread_id = str(uuid5(NAMESPACE_URL, f"{self.storage.path}:{job_id}"))
            config = checkpoint_config(thread_id)
            with open_checkpointer(self.checkpoint_path) as saver:
                reader = build_workflow(discovery_agent=None, verification_agent=None,
                                        email_writer=None, evaluator=None, checkpointer=saver)
                snapshot = reader.get_state(config)
                exists = snapshot.created_at is not None
                if resume and not exists:
                    raise ValueError("No checkpoint exists; start this job with run_job.")
                if not resume and exists:
                    raise ValueError("This job already has a checkpoint; use resume_job.")
                initial = create_initial_state(
                    **{field: job[field] for field in OutreachContext.model_fields},
                    target_results=job["target_count"],
                )
                if exists and snapshot.values["settings"] != initial["settings"]:
                    raise ValueError("Checkpoint settings do not match the saved job.")
                try:
                    if exists:
                        self._persist_state(job_id, snapshot.values)
                        if not snapshot.next:
                            return self.load_results(job_id)
                    self.storage.update_job(job_id, status="running")
                    with self._agent_session() as (discovery, verifier, writer, evaluator):
                        graph = build_workflow(discovery_agent=discovery, verification_agent=verifier,
                                               email_writer=writer, evaluator=evaluator, checkpointer=saver)
                        for state in graph.stream(None if resume else initial, config,
                                                  stream_mode="values", durability="sync"):
                            self._persist_state(job_id, state)
                except Exception:
                    self.storage.update_job(job_id, status="failed")
                    raise
            return self.load_results(job_id)
        finally:
            _EXECUTION_LOCK.release()

    def run_job(self, job_id: str) -> dict[str, Any]:
        """Start a saved search and persist each completed graph step."""
        return self._execute(job_id, resume=False)

    def resume_job(self, job_id: str) -> dict[str, Any]:
        """Resume an existing checkpoint or reload its completed results."""
        return self._execute(job_id, resume=True)

    def evaluate_draft(self, job_id: str, draft_id: str, *, evaluation_id: str | None = None) -> str:
        """Evaluate an exact saved version against its latest verified evidence."""
        if not _EXECUTION_LOCK.acquire(blocking=False):
            raise RuntimeError("Another workflow or evaluation is already running.")
        try:
            results = self.load_results(job_id)
            draft = next((item for item in results["drafts"] if item["id"] == draft_id), None)
            if draft is None:
                raise KeyError(draft_id)
            if evaluation_id is not None:
                existing = self.storage.get_record("evaluations", evaluation_id)
                if existing is not None:
                    if existing["draft_id"] != draft_id:
                        raise ValueError("Evaluation ID belongs to a different draft.")
                    return evaluation_id
            stored_candidate = next(item for item in results["candidates"] if item["id"] == draft["candidate_id"])
            raw_candidate = stored_candidate["metadata_json"].get("_agent_record")
            if raw_candidate is None:
                raw_candidate = {field: stored_candidate[field] for field in (
                    "id", "target_type", "name", "organization", "city", "website", "email", "phone", "source_url",
                )}
                raw_candidate["metadata"] = stored_candidate["metadata_json"]
            candidate = Candidate.model_validate(raw_candidate)
            verifications = [item for item in results["verifications"] if item["candidate_id"] == draft["candidate_id"]]
            if not verifications or verifications[-1]["eligible"] is not True:
                raise ValueError("A supported verification is required to evaluate this draft.")
            evidence = verifications[-1]
            verification = VerificationResult(
                candidate=candidate, **{field: results["job"][field] for field in OutreachContext.model_fields},
                status="supported", confidence=evidence["confidence"], reasoning=evidence["reason"],
                evidence_quote=evidence["evidence"], source_url=evidence["source_url"],
            )
            EmailWriterInput.from_verification(verification)
            record = CandidateEmailDraft(draft_id=draft_id, candidate=candidate,
                                         draft=EmailDraft(subject=draft["subject"], body=draft["body"]))
            with self._agent_session(evaluation_only=True) as (_, _, _, evaluator):
                assessment = evaluator(record, verification)
            if isinstance(assessment, EvaluationResult):
                assessment = assessment.model_dump()
            assessment = EvaluationResult.model_validate(assessment)
            if assessment.draft_id != draft_id:
                raise ValueError("Evaluation references a different draft.")
            payload = {
                "draft_id": draft_id, "passed": assessment.passed and not assessment.issues,
                "score": assessment.score, "reasoning": assessment.reasoning, "issues_json": assessment.issues,
            }
            if evaluation_id is not None:
                payload["id"] = evaluation_id
            return self.storage.save_record("evaluations", payload)
        finally:
            _EXECUTION_LOCK.release()
