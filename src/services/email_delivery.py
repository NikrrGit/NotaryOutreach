"""Explicit SMTP delivery; no automatic retries after a possible send."""

from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path
import os
import re
import smtplib
import ssl

from dotenv import dotenv_values


def email_address(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", value):
        raise ValueError("Enter a single valid email address.")
    return value


@dataclass(frozen=True)
class MailSettings:
    host: str
    port: int
    security: str
    sender: str
    username: str | None = None
    password: str | None = field(default=None, repr=False)


def parse_mail_settings(values: dict) -> MailSettings:
    """Validate mail settings from either a form or environment."""
    host = (values.get("SMTP_HOST") or "").strip()
    sender = (values.get("SMTP_FROM") or "").strip()
    if not host or not sender:
        raise ValueError("Set SMTP_HOST and SMTP_FROM, or connect an account under Email sending setup.")
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host):
        raise ValueError("Enter an SMTP hostname without a URL scheme, port, or credentials.")
    security = (values.get("SMTP_SECURITY") or "starttls").strip().lower()
    if security not in {"starttls", "ssl"}:
        raise ValueError("SMTP_SECURITY must be starttls or ssl.")
    try:
        port = int(values.get("SMTP_PORT") or (465 if security == "ssl" else 587))
        if not 1 <= port <= 65535:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("SMTP_PORT must be between 1 and 65535.") from None
    username = (values.get("SMTP_USERNAME") or "").strip() or None
    password = values.get("SMTP_PASSWORD") or None
    if bool(username) != bool(password):
        raise ValueError("Set both SMTP_USERNAME and SMTP_PASSWORD for authentication.")
    return MailSettings(host, port, security, email_address(sender), username, password)


def load_mail_settings(env_file: str | Path = ".env") -> MailSettings:
    values = {**dotenv_values(env_file), **os.environ}
    host = (values.get("SMTP_HOST") or "").strip()
    sender = (values.get("SMTP_FROM") or "").strip()
    if not host or not sender:
        raise ValueError("Set SMTP_HOST and SMTP_FROM in .env to send emails from this app.")
    security = (values.get("SMTP_SECURITY") or "starttls").strip().lower()
    if security not in {"starttls", "ssl"}:
        raise ValueError("SMTP_SECURITY must be starttls or ssl.")
    try:
        port = int(values.get("SMTP_PORT") or (465 if security == "ssl" else 587))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError("SMTP_PORT must be between 1 and 65535.") from None
    username = (values.get("SMTP_USERNAME") or "").strip() or None
    password = values.get("SMTP_PASSWORD") or None
    if bool(username) != bool(password):
        raise ValueError("Set both SMTP_USERNAME and SMTP_PASSWORD for authentication.")
    return MailSettings(host, port, security, email_address(sender), username, password)


class DeliveryError(RuntimeError):
    def __init__(self, message: str, *, uncertain: bool = False):
        super().__init__(message)
        self.uncertain = uncertain


def deliver(settings: MailSettings, *, recipient: str, subject: str, body: str, message_id: str) -> None:
    """Submit exactly one message; successful SMTP acceptance is not inbox delivery."""
    message = EmailMessage()
    message["From"] = settings.sender
    message["To"] = email_address(recipient)
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message["Date"] = formatdate(localtime=False)
    message.set_content(body)
    client = None
    submitting = False
    try:
        context = ssl.create_default_context()
        if settings.security == "ssl":
            client = smtplib.SMTP_SSL(settings.host, settings.port, timeout=30, context=context)
        else:
            client = smtplib.SMTP(settings.host, settings.port, timeout=30)
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        if settings.username:
            client.login(settings.username, settings.password)
        submitting = True
        refused = client.send_message(message, from_addr=settings.sender, to_addrs=[recipient])
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPDataError, smtplib.SMTPSenderRefused):
        raise DeliveryError("The mail server rejected the message. Check the sender and recipient before retrying.") from None
    except Exception:
        if submitting:
            raise DeliveryError("Delivery is uncertain. Check your mail provider before sending another copy.", uncertain=True) from None
        raise DeliveryError("Could not connect or sign in to the mail server. Check SMTP settings and retry.") from None
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def default_email(job: dict, candidate: dict) -> tuple[str, str]:
    """A plain starting draft without claims about the recipient's suitability."""
    if job["target_type"] == "notary":
        subject = f"Terminanfrage zur Gründung einer {job['company_type']}"
        body = (f"Sehr geehrte Damen und Herren,\n\nich möchte eine {job['company_type']} in {job['location']} gründen. "
                "Bieten Sie hierfür notarielle Unterstützung an? Wann wäre der früheste Termin möglich, "
                "und welche Unterlagen benötigen Sie vorab?\n\nVielen Dank.\nMit freundlichen Grüßen\n[Ihr Name]")
    else:
        subject = f"Anfrage zu einer Finanzierung in der Phase {job['funding_stage']}"
        body = (f"Sehr geehrte Damen und Herren,\n\nkurz zu unserem Startup: {job['startup_description']}\n\n"
                f"Wir suchen eine Finanzierung in der Phase {job['funding_stage']} im Bereich {job['industry']}. "
                "Passt dies zu Ihrem Investitionsfokus? Über ein kurzes Kennenlerngespräch würden wir uns freuen.\n\n"
                "Mit freundlichen Grüßen\n[Ihr Name]")
    return subject, body
