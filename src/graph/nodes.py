"""Injectable workflow nodes; register their bound methods with the graph.

Nodes return state updates without mutating the input. Append-reduced fields
contain only new records. Routing and retry budgets belong to the graph caller.
The current writer supports German only; discovery does not enforce radius_km.

Supply an evaluation callable (such as EmailEvaluator) accepting a
CandidateEmailDraft and its VerificationResult and returning EvaluationResult.
No credentials are loaded or external clients created when this module imports.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agents.discovery import Candidate, DiscoveryAgent, DiscoveryError
from agents.email_writer import EmailWriter, EmailWriterInput
from agents.verifier import VerificationAgent, VerificationResult

from .state import CandidateEmailDraft, EvaluationResult, WorkflowError, WorkflowState

EvaluateDraft = Callable[[CandidateEmailDraft, VerificationResult], EvaluationResult]
SaveDraft = Callable[[CandidateEmailDraft, VerificationResult, EvaluationResult], None]


def candidate_key(candidate: Candidate) -> str:
    """Stable office reference without requiring a new field on Candidate."""
    return "|".join((candidate.name.casefold(), candidate.city.casefold(),
                     (candidate.website or candidate.source_url).rstrip("/")))


def _error(node: str, exc: Exception, candidate: Candidate | None = None) -> WorkflowError:
    # Exception messages may contain provider responses or credentials.
    return WorkflowError(
        node=node,
        message=f"{node} failed ({type(exc).__name__}).",
        candidate_id=candidate_key(candidate) if candidate is not None else None,
        retryable=isinstance(exc, (TimeoutError, ConnectionError)),
    )


def _verifications(state: WorkflowState) -> dict[str, VerificationResult]:
    """Use the latest result for each candidate and the requested company type."""
    company_type = state["settings"].company_type
    return {
        candidate_key(result.candidate): result
        for result in state.get("verification_results", [])
        if result.company_type == company_type
    }


class SupabaseDraftStore:
    """Save drafts through an already authenticated Supabase client.

    Uses the existing notaries schema. Draft IDs must be unique primary keys.
    Replaying a saved draft preserves its human review status. The schema stores
    subject and body together; full evaluations/evidence remain in graph state
    and require durable graph checkpoints to survive process restarts.
    """

    def __init__(self, client: Any) -> None:
        self.client = client

    def __call__(
        self,
        record: CandidateEmailDraft,
        verification: VerificationResult,
        evaluation: EvaluationResult,
    ) -> None:
        if evaluation.draft_id != record.draft_id or not evaluation.passed:
            raise ValueError("A passing evaluation for this draft is required.")
        if verification.candidate != record.candidate:
            raise ValueError("Verification belongs to a different candidate.")
        EmailWriterInput.from_verification(verification)
        table = self.client.table("notaries")
        if table.select("id").eq("id", record.draft_id).execute().data:
            return
        candidate = record.candidate
        payload = {
            "id": record.draft_id,
            "name": candidate.name,
            "city": candidate.city,
            "website": candidate.website,
            "email": candidate.email,
            "phone": candidate.phone,
            "source_url": verification.source_url,
            "personalised_email": f"Betreff: {record.draft.subject}\n\n{record.draft.body}",
            "status": "pending",
        }
        try:
            self.client.table("notaries").insert(payload).execute()
        except Exception:
            # A concurrent replay or a lost response may have already saved it.
            if not self.client.table("notaries").select("id").eq(
                "id", record.draft_id,
            ).execute().data:
                raise


class WorkflowNodes:
    """Bind agents and storage once, then register discover/verify/write_email/
    evaluate/persist as graph nodes. Storage must tolerate replay of a draft ID.

    Evaluation receives the exact draft and matching verification. It must check
    appointment intent, company type and evidence support before returning pass.
    """

    def __init__(
        self,
        *,
        discovery: DiscoveryAgent,
        verifier: VerificationAgent,
        writer: EmailWriter,
        evaluator: EvaluateDraft,
        persist_draft: SaveDraft,
        sender_name: str | None = None,
        company_name: str | None = None,
    ) -> None:
        self.discovery = discovery
        self.verifier = verifier
        self.writer = writer
        self.evaluator = evaluator
        self.persist_draft = persist_draft
        self.sender_name = sender_name
        self.company_name = company_name

    def discover(self, state: WorkflowState) -> dict[str, Any]:
        settings = state["settings"]
        existing = {candidate_key(c) for c in state.get("candidates", [])}
        remaining = max(0, settings.target_count - len(existing))
        if not remaining:
            return {}
        errors: list[WorkflowError] = []
        try:
            candidates = self.discovery.discover(
                settings.location, settings.company_type, limit=remaining,
            )
        except DiscoveryError as exc:
            candidates = exc.candidates
            errors.append(_error("discover", exc))
        except Exception as exc:
            return {"errors": [_error("discover", exc)]}
        added = []
        for candidate in candidates:
            key = candidate_key(candidate)
            if key not in existing:
                added.append(candidate)
                existing.add(key)
        return {"candidates": added, "errors": errors}

    def verify(self, state: WorkflowState) -> dict[str, Any]:
        existing = _verifications(state)
        results: list[VerificationResult] = []
        errors: list[WorkflowError] = []
        for candidate in state.get("candidates", []):
            key = candidate_key(candidate)
            if key in existing:
                continue
            try:
                result = self.verifier.verify(candidate, state["settings"].company_type)
                results.append(result)
                existing[key] = result
                errors.extend(WorkflowError(
                    node="verify", candidate_id=key,
                    message=f"Verification reported a {failure.stage} failure.",
                ) for failure in result.errors)
            except Exception as exc:
                errors.append(_error("verify", exc, candidate))
        return {"verification_results": results, "errors": errors}

    def write_email(self, state: WorkflowState) -> dict[str, Any]:
        if state["settings"].language != "de":
            return {"errors": [WorkflowError(
                node="write_email", message="The email writer currently supports German (de) only.",
            )]}
        existing = {candidate_key(d.candidate) for d in state.get("email_drafts", [])}
        drafts: list[CandidateEmailDraft] = []
        errors: list[WorkflowError] = []
        for key, verification in _verifications(state).items():
            if key in existing or verification.status != "supported":
                continue
            try:
                draft = self.writer.write_verified(
                    verification, sender_name=self.sender_name, company_name=self.company_name,
                )
                drafts.append(CandidateEmailDraft(candidate=verification.candidate, draft=draft))
                existing.add(key)
            except Exception as exc:
                errors.append(_error("write_email", exc, verification.candidate))
        return {"email_drafts": drafts, "errors": errors}

    def evaluate(self, state: WorkflowState) -> dict[str, Any]:
        verifications = _verifications(state)
        existing = {e.draft_id for e in state.get("evaluations", [])}
        evaluations: list[EvaluationResult] = []
        errors: list[WorkflowError] = []
        for record in state.get("email_drafts", []):
            if record.draft_id in existing:
                continue
            try:
                verification = verifications[candidate_key(record.candidate)]
                EmailWriterInput.from_verification(verification)
                result = self.evaluator(record, verification)
                result = EvaluationResult.model_validate(result)
                if result.draft_id != record.draft_id:
                    raise ValueError("Evaluation references a different draft.")
                evaluations.append(result)
                existing.add(record.draft_id)
            except Exception as exc:
                errors.append(_error("evaluate", exc, record.candidate))
        return {"evaluations": evaluations, "errors": errors}

    def persist(self, state: WorkflowState) -> dict[str, Any]:
        verifications = _verifications(state)
        evaluations = {e.draft_id: e for e in state.get("evaluations", [])}
        # Only the latest draft per candidate is eligible for persistence.
        drafts = {candidate_key(d.candidate): d for d in state.get("email_drafts", [])}
        errors: list[WorkflowError] = []
        for key, record in drafts.items():
            evaluation = evaluations.get(record.draft_id)
            if evaluation is None or not evaluation.passed:
                continue
            try:
                verification = verifications[key]
                EmailWriterInput.from_verification(verification)
                self.persist_draft(record, verification, evaluation)
            except Exception as exc:
                errors.append(_error("persist", exc, record.candidate))
        return {"errors": errors}
