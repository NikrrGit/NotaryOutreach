"""Connect a local mail account without editing configuration files."""

import os

import streamlit as st

from services.email_delivery import (
    DeliveryError, check_mail_connection, load_mail_settings, parse_mail_settings, save_mail_settings,
)


def render_email_setup(service) -> None:
    """Test account sign-in, then save it only after explicit submission."""
    try:
        current = load_mail_settings(service.env_file)
    except ValueError:
        current = None
    if st.session_state.pop("mail_clear_password", False):
        st.session_state["mail_password"] = ""
    with st.expander("Email sending setup", expanded=current is None):
        if current:
            st.success(f"Sending account: {current.sender}")
        else:
            st.info("Connect your email account to send reviewed drafts directly from this app.")
        notice = st.session_state.pop("mail_setup_notice", None)
        if notice:
            (st.success if notice["ok"] else st.error)(notice["message"])
        managed = any(key in os.environ for key in (
            "SMTP_HOST", "SMTP_PORT", "SMTP_SECURITY", "SMTP_FROM", "SMTP_USERNAME", "SMTP_PASSWORD",
        ))
        if managed:
            st.info("SMTP environment variables control this account. Remove those overrides to save account changes here.")
        provider = st.selectbox("Email provider", ["Gmail / Google Workspace", "Custom SMTP"],
                                index=1 if current and current.host != "smtp.gmail.com" else 0, key="mail_provider")
        gmail = provider == "Gmail / Google Workspace"
        if gmail:
            st.markdown("Enable [2-Step Verification](https://myaccount.google.com/security), then [create an app password](https://myaccount.google.com/apppasswords).")
            st.caption("Use the Google app password, not your normal password. Some Workspace accounts restrict app passwords.")
        else:
            st.caption("Use the SMTP settings supplied by your email provider. Password or app-password authentication is required.")
        st.caption("Saved in this repo's local .env file, unencrypted and ignored by Git. The connection test sends no email.")
        with st.form("mail-setup"):
            sender = st.text_input("Your email address", value=current.sender if current else "", key="mail_sender")
            password = st.text_input("App password" if gmail else "Password / app password", type="password", key="mail_password")
            host, port, security, username = "smtp.gmail.com", 587, "starttls", sender
            if not gmail:
                host = st.text_input("SMTP host", value=current.host if current else "", key="mail_host")
                port = st.number_input("SMTP port", min_value=1, max_value=65535,
                                       value=current.port if current else 587, step=1, key="mail_port")
                security = st.selectbox("Encryption", ["starttls", "ssl"],
                                        index=1 if current and current.security == "ssl" else 0, key="mail_security")
                username = st.text_input("SMTP username (blank uses your email)",
                                         value=current.username or "" if current else "", key="mail_username") or sender
                st.caption("Common settings: port 587 with starttls, or port 465 with ssl.")
            submitted = st.form_submit_button("Test and save email account", disabled=managed)
        if submitted:
            try:
                if not password.strip():
                    raise ValueError("Enter your app password to connect this account.")
                settings = parse_mail_settings(dict(
                    SMTP_HOST=host, SMTP_PORT=port, SMTP_SECURITY=security, SMTP_FROM=sender,
                    SMTP_USERNAME=username, SMTP_PASSWORD="".join(password.split()) if gmail else password,
                ))
                with st.spinner("Checking email account sign-in…"):
                    check_mail_connection(settings)
                save_mail_settings(settings, service.env_file)
            except (ValueError, DeliveryError) as exc:
                notice = {"ok": False, "message": str(exc)}
            except OSError:
                notice = {"ok": False, "message": "Could not save the account. Check permissions for the local .env file."}
            else:
                notice = {"ok": True, "message": "Account connected. Review a draft and click Send email. No test email was sent."}
            st.session_state["mail_setup_notice"] = notice
            st.session_state["mail_clear_password"] = True
            st.rerun()
