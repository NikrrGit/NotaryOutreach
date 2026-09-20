"""Compile the injected agents into a bounded draft-review workflow."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.email_writer import EmailWriterInput

from .nodes import EvaluateDraft, WorkflowNodes, candidate_key
from .routing import Route, route_on_error
from .state import CompanyType, SearchSettings, WorkflowState


def create_initial_state(
    *,
    location: str,
    company_type: CompanyType,
    target_results: int = 10,
) -> WorkflowState:
    """Validate search settings and initialize the graph's actual state fields."""
    return WorkflowState(
        settings=SearchSettings(
            location=location, company_type=company_type, target_count=target_results,
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
):
    """Build a graph that produces drafts for human review without sending them.

    The evaluator is a callable taking a CandidateEmailDraft and its
    VerificationResult and returning the state's EvaluationResult.
    """
    nodes = WorkflowNodes(
        discovery=discovery_agent,
        verifier=verification_agent,
        writer=email_writer,
        evaluator=evaluator,
        persist_draft=lambda *_: None,
    )
    graph = StateGraph(WorkflowState)

    def latest_verifications(state: WorkflowState):
        return {
            candidate_key(result.candidate): result
            for result in state["verification_results"]
            if result.company_type == state["settings"].company_type
        }

    def resolved(result) -> bool:
        if result.status == "unsupported":
            return True
        try:
            EmailWriterInput.from_verification(result)
        except (TypeError, ValueError):
            return False
        return True

    def verify_node(state: WorkflowState) -> dict:
        # Filter only the node's input; the graph retains the full result history.
        resolved_results = [r for r in latest_verifications(state).values() if resolved(r)]
        return nodes.verify({**state, "verification_results": resolved_results})

    def verification_route(state: WorkflowState) -> Route:
        results = latest_verifications(state)
        if state["candidates"] and all(
            (result := results.get(candidate_key(candidate))) is not None and resolved(result)
            for candidate in state["candidates"]
        ):
            return "continue"
        return route_on_error(state, "verification")

    def passing_drafts(state: WorkflowState):
        evaluations = {result.draft_id: result for result in state["evaluations"]}
        latest = {candidate_key(d.candidate): d for d in state["email_drafts"]}
        return {
            key: draft for key, draft in latest.items()
            if (result := evaluations.get(draft.draft_id)) is not None and result.passed
        }

    def write_emails_node(state: WorkflowState) -> dict:
        # Keep passing drafts; failed/missing evaluations need a fresh draft ID.
        return nodes.write_email({**state, "email_drafts": list(passing_drafts(state).values())})

    def evaluate_node(state: WorkflowState) -> dict:
        latest = {candidate_key(d.candidate): d for d in state["email_drafts"]}
        return nodes.evaluate({**state, "email_drafts": list(latest.values())})

    def evaluation_route(state: WorkflowState) -> Route:
        required = {
            key for key, result in latest_verifications(state).items()
            if result.status == "supported"
        }
        if not required:
            return "manual_review"
        if required <= passing_drafts(state).keys():
            return "continue"
        return route_on_error(state, "email_generation")

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
    graph.add_conditional_edges("verify", verification_route, {
        "continue": "write_emails",
        "retry": "increment_verification_retry",
        "manual_review": "manual_review",
    })
    graph.add_edge("increment_verification_retry", "verify")
    graph.add_edge("write_emails", "evaluate")
    graph.add_conditional_edges("evaluate", evaluation_route, {
        "continue": "complete",
        "retry": "increment_email_retry",
        "manual_review": "manual_review",
    })
    graph.add_edge("increment_email_retry", "write_emails")
    graph.add_edge("complete", END)
    graph.add_edge("manual_review", END)
    return graph.compile()
