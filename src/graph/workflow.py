
from __future__ import annotations

from typing import Any
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from src.graph.routing import (
    route_after_evaluation,
    route_after_verification,
)
from src.graph.state import WorkflowState


# ---------------------------------------------------------------------------
# State initialization
# ---------------------------------------------------------------------------


def create_initial_state(
    *,
    location: str,
    company_type: str,
    target_results: int = 10,
) -> WorkflowState:
    """
    Create the initial state for a new workflow run.

    The graph should receive a predictable state shape instead of
    allowing individual nodes to initialize fields themselves.
    """

    if not location.strip():
        raise ValueError("location cannot be empty")

    if company_type not in {"UG", "GmbH"}:
        raise ValueError(
            "company_type must be either 'UG' or 'GmbH'"
        )

    if target_results <= 0:
        raise ValueError(
            "target_results must be greater than zero"
        )

    return WorkflowState(
        job_id=str(uuid4()),
        location=location.strip(),
        company_type=company_type,
        target_results=target_results,

        candidates=[],
        verified_candidates=[],
        email_drafts=[],
        evaluations=[],

        retry_counts={
            "discovery": 0,
            "verification": 0,
            "email": 0,
            "evaluation": 0,
        },

        errors=[],
        status="pending",
    )


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


def build_workflow(
    *,
    discovery_agent: Any,
    verification_agent: Any,
    email_writer: Any,
    evaluator: Any,
):
    """
    Build and compile the Notary Agent LangGraph workflow.

    Agent instances are injected instead of created here.

    This keeps the graph independent from Groq or any future provider.
    """

    graph = StateGraph(WorkflowState)