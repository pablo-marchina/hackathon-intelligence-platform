"""Email outbox relay. Dispatch is opt-in; SMTP is never invoked by the poller."""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from sqlalchemy import Engine, create_engine, text

from hackathon_alerts.delivery import EmailSender, RetryPolicy, SmtpEmailSender, render_alert_email
from hackathon_alerts.repository import AlertRepository
from hackathon_shared.settings import get_settings


def dispatch_due_emails(
    *,
    publish: Callable[[str, str], None],
    engine: Engine | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    """Claim due rows and publish only token-fenced Celery task messages.

    A failed publish releases its claim. A process crash after claim is recovered
    by lease expiry. SMTP itself is only at-least-once (not exactly-once).
    """
    settings = get_settings()
    if not settings.alert_dispatch_enabled:
        return {"disabled": 1}
    owns_engine = engine is None
    if engine is None:
        engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            claims = AlertRepository(connection).claim_due_emails(
                limit=limit if limit is not None else settings.alert_dispatch_batch_size,
                lease_seconds=settings.alert_dispatch_claim_seconds,
            )
        published = 0
        failed = 0
        for claim in claims:
            try:
                publish(claim.delivery_id, claim.token)
                published += 1
            except Exception:
                # No user content or broker credentials are logged/returned.
                failed += 1
                with engine.begin() as connection:
                    AlertRepository(connection).release_dispatch_claim(claim)
        return {"claimed": len(claims), "published": published, "failed": failed}
    finally:
        if owns_engine:
            engine.dispose()


def deliver_pending_email(
    delivery_id: str,
    dispatch_token: str,
    *,
    sender: EmailSender | None = None,
    engine: Engine | None = None,
) -> dict[str, object]:
    """Send one claimed email while holding its DB row lock.

    Late, forged, already-finished and not-yet-due tasks never contact SMTP.
    A second task cannot send concurrently because the row lock is retained
    across the bounded SMTP call. An SMTP success followed by a DB crash can
    still be delivered twice: SMTP does not guarantee provider idempotency.
    """
    settings = get_settings()
    if not settings.alert_dispatch_enabled:
        return {"delivery_id": delivery_id, "status": "DISABLED"}
    owns_engine = engine is None
    if engine is None:
        engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            row = connection.execute(text("""
                SELECT d.id,d.alert_event_id,d.attempt_count,d.max_attempts,
                       a.user_id,a.alert_type,a.payload,a.hackathon_id,
                       h.slug,h.name
                FROM alert_deliveries d
                JOIN alert_events a ON a.id=d.alert_event_id
                JOIN users u ON u.id=a.user_id
                LEFT JOIN hackathons h ON h.id=a.hackathon_id
                WHERE d.id=CAST(:id AS uuid) AND d.channel='EMAIL'
                  AND d.status='PENDING'
                  AND d.dispatch_token=CAST(:token AS uuid)
                  AND d.dispatch_expires_at > clock_timestamp()
                  AND d.attempt_count < d.max_attempts
                  AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= clock_timestamp())
                FOR UPDATE OF d
            """), {"id": delivery_id, "token": dispatch_token}).mappings().first()
            if not row:
                return {"delivery_id": delivery_id, "status": "SKIPPED"}
            repo = AlertRepository(connection)
            # Read the current address *after* locking the user row. The initial
            # delivery SELECT can predate an email/preference update, so using
            # the email from that SELECT would risk mailing an old address.
            recipient = repo.authorized_email_recipient(
                user_id=str(row["user_id"]), alert_type=row["alert_type"],
                lock_user=True,
            )
            email_allowed = recipient is not None
            if email_allowed and row["alert_type"] == "new_match":
                from hackathon_worker.matches import allow_new_match_email

                email_allowed = allow_new_match_email(
                    connection, user_id=str(row["user_id"]),
                    hackathon_id=str(row["hackathon_id"]) if row["hackathon_id"] else None,
                    payload=dict(row["payload"] or {}),
                )
            if not email_allowed or recipient is None:
                repo.record_delivery_result(
                    delivery_id=delivery_id,
                    success=False,
                    error_message="email disabled or unavailable",
                    retry_policy=RetryPolicy(max_attempts=1),
                )
                return {"delivery_id": delivery_id, "status": "DISABLED"}
            subject, body = render_alert_email(
                alert_type=row["alert_type"],
                event_name=row["name"] or "Hackathon update",
                event_url=(
                    f"{settings.web_base_url.rstrip('/')}/hackathons/{row['slug']}"
                    if row["slug"] else settings.web_base_url
                ),
                payload=dict(row["payload"] or {}),
            )
            transport = sender or SmtpEmailSender(
                host=settings.smtp_host,
                port=settings.smtp_port,
                from_address=settings.smtp_from_address,
                username=settings.smtp_username,
                password=settings.smtp_password,
                starttls=settings.smtp_starttls,
                ssl_enabled=settings.smtp_ssl,
            )
            try:
                message_id = asyncio.run(transport.send(
                    to=recipient, subject=subject, text=body,
                    idempotency_key=f"alert-{row['alert_event_id']}",
                ))
                repo.record_delivery_result(
                    delivery_id=delivery_id, success=True, provider_message_id=message_id,
                    provider_response={"transport": "smtp"},
                    retry_policy=RetryPolicy(max_attempts=settings.alert_max_attempts),
                )
                return {"delivery_id": delivery_id, "status": "SENT", "provider_message_id": message_id}
            except Exception as exc:
                # SMTP exceptions can contain addresses/credentials/server responses.
                # Do not leak their text into queue results or persistent error fields.
                next_status = repo.record_delivery_result(
                    delivery_id=delivery_id, success=False,
                    error_message=type(exc).__name__,
                    provider_response={"transport": "smtp"},
                    retry_policy=RetryPolicy(max_attempts=settings.alert_max_attempts),
                )
                return {"delivery_id": delivery_id, "status": "RETRY" if next_status == "PENDING" else "FAILED"}
    finally:
        if owns_engine:
            engine.dispose()
