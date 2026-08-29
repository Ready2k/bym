"""Delivery helpers for material Super 6 alerts.

The decision UI always has an in-app stream.  Email is deliberately opt-in:
it is sent only when a destination and a working SMTP relay are both
configured.  A missing mail transport is reported to the UI as unavailable,
never treated as a sent alert.

Callers resolve the relay itself (host/port/sender from either the Settings
UI or `BYM_SMTP_*` env vars, username/password from the same secrets/ store
that holds API keys — see `resolve_smtp_config` in web.py) and hand this
module a fully-formed `SmtpConfig`. Nothing here reads the environment
directly, since the browser is one of two places that config can come from.
"""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage


@dataclass(frozen=True)
class FinalPick:
    home_team: str
    away_team: str
    pick: str | None
    modellable: bool = True


@dataclass(frozen=True)
class SmtpConfig:
    host: str | None
    port: int = 587
    sender: str = "bym@localhost"
    username: str | None = None
    password: str | None = None
    starttls: bool = True


def email_delivery_status(
    enabled: bool, destination: str | None, smtp: SmtpConfig
) -> str:
    if not enabled:
        return "OFF"
    if not destination:
        return "NEEDS_ADDRESS"
    if not smtp.host:
        return "NEEDS_SMTP"
    return "READY"


def send_email_alert(
    *, destination: str, subject: str, body: str, smtp: SmtpConfig
) -> None:
    """Send one opted-in alert through an explicitly configured SMTP relay."""
    if not smtp.host:
        raise RuntimeError("An SMTP host is not configured")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp.sender
    message["To"] = destination
    message.set_content(body)
    with smtplib.SMTP(smtp.host, smtp.port, timeout=10) as client:
        if smtp.starttls:
            client.starttls(context=ssl.create_default_context())
        if smtp.username:
            client.login(smtp.username, smtp.password or "")
        client.send_message(message)


def compose_final_summary(
    round_number: int,
    deadline: datetime,
    fixtures: list[FinalPick],
    golden_goal_minute: int | None,
) -> tuple[str, str]:
    """Format the last-call, copy-into-Super-6 email.

    Full team names, not the in-app "Copy picks" button's short codes:
    that abbreviation only reads back correctly next to the app itself,
    and this email is meant to stand on its own in an inbox.
    """
    subject = f"Super 6 — Round {round_number} final recommended picks"
    lines = [
        f"Deadline: {deadline.strftime('%a %d %b, %H:%M')} — under an hour to go.",
        "",
    ]
    unmodellable = []
    for fixture in fixtures:
        if fixture.modellable and fixture.pick:
            lines.append(f"{fixture.home_team} {fixture.pick} {fixture.away_team}")
        else:
            lines.append(f"{fixture.home_team} v {fixture.away_team}: no forecast — enter this one yourself")
            unmodellable.append(f"{fixture.home_team} v {fixture.away_team}")
    if golden_goal_minute is not None:
        lines.append(f"Golden Goal: minute {golden_goal_minute}")
    if unmodellable:
        lines.append("")
        lines.append(
            "The model has no usable forecast for: " + ", ".join(unmodellable)
            + " — those are outside its data, so pick them yourself."
        )
    lines.append("")
    lines.append("Review in Beat Your Mates before you enter these.")
    return subject, "\n".join(lines)
