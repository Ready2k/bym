"""Email delivery status, SMTP config resolution, and the final-summary format."""

from datetime import datetime, timezone

from bym.notifications import FinalPick, SmtpConfig, compose_final_summary, email_delivery_status

DEADLINE = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)


def test_email_status_needs_a_destination_before_smtp():
    smtp = SmtpConfig(host="smtp.gmail.com")
    assert email_delivery_status(True, None, smtp) == "NEEDS_ADDRESS"
    assert email_delivery_status(True, "", smtp) == "NEEDS_ADDRESS"


def test_email_status_needs_smtp_once_a_destination_exists():
    smtp = SmtpConfig(host=None)
    assert email_delivery_status(True, "you@example.com", smtp) == "NEEDS_SMTP"


def test_email_status_is_ready_once_both_are_configured():
    smtp = SmtpConfig(host="smtp.gmail.com")
    assert email_delivery_status(True, "you@example.com", smtp) == "READY"


def test_email_status_is_off_regardless_of_configuration_when_disabled():
    smtp = SmtpConfig(host="smtp.gmail.com")
    assert email_delivery_status(False, "you@example.com", smtp) == "OFF"


def test_compose_final_summary_lists_all_six_picks_in_fixture_order():
    fixtures = [
        FinalPick("Arsenal", "Everton", "2-0"),
        FinalPick("Chelsea", "Leeds", "1-1"),
    ]
    subject, body = compose_final_summary(12, DEADLINE, fixtures, golden_goal_minute=34)
    assert "Round 12" in subject
    assert body.index("Arsenal 2-0 Everton") < body.index("Chelsea 1-1 Leeds")
    assert "Golden Goal: minute 34" in body


def test_compose_final_summary_warns_on_an_unmodellable_fixture_instead_of_guessing():
    fixtures = [
        FinalPick("Arsenal", "Everton", "2-0"),
        FinalPick("Newcastle", "Notts County", None, modellable=False),
    ]
    _, body = compose_final_summary(12, DEADLINE, fixtures, golden_goal_minute=None)
    assert "Arsenal 2-0 Everton" in body
    assert "Newcastle v Notts County" in body
    assert "no forecast" in body.lower()
    assert "enter this one yourself" in body.lower()


def test_compose_final_summary_omits_golden_goal_when_it_was_never_computed():
    fixtures = [FinalPick("Arsenal", "Everton", "2-0")]
    _, body = compose_final_summary(12, DEADLINE, fixtures, golden_goal_minute=None)
    assert "Golden Goal" not in body
