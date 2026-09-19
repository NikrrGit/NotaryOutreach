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

