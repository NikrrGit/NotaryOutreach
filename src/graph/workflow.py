
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


# ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------

    def discover_node(state: WorkflowState) -> dict:
        """
        Find candidate notaries.
        """

        candidates = discovery_agent.discover(
            location=state["location"],
            company_type=state["company_type"],
            limit=state["target_results"],
        )

        return {
            "candidates": candidates,
            "status": "discovered",
        }

    def verify_node(state: WorkflowState) -> dict:
        """
        Verify discovered candidates.

        Verification is intentionally separate from discovery.
        """

        verified = []

        for candidate in state["candidates"]:
            try:
                result = verification_agent.verify(
                    candidate=candidate,
                    company_type=state["company_type"],
                )

                verified.append(result)

            except Exception as exc:
                errors = list(state.get("errors", []))

                errors.append(
                    {
                        "stage": "verification",
                        "candidate": getattr(
                            candidate,
                            "name",
                            "unknown",
                        ),
                        "error": str(exc),
                    }
                )

                # One bad candidate must not kill the whole job.
                continue

        return {
            "verified_candidates": verified,
            "status": "verified",
        }



def write_emails_node(state: WorkflowState) -> dict:
        """
        Generate emails only for verified candidates.
        """

        drafts = []

        for verification in state["verified_candidates"]:
            try:
                draft = email_writer.write(
                    verification
                )

                drafts.append(draft)

            except Exception as exc:
                errors = list(state.get("errors", []))

                errors.append(
                    {
                        "stage": "email",
                        "error": str(exc),
                    }
                )

                continue

        return {
            "email_drafts": drafts,
            "status": "emails_written",
        }

    def evaluate_node(state: WorkflowState) -> dict:
        """
        Evaluate generated drafts before human review.
        """

        evaluations = []

        for draft in state["email_drafts"]:
            try:
                evaluation = evaluator.evaluate(draft)

                evaluations.append(evaluation)

            except Exception as exc:
                errors = list(state.get("errors", []))

                errors.append(
                    {
                        "stage": "evaluation",
                        "error": str(exc),
                    }
                )

                continue

        return {
            "evaluations": evaluations,
            "status": "evaluated",
        }

    def increment_verification_retry(
        state: WorkflowState,
    ) -> dict:
        """
        Increment verification retry counter.

        Routing itself remains pure and does not mutate state.
        """

        retry_counts = dict(
            state.get("retry_counts", {})
        )

        retry_counts["verification"] = (
            retry_counts.get("verification", 0) + 1
        )

        return {
            "retry_counts": retry_counts,
            "status": "retrying_verification",
        }

    def increment_email_retry(
        state: WorkflowState,
    ) -> dict:
        """
        Increment email retry counter before regenerating drafts.
        """

        retry_counts = dict(
            state.get("retry_counts", {})
        )

        retry_counts["email"] = (
            retry_counts.get("email", 0) + 1
        )

        return {
            "retry_counts": retry_counts,
            "status": "retrying_email",
        }

    def manual_review_node(
        state: WorkflowState,
    ) -> dict:
        """
        Stop autonomous processing and mark the job for human review.
        """

        return {
            "status": "manual_review",
        }

    def complete_node(
        state: WorkflowState,
    ) -> dict:
        """
        Mark successful autonomous processing as ready for human
        approval.
        """

        return {
            "status": "ready_for_review",
        }

    # ------------------------------------------------------------------
    # Register nodes
    # ------------------------------------------------------------------

    graph.add_node("discover", discover_node)
    graph.add_node("verify", verify_node)
    graph.add_node("write_emails", write_emails_node)
    graph.add_node("evaluate", evaluate_node)

    graph.add_node(
        "increment_verification_retry",
        increment_verification_retry,
    )

    graph.add_node(
        "increment_email_retry",
        increment_email_retry,
    )

    graph.add_node(
        "manual_review",
        manual_review_node,
    )

    graph.add_node(
        "complete",
        complete_node,
    )

    # ------------------------------------------------------------------
    # Main workflow
    # ------------------------------------------------------------------

    graph.add_edge(
        START,
        "discover",
    )

    graph.add_edge(
        "discover",
        "verify",
    )

    # ------------------------------------------------------------------
    # Verification routing
    # ------------------------------------------------------------------

    graph.add_conditional_edges(
        "verify",
        route_after_verification,
        {
            "continue": "write_emails",
            "retry": "increment_verification_retry",
            "manual_review": "manual_review",
        },
    )

    graph.add_edge(
        "increment_verification_retry",
        "verify",
    )

    # ------------------------------------------------------------------
    # Email generation
    # ------------------------------------------------------------------

    graph.add_edge(
        "write_emails",
        "evaluate",
    )

    # ------------------------------------------------------------------
    # Evaluation routing
    # ------------------------------------------------------------------

    graph.add_conditional_edges(
        "evaluate",
        route_after_evaluation,
        {
            "continue": "complete",
            "retry": "increment_email_retry",
            "manual_review": "manual_review",
        },
    )

    # Failed email evaluation means regenerate the email,
    # not rerun discovery or verification.
    graph.add_edge(
        "increment_email_retry",
        "write_emails",
    )

    # ------------------------------------------------------------------
    # Terminal states
    # ------------------------------------------------------------------

    graph.add_edge(
        "complete",
        END,
    )

    graph.add_edge(
        "manual_review",
        END,
    )

    return graph.compile()