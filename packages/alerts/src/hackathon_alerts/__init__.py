"""Saved-search and alert generation/delivery primitives."""

from .delivery import EmailSender, RetryPolicy, SmtpEmailSender, render_alert_email
from .events import AlertCandidate, change_alert, deadline_alerts, dedup_key, new_match_alert
from .repository import AlertRepository, saved_search_fingerprint

__all__ = [
    "AlertCandidate",
    "AlertRepository",
    "EmailSender",
    "RetryPolicy",
    "SmtpEmailSender",
    "change_alert",
    "deadline_alerts",
    "dedup_key",
    "new_match_alert",
    "render_alert_email",
    "saved_search_fingerprint",
]
