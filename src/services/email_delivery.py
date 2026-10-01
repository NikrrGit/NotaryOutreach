"""Explicit SMTP delivery; no automatic retries after a possible send."""

from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path
import os
import re
import smtplib
import ssl
from tempfile import mkstemp

from dotenv import dotenv_values, set_key


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
    """Load a local account without expanding characters in its password."""
    return parse_mail_settings({**dotenv_values(env_file, interpolate=False), **os.environ})


class DeliveryError(RuntimeError):
    def __init__(self, message: str, *, uncertain: bool = False):
        super().__init__(message)
        self.uncertain = uncertain


def save_mail_settings(settings: MailSettings, env_file: str | Path = ".env") -> None:
    """Atomically save an account while preserving unrelated local settings."""
    values = dict(SMTP_HOST=settings.host, SMTP_PORT=str(settings.port), SMTP_SECURITY=settings.security,
                  SMTP_FROM=settings.sender, SMTP_USERNAME=settings.username or "", SMTP_PASSWORD=settings.password or "")
    parse_mail_settings(values)
    if any(name in os.environ for name in values):
        raise ValueError("SMTP environment variables override saved accounts. Remove those overrides before saving here.")
    path = Path(env_file)
    if path.is_symlink():
        raise ValueError("Save the email account to a regular .env file, not a symbolic link.")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = mkstemp(prefix=".env.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            if path.exists():
                output.write(path.read_text(encoding="utf-8"))
        for name, value in values.items():
            set_key(temporary, name, value, quote_mode="always")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _connect_mail(settings: MailSettings):
    """Open an encrypted SMTP session and close partial connections on failure."""
    client = None
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
        return client
    except Exception:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        raise


def check_mail_connection(settings: MailSettings) -> None:
    """Test server sign-in without sending a message."""
    try:
        client = _connect_mail(settings)
    except smtplib.SMTPAuthenticationError:
        raise DeliveryError("Email sign-in failed. Check the email address and app password, and whether your account permits SMTP.") from None
    except Exception:
        raise DeliveryError("Could not connect to the mail server. Check the host, port, encryption, and your connection.") from None
    try:
        client.close()
    except Exception:
        pass


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
        client = _connect_mail(settings)
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
