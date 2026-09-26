from __future__ import annotations

from typing import Any, Literal, Mapping


Route = Literal["continue", "retry", "manual_review"]

DEFAULT_MAX_RETRIES = 2


def _get_values(obj: Any, key: str, default: Any = None) -> Any:
    """
    Read a value from either:
    - a dictionary / TypedDict
    - a Pydantic model
    - a normal Python object

    This keeps routing independent of the exact state implementation.
    """

    if obj is None:
        return default

    if isinstance(obj, Mapping):
        return obj.get(key, default)

    return getattr(obj, key, default)


def _retry_count(state: Any, stage: str) -> int:
    """
    Read how many times a specific workflow stage has already retried.

    Expected state shape:

        retry_counts = {
            "verification": 1,
            "email_generation": 0,
            "evaluation": 0,
        }
    """
    retry_counts = _get_values(state, "retry_counts", {})

    if not isinstance(retry_counts, Mapping):
        return 0
    return int(retry_counts.get(stage, 0))


def _retry_or_manual_review(
    state: Any,
    stage: str,
    max_retries: int,
) -> Route:
    """
    Retry while budget remains.

    Once the retry limit is reached, stop autonomous execution and
    send the item to manual review.
    """
    if _retry_count(state, stage) < max_retries:
        return "retry"

    return "manual_review"


def verification_resolved(result: Any) -> bool:
    """Require evidence for both positive and negative conclusions."""
    from agents.discovery import Candidate
    from agents.email_writer import EmailWriterInput

    try:
        if result.status == "unsupported":
            if not result.reasoning.strip() or not result.evidence_quote or not result.evidence_quote.strip():
                return False
            if not result.source_url:
                return False
            Candidate.validate_url(result.source_url)
            return True
        EmailWriterInput.from_verification(result)
    except (AttributeError, TypeError, ValueError):
        return False
    return True


def passing_drafts(state: Any) -> dict:
    """Keep only the latest draft with a passing evaluation and matching evidence."""
    from .nodes import _verifications, candidate_key

    verifications = _verifications(state)
    evaluations = {item.draft_id: item for item in state.get("evaluations", [])}
    latest = {candidate_key(item.candidate): item for item in state.get("email_drafts", [])}
    return {
        key: draft for key, draft in latest.items()
        if key in verifications and verifications[key].candidate == draft.candidate
        and verifications[key].status == "supported" and verification_resolved(verifications[key])
        and (result := evaluations.get(draft.draft_id)) is not None
        and result.passed and not result.issues
    }


def route_after_verification(state: Any, max_retries: int = DEFAULT_MAX_RETRIES) -> Route:
    """Continue a resolved batch; retry unknown or missing results."""
    if _get_values(state, "settings") is not None:
        from .nodes import _verifications, candidate_key

        results = _verifications(state)
        candidates = state.get("candidates", [])
        if candidates and all(
            (result := results.get(candidate_key(candidate))) is not None
            and verification_resolved(result) for candidate in candidates
        ):
            return "continue"
    else:
        verification = _get_values(state, "verification")
        if _get_values(verification, "status") is not None:
            if verification_resolved(verification):
                return "continue"
        else:
            supported = _get_values(verification, "supported", _get_values(verification, "eligible"))
            if supported is True and _get_values(verification, "evidence") and _get_values(verification, "source_url"):
                return "continue"
    return _retry_or_manual_review(state, "verification", max_retries)


def route_after_evaluation(state: Any, max_retries: int = DEFAULT_MAX_RETRIES) -> Route:
    """Route batch results or a target-specific standalone assessment."""
    if _get_values(state, "settings") is not None:
        from .nodes import _verifications

        required = {key for key, result in _verifications(state).items() if result.status == "supported"}
        if not required:
            return "manual_review"
        if required <= passing_drafts(state).keys():
            return "continue"
    else:
        evaluation = _get_values(state, "evaluation")
        target = _get_values(state, "target_type", "notary")
        checks = ("appointment_requested", "correct_company_type") if target == "notary" else (
            "conversation_requested", "startup_represented_correctly", "investment_fit_supported",
        )
        if target in ("notary", "vc") and all(
            _get_values(evaluation, field) is True for field in ("passed", "claims_supported", *checks)
        ) and not _get_values(evaluation, "issues", []):
            return "continue"
    return _retry_or_manual_review(state, "email_generation", max_retries)


def route_on_error(
    state: Any,
    stage: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> Route:
    """
    Generic routing for recoverable workflow errors.

    Examples:
        Groq timeout
        malformed structured output
        temporary website failure

    The node handling the retry is responsible for incrementing
    retry_counts[stage].
    """
    return _retry_or_manual_review(
        state,
        stage=stage,
        max_retries=max_retries,
    )
