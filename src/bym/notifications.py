"""Delivery helpers for material Super 6 alerts.

The decision UI always has an in-app stream.  Email is deliberately opt-in:
it is sent only when a destination, SMTP host and the email channel are all
configured.  A missing mail transport is reported to the UI as unavailable,
never treated as a sent alert.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage


def email_delivery_status(enabled: bool, destination: str | None) -> str:
    if not enabled:
        return "OFF"
    if not destination:
        return "NEEDS_ADDRESS"
    if not os.environ.get("BYM_SMTP_HOST"):
        return "NEEDS_SMTP"
    return "READY"


def send_email_alert(*, destination: str, subject: str, body: str) -> None:
    """Send one opted-in alert through an explicitly configured SMTP relay."""
    host = os.environ.get("BYM_SMTP_HOST")
    if not host:
        raise RuntimeError("BYM_SMTP_HOST is not configured")
    port = int(os.environ.get("BYM_SMTP_PORT", "587"))
    sender = os.environ.get("BYM_SMTP_FROM", "bym@localhost")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = destination
    message.set_content(body)
    with smtplib.SMTP(host, port, timeout=10) as client:
        if os.environ.get("BYM_SMTP_STARTTLS", "true").lower() != "false":
            client.starttls(context=ssl.create_default_context())
        username = os.environ.get("BYM_SMTP_USERNAME")
        if username:
            client.login(username, os.environ.get("BYM_SMTP_PASSWORD", ""))
        client.send_message(message)
