"""Compile the injected agents into a bounded draft-review workflow."""

from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver

from .nodes import EvaluateDraft, WorkflowNodes, _verifications, candidate_key
from .routing import (passing_drafts, route_after_evaluation, route_after_verification,
                      verification_resolved)
from .state import CompanyType, SearchSettings, WorkflowState


def create_initial_state(
    *,
    location: str,
    company_type: CompanyType | None = None,
    target_results: int = 10,
    target_type: Literal["notary", "vc"] = "notary",
    startup_description: str | None = None,
    industry: str | None = None,
    funding_stage: str | None = None,
) -> WorkflowState:
    """Validate search settings and initialize the graph's actual state fields."""
    return WorkflowState(
        settings=SearchSettings(
            location=location, company_type=company_type, target_count=target_results,
            target_type=target_type, startup_description=startup_description,
            industry=industry, funding_stage=funding_stage,
        ),
        candidates=[],
        verification_results=[],
        email_drafts=[],
        evaluations=[],
        retry_counts={
            "discovery": 0, "verification": 0,
            "email_generation": 0, "evaluation": 0,
        },
        errors=[],
        status="pending",
    )


def build_workflow(
    *,
    discovery_agent: Any,
    verification_agent: Any,
    email_writer: Any,
    evaluator: EvaluateDraft,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """Build a graph that produces drafts for human review without sending them.

    The evaluator is a callable taking a CandidateEmailDraft and its
    VerificationResult and returning the state's EvaluationResult.
    Pass an open durable checkpointer to enable restart/resume, and keep its
    connection open for the lifetime of graph execution.
    """
    nodes = WorkflowNodes(
        discovery=discovery_agent,
        verifier=verification_agent,
        writer=email_writer,
        evaluator=evaluator,
        persist_draft=lambda *_: None,
    )
    graph = StateGraph(WorkflowState)

    def verify_node(state: WorkflowState) -> dict:
        resolved = [result for result in _verifications(state).values() if verification_resolved(result)]
        return nodes.verify({**state, "verification_results": resolved})

    def write_emails_node(state: WorkflowState) -> dict:
        # Keep passing drafts; failed/missing evaluations need a fresh draft ID.
        return nodes.write_email({**state, "email_drafts": list(passing_drafts(state).values())})

    def evaluate_node(state: WorkflowState) -> dict:
        latest = {candidate_key(d.candidate): d for d in state["email_drafts"]}
        return nodes.evaluate({**state, "email_drafts": list(latest.values())})

    def increment_retry(state: WorkflowState, stage: str) -> dict:
        counts = dict(state["retry_counts"])
        counts[stage] = counts.get(stage, 0) + 1
        return {"retry_counts": counts}

    graph.add_node("discover", nodes.discover)
    graph.add_node("verify", verify_node)
    graph.add_node("write_emails", write_emails_node)
    graph.add_node("evaluate", evaluate_node)
    graph.add_node(
        "increment_verification_retry",
        lambda state: increment_retry(state, "verification"),
    )
    graph.add_node(
        "increment_email_retry",
        lambda state: increment_retry(state, "email_generation"),
    )
    graph.add_node("manual_review", lambda state: {"status": "manual_review"})
    graph.add_node("complete", lambda state: {"status": "ready_for_review"})

    graph.add_edge(START, "discover")
    graph.add_edge("discover", "verify")
    graph.add_conditional_edges("verify", route_after_verification, {
        "continue": "write_emails",
        "retry": "increment_verification_retry",
        "manual_review": "manual_review",
    })
    graph.add_edge("increment_verification_retry", "verify")
    graph.add_edge("write_emails", "evaluate")
    graph.add_conditional_edges("evaluate", route_after_evaluation, {
        "continue": "complete",
        "retry": "increment_email_retry",
        "manual_review": "manual_review",
    })
    graph.add_edge("increment_email_retry", "write_emails")
    graph.add_edge("complete", END)
    graph.add_edge("manual_review", END)
    return graph.compile(checkpointer=checkpointer)
