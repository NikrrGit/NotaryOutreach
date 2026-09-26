"""Local outreach search settings and draft review."""

from pathlib import Path
import os
import sqlite3
import sys
from uuid import uuid4

import streamlit as st
from dotenv import dotenv_values

# Support direct execution before the new packages are installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.outreach_service import OutreachService
from storage.sqlite import SQLiteStorage


def execute_job(service: OutreachService, job_id: str, *, resume: bool = False) -> None:
    """Execute an explicit user action and refresh saved results."""
    try:
        with st.spinner("Resuming search…" if resume else "Researching contacts and preparing drafts…"):
            results = service.resume_job(job_id) if resume else service.run_job(job_id)
        if results["job"]["status"] == "manual_review":
            st.session_state["notice"] = "Search finished with items needing review. Inspect the results and any reported issues."
            st.session_state["notice_level"] = "warning"
        else:
            st.session_state["notice"] = "Search finished. Review the results and email drafts below."
            st.session_state["notice_level"] = "success"
    except Exception:
        st.session_state["notice"] = (
            "Search could not finish. Your saved search is available below. "
            "Check your Groq API key and connection, then use Start or Resume to retry. "
            "If another search is running, wait for it to finish."
        )
        st.session_state["notice_level"] = "error"
    st.rerun()


def render_search(service: OutreachService) -> None:
    """Create a search and optionally run it immediately."""
    mode = st.radio("What are you looking for?", ["Notary", "Venture Capital"], horizontal=True)
    target = "notary" if mode == "Notary" else "vc"
    with st.form(f"search-{target}"):
        settings = {"target_type": target}
        settings["location"] = st.text_input("Location / investment geography" if target == "vc" else "Location")
        if target == "notary":
            settings["company_type"] = st.selectbox("Company type", ["UG", "GmbH"])
        else:
            settings["startup_description"] = st.text_area("Startup description")
            settings["industry"] = st.text_input("Industry")
            settings["funding_stage"] = st.selectbox("Funding stage", ["Pre-seed", "Seed", "Series A", "Series B", "Growth"])
        settings["target_count"] = st.number_input("Number of results", min_value=1, max_value=100, value=10, step=1)
        start = st.form_submit_button("Start search")
        submitted = st.form_submit_button("Save search")
    if start or submitted:
        job_id = st.session_state.setdefault("pending_job_id", str(uuid4()))
        try:
            service.create_job(**settings, job_id=job_id)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state.pop("pending_job_id", None)
            st.session_state["selected_job"] = job_id
            if start:
                execute_job(service, job_id)
            st.session_state["notice"] = "Search saved. Start it from Saved searches when ready."
            st.rerun()


def render_draft(service: OutreachService, job_id: str, draft: dict, results: dict) -> None:
    """Show an exact draft version and its review controls."""
    draft_id = draft["id"]
    evaluations = [item for item in results["evaluations"] if item["draft_id"] == draft_id]
    reviews = [item for item in results["reviews"] if item["draft_id"] == draft_id]
    evaluation = evaluations[-1] if evaluations else None
    decision = reviews[-1]["decision"] if reviews else "pending"
    st.caption(f"Human review: {decision}")
    if evaluation:
        st.write("Evaluation:", "Passed" if evaluation["passed"] else "Failed")
        if evaluation.get("score") is not None:
            st.write("Score:", evaluation["score"])
        st.text(evaluation.get("reasoning") or "No evaluation explanation recorded.")
        for issue in evaluation["issues_json"]:
            st.text(str(issue))
    else:
        st.info("This version needs evaluation before approval.")
    subject = st.text_input("Subject", value=draft["subject"], key=f"subject-{draft_id}")
    body = st.text_area("Email", value=draft["body"], height=220, key=f"body-{draft_id}")
    changed = subject != draft["subject"] or body != draft["body"]
    if changed:
        st.caption("Save changes as a new version before reviewing it.")
    save, evaluate, approve, reject = st.columns(4)
    edited = save.button("Save new version", key=f"edit-{draft_id}", disabled=not changed)
    evaluated = evaluate.button("Re-evaluate" if evaluation else "Evaluate draft",
                                key=f"evaluate-{draft_id}", disabled=changed)
    approved = approve.button(
        "Approve", key=f"approve-{draft_id}",
        disabled=changed or not evaluation or not evaluation["passed"] or decision == "approved",
    )
    rejected = reject.button("Reject", key=f"reject-{draft_id}", disabled=changed or decision == "rejected")
    if edited or evaluated or approved or rejected:
        try:
            if edited:
                service.edit_email(job_id, draft_id, subject=subject, body=body)
                st.session_state.pop(f"version-{draft['candidate_id']}", None)
                notice = "New version saved. It needs a fresh evaluation and review."
            elif evaluated:
                with st.spinner("Evaluating this draft version…"):
                    service.evaluate_draft(job_id, draft_id)
                notice = "Evaluation saved. Inspect the result before approving."
            elif approved:
                service.approve_draft(job_id, draft_id)
                notice = "Draft approved. No email was sent."
            else:
                service.reject_draft(job_id, draft_id)
                notice = "Draft rejected."
        except ValueError as exc:
            st.error("Evaluation could not finish. Check the search evidence and settings, then retry." if evaluated else str(exc))
        except KeyError:
            st.error("This draft is no longer available. Reload the search.")
        except Exception:
            st.error("Could not save this action. Check your connection and local storage, then retry.")
        else:
            st.session_state["notice"] = notice
            st.rerun()
    if reviews:
        with st.expander("Review history"):
            for review in reviews:
                st.caption(f"{review['created_at']} · {review['decision']}")
                st.text(review["final_subject"])
                st.text(review["final_body"])


