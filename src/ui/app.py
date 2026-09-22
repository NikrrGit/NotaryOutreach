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


def render_search(service: OutreachService) -> None:
    """Save settings for either search mode."""
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
        submitted = st.form_submit_button("Save search")
    if submitted:
        job_id = st.session_state.setdefault("pending_job_id", str(uuid4()))
        try:
            service.create_job(**settings, job_id=job_id)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state.pop("pending_job_id", None)
            st.session_state["selected_job"] = job_id
            st.session_state["notice"] = "Search saved. Execution is not available yet."
            st.rerun()
