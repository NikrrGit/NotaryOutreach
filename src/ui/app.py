"""Local outreach search settings and draft review."""

from pathlib import Path
from hashlib import sha256
import os
import sqlite3
import sys
from uuid import uuid4
from urllib.parse import quote, urlencode

import streamlit as st
from dotenv import dotenv_values

# Support direct execution before the new packages are installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.outreach_service import OutreachService
from services.email_delivery import default_email, email_address, load_mail_settings
from providers.errors import failure_message
from storage.sqlite import SQLiteStorage
from ui.email_setup import render_email_setup


def execute_job(service: OutreachService, job_id: str, *, resume: bool = False) -> None:
    """Execute an explicit user action and refresh saved results."""
    try:
        with st.spinner("Resuming search…" if resume else "Researching contacts and preparing drafts…"):
            results = service.resume_job(job_id) if resume else service.run_job(job_id)
        if not results["candidates"]:
            st.session_state["notice"] = "Search returned no contacts. See the error below, then retry the search."
            st.session_state["notice_level"] = "error"
        elif results["job"]["status"] == "manual_review":
            st.session_state["notice"] = "Search finished with items needing review. Inspect the results and any reported issues."
            st.session_state["notice_level"] = "warning"
        else:
            st.session_state["notice"] = "Search finished. Review the results and email drafts below."
            st.session_state["notice_level"] = "success"
    except Exception as exc:
        st.session_state["notice"] = failure_message(exc) + " " + (
            "Your search is saved below. Fix the reported issue, then use "
            "Start saved search or Resume search to retry."
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
    """Edit a generated or default draft and send only on explicit confirmation."""
    draft_id = draft["id"]
    template = draft.get("is_template", False)
    candidate = next(item for item in results["candidates"] if item["id"] == draft["candidate_id"])
    evaluations = [item for item in results["evaluations"] if item["draft_id"] == draft_id]
    reviews = [item for item in results["reviews"] if item["draft_id"] == draft_id]
    evaluation = evaluations[-1] if evaluations else None
    decision = reviews[-1]["decision"] if reviews else "pending"
    st.subheader("Email draft")
    if template:
        st.caption("Default template. Edit it and replace the signature before sending. It makes no verified claims about this contact.")
    recipient = st.text_input("To", value=candidate.get("email") or "", key=f"recipient-{draft_id}")
    try:
        address = email_address(recipient)
    except ValueError:
        address = None
        if recipient.strip():
            st.warning("Enter one valid recipient email address.")
    if not candidate.get("email"):
        st.caption("No public email was found. Enter an address you have checked, or use the contact's website.")
    subject = st.text_input("Subject", value=draft["subject"], key=f"subject-{draft_id}")
    body = st.text_area("Email", value=draft["body"], height=300, key=f"body-{draft_id}")
    changed = subject != draft["subject"] or body != draft["body"]
    edited = st.button("Save draft" if template else "Save new version", key=f"edit-{draft_id}", disabled=not template and not changed)
    if edited:
        try:
            if template:
                service.create_draft(job_id, draft["candidate_id"], subject=subject, body=body)
            else:
                service.edit_email(job_id, draft_id, subject=subject, body=body)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state.pop(f"version-{draft['candidate_id']}", None)
            st.session_state["notice"] = "Draft saved."
            st.rerun()

    deliveries = [item for item in results.get("deliveries", []) if item["draft_id"] == draft_id]
    delivery = deliveries[-1] if deliveries else None
    blocked = bool(delivery and delivery["status"] != "failed")
    if delivery:
        if delivery["status"] == "sent":
            st.success(f"Submitted to your mail server for {delivery['recipient']} at {delivery['updated_at']}.")
        elif blocked:
            st.warning("A send was started but delivery is unconfirmed. Check your mail provider before creating another copy.")
        else:
            st.error(delivery.get("error") or "Sending failed. Check SMTP settings and retry.")
    try:
        mail = load_mail_settings(service.env_file)
        st.caption(f"From: {mail.sender}")
        configured = True
    except ValueError as exc:
        configured = False
        st.warning("Sending from this app is not configured. " + str(exc))
        st.caption("Your AI provider key generates drafts. Sending needs your email account; see Email sending setup in the sidebar.")
        if address and not blocked:
            compose_url = f"mailto:{quote(address, safe='@')}?" + urlencode({"subject": subject, "body": body}, quote_via=quote)
            st.link_button("Open in email app", compose_url, key=f"compose-{draft_id}")
            st.caption("Opens this draft in your configured mail app. Review and send there; delivery will not be recorded here.")
    st.caption("Suitability verification does not block sending a reviewed email.")
    fingerprint = sha256(f"{recipient}\0{subject}\0{body}".encode()).hexdigest()[:16]
    confirmed = st.checkbox("I reviewed this recipient and message", key=f"confirm-{draft_id}-{fingerprint}", disabled=blocked)
    if st.button("Send email", key=f"send-{draft_id}", type="primary",
                 disabled=not configured or not confirmed or not address or blocked):
        try:
            with st.spinner("Sending email…"):
                if template:
                    draft_id = service.create_draft(job_id, draft["candidate_id"], subject=subject, body=body)
                elif changed:
                    draft_id = service.edit_email(job_id, draft_id, subject=subject, body=body)
                st.session_state.pop(f"version-{draft['candidate_id']}", None)
                service.send_draft(job_id, draft_id, recipient=recipient, confirmed=True)
        except Exception as exc:
            from services.email_delivery import DeliveryError
            st.session_state["notice"] = str(exc) if isinstance(exc, (ValueError, DeliveryError)) else "Sending could not be confirmed. Check delivery history before retrying."
            st.session_state["notice_level"] = "error"
        else:
            st.session_state["notice"] = "Email submitted to your mail server."
        st.rerun()

    if not template:
        with st.expander("Quality checks and review history"):
            st.caption(f"Human review: {decision}")
            if evaluation:
                st.write("Evaluation:", "Passed" if evaluation["passed"] else "Needs review")
                st.text(evaluation.get("reasoning") or "No explanation recorded.")
                for issue in evaluation["issues_json"]:
                    st.text(str(issue))
            else:
                st.caption("Not evaluated. Check the recipient, facts, and wording before sending.")
            with st.container(horizontal=True):
                evaluated = st.button("Re-evaluate" if evaluation else "Evaluate draft", key=f"evaluate-{draft_id}", disabled=changed)
                approved = st.button("Approve", key=f"approve-{draft_id}", disabled=changed or not evaluation or not evaluation["passed"] or decision == "approved")
                rejected = st.button("Reject", key=f"reject-{draft_id}", disabled=changed or decision == "rejected")
            if evaluated or approved or rejected:
                try:
                    if evaluated:
                        with st.spinner("Evaluating this draft…"):
                            service.evaluate_draft(job_id, draft_id)
                    elif approved:
                        service.approve_draft(job_id, draft_id)
                    else:
                        service.reject_draft(job_id, draft_id)
                except Exception:
                    st.error("Could not complete this review. Check the evidence and provider settings, then retry.")
                else:
                    st.session_state["notice"] = "Review saved."
                    st.rerun()
            for review in reviews:
                st.caption(f"{review['created_at']} · {review['decision']}")
                st.text(review["final_subject"])
                st.text(review["final_body"])


def render_results(service: OutreachService) -> None:
    """Browse saved searches using shared candidate and evidence views."""
    jobs = service.list_jobs()
    st.subheader("Contacts and emails")
    if not jobs:
        st.info("Choose Notary or Venture Capital in the sidebar and start a search. Contacts and editable emails will appear here.")
        return
    labels = {
        job["id"]: f"{job['location']} · {'Notary' if job['target_type'] == 'notary' else 'VC'} · {job['created_at'][:16].replace('T', ' ')}"
        for job in jobs
    }
    if st.session_state.get("selected_job") not in labels:
        st.session_state["selected_job"] = jobs[0]["id"]
    job_id = st.selectbox("Saved search", list(labels), format_func=labels.get, key="selected_job")
    results = service.load_results(job_id)
    job = results["job"]
    st.caption(f"{len(results['candidates'])} contacts found · {job['location']}")
    if job["status"] not in ("ready_for_review", "manual_review", "completed"):
        resume = results["has_checkpoint"]
        if st.button("Resume search" if resume else "Start saved search", key=f"run-{job_id}"):
            execute_job(service, job_id, resume=resume)
    if results.get("workflow_errors"):
        for error in results["workflow_errors"]:
            message = error["message"]
            if message == "discover failed (DiscoveryError).":
                message = "Research failed before contacts were saved. Check your search provider and model, then use Retry search."
            st.error(f"{error['node'].replace('_', ' ').capitalize()}: {message}")
    if job["status"] in ("manual_review", "failed") or not results["candidates"] and job["status"] in ("ready_for_review", "completed"):
        if st.button("Retry search", key=f"retry-{job_id}"):
            fields = {key: job[key] for key in ("target_type", "location", "target_count", "company_type", "startup_description", "industry", "funding_stage")}
            new_job = service.create_job(**fields)
            st.session_state["pending_selection"] = new_job
            execute_job(service, new_job)
    if not results["candidates"]:
        st.warning("No contacts were found for this search. Retry after fixing the reported issue, or change your search criteria.")
        st.subheader("Default email template")
        subject, body = default_email(job, {})
        st.text_input("Subject", value=subject, key=f"empty-subject-{job_id}", disabled=True)
        st.text_area("Email", value=body, height=270, key=f"empty-body-{job_id}", disabled=True)
        st.caption("Once a contact is found, its details and an editable email with sending controls appear here.")
        return

    st.dataframe([{field: candidate.get(field) or "—" for field in ("name", "organization", "city", "email", "phone", "website")}
                  for candidate in results["candidates"]], hide_index=True,
                 column_config={"website": st.column_config.LinkColumn("Website"), "name": "Name", "organization": "Organization",
                                "city": "City", "email": "Email", "phone": "Phone"})
    contacts = {item["id"]: item for item in results["candidates"]}
    candidate_id = st.selectbox("Open contact", list(contacts), format_func=lambda item: contacts[item]["name"], key=f"contact-{job_id}")
    candidate = contacts[candidate_id]
    details, editor = st.columns([1, 2])
    with details:
        st.subheader(candidate["name"])
        for field in ("organization", "city", "email", "phone"):
            st.text(f"{field.capitalize()}: {candidate.get(field) or 'Not available'}")
        for field, label in (("website", "Website"), ("source_url", "Research source")):
            if candidate.get(field):
                st.link_button(label, candidate[field])
        with st.expander("Research evidence", expanded=True):
            st.caption("Suitability checks the match for your search. It does not confirm that the email address can receive mail.")
            verifications = [item for item in results["verifications"] if item["candidate_id"] == candidate_id]
            if not verifications:
                st.info("Suitability has not been checked. You can still review and send the default email.")
            for verification in verifications[-1:]:
                eligible = verification["eligible"]
                st.write("Suitability:", "Unknown" if eligible is None else "Supported" if eligible else "Not supported")
                if eligible is None:
                    st.info("The available website evidence did not confirm a match. You can still send a reviewed email asking about suitability.")
                st.text(verification["reason"])
                st.text(verification.get("evidence") or "No supporting evidence recorded.")
    with editor:
        drafts = [item for item in reversed(results["drafts"]) if item["candidate_id"] == candidate_id]
        if drafts:
            versions = {item["id"]: f"Version {len(drafts) - index}" for index, item in enumerate(drafts)}
            selected = st.selectbox("Draft version", list(versions), format_func=versions.get, key=f"version-{candidate_id}")
            draft = next(item for item in drafts if item["id"] == selected)
        else:
            subject, body = default_email(job, candidate)
            draft = dict(id=f"template-{candidate_id}", candidate_id=candidate_id, subject=subject, body=body, is_template=True)
        render_draft(service, job_id, draft, results)


def main() -> None:
    """Run the local review page."""
    st.set_page_config(page_title="Outreach", page_icon="✉", layout="wide")
    st.title("Outreach")
    st.caption("Find relevant contacts. Prepare emails. Review every draft.")
    st.caption("Review contact details, edit the email, and send when you are ready.")
    if "pending_selection" in st.session_state:
        st.session_state["selected_job"] = st.session_state.pop("pending_selection")
    notice = st.session_state.pop("notice", None)
    level = st.session_state.pop("notice_level", "success")
    if notice:
        {"success": st.success, "warning": st.warning, "error": st.error}.get(level, st.info)(notice)
    try:
        settings = {**dotenv_values(".env"), **os.environ}
        storage = SQLiteStorage(settings.get("DATABASE_PATH") or "data/outreach.db")
        service = OutreachService(storage, checkpoint_path=settings.get("CHECKPOINT_PATH") or "runs/checkpoints.sqlite3")
        with st.sidebar:
            st.header("Find contacts")
            render_search(service)
            render_email_setup(service)
        render_results(service)
    except (sqlite3.Error, OSError):
        st.error("Could not access the local database. Check its location and write permissions, then retry.")
    except ValueError as exc:
        st.error(str(exc))
    except KeyError:
        st.error("This search is no longer available. Reload the page.")


if __name__ == "__main__":
    main()
