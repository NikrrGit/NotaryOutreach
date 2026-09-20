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
       Retry how many times a specific workflow stage has already retried
       
       Expected state shape:

            retry_counts = {
            "verification": 1,
            "email": 0,
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

def route_after_verification(
    state: Any,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> Route:
    """
    Decide what happens after the verification agent.

    Continue only when:
        - verification says the candidate is supported/relevant
        - evidence exists
        - a source URL exists

    Retry incomplete verification.

    After the retry budget is exhausted, request manual review.
    """
    verification = _get_value(state, "verification")

    if verification is None:
        return _retry_or_manual_review(
            state,
            stage="verification",
            max_retries=max_retries,
        )

    supported = _get_value(
        verification,
        "supported",
        _get_value(verification, "eligible", False),
    )

    evidence = _get_value(verification, "evidence")
    source_url = _get_value(verification, "source_url")

    verification_complete = bool(
        supported
        and evidence
        and source_url
    )

    if verification_complete:
        return "continue"

    return _retry_or_manual_review(
        state,
        stage="verification",
        max_retries=max_retries,
    )