def render_results(service: OutreachService) -> None:
    """Browse saved searches using shared candidate and evidence views."""
    jobs = service.list_jobs()
    st.subheader("Saved searches")
    if not jobs:
        st.info("Save a search to get started.")
        return
    labels = {
        job["id"]: f"{job['location']} · {'Notary' if job['target_type'] == 'notary' else 'VC'} · {job['created_at']} · {job['id'][:8]}"
        for job in jobs
    }
    if st.session_state.get("selected_job") not in labels:
        st.session_state["selected_job"] = jobs[0]["id"]
    job_id = st.selectbox("Search", list(labels), format_func=labels.get, key="selected_job")
    results = service.load_results(job_id)
    job = results["job"]
    st.caption(f"Status: {job['status']} · Requested results: {job['target_count']}")
    if job["status"] not in ("ready_for_review", "manual_review", "completed"):
        resume = results["has_checkpoint"]
        if st.button("Resume search" if resume else "Start saved search", key=f"run-{job_id}"):
            execute_job(service, job_id, resume=resume)
    if results.get("workflow_errors"):
        with st.expander("Search issues"):
            for error in results["workflow_errors"]:
                st.text(f"{error['node']}: {error['message']}")

    with st.expander("Search settings"):
        for field in ("location", "company_type", "startup_description", "industry", "funding_stage"):
            if job.get(field):
                st.text(f"{field.replace('_', ' ').capitalize()}: {job[field]}")
    if not results["candidates"]:
        st.info("No candidates saved for this search yet.")
    for candidate in results["candidates"]:
        candidate_id = candidate["id"]
        with st.expander(candidate["name"], expanded=True):
            for field in ("organization", "city", "website", "email", "phone", "source_url"):
                st.text(f"{field.replace('_', ' ').capitalize()}: {candidate.get(field) or 'Not available'}")
            verifications = [item for item in results["verifications"] if item["candidate_id"] == candidate_id]
            if not verifications:
                st.info("Verification pending.")
            for verification in verifications:
                eligible = verification["eligible"]
                status = "Unknown" if eligible is None else "Eligible" if eligible else "Ineligible"
                st.write("Verification:", status, "· Confidence:", verification["confidence"])
                st.text(verification["reason"])
                st.text(verification.get("evidence") or "No evidence recorded.")
                st.text(f"Source: {verification.get('source_url') or 'Not available'}")
            drafts = [item for item in reversed(results["drafts"]) if item["candidate_id"] == candidate_id]
            if not drafts:
                st.info("No email draft available.")
                continue
            versions = {draft["id"]: f"Version {len(drafts) - index} · {draft['created_at']}" for index, draft in enumerate(drafts)}
            selected = st.selectbox("Draft version", list(versions), format_func=versions.get, key=f"version-{candidate_id}")
            draft = next(item for item in drafts if item["id"] == selected)
            render_draft(service, job_id, draft, results)


def main() -> None:
    """Run the local review page."""
    st.set_page_config(page_title="Outreach", page_icon="✉", layout="wide")
    st.title("Outreach")
    st.caption("Find relevant contacts. Prepare emails. Review every draft.")
    st.info("Search execution is not connected yet. You can save search settings and review existing results. No emails are sent.")
    notice = st.session_state.pop("notice", None)
    if notice:
        st.success(notice)
    try:
        settings = {**dotenv_values(".env"), **os.environ}
        storage = SQLiteStorage(settings.get("DATABASE_PATH") or "data/outreach.db")
        service = OutreachService(storage)
        render_search(service)
        st.divider()
        render_results(service)
    except (sqlite3.Error, OSError):
        st.error("Could not access the local database. Check its location and write permissions, then retry.")
    except ValueError as exc:
        st.error(str(exc))
    except KeyError:
        st.error("This search is no longer available. Reload the page.")


if __name__ == "__main__":
    main()
