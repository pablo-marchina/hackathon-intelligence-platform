from datetime import UTC, datetime, timedelta

from hackathon_alerts.events import change_alert, deadline_alerts


def test_change_alert_dedup_is_stable() -> None:
    a = change_alert(
        user_id="u", hackathon_id="h", change_id="c1", change_type="deadline", payload={}
    )
    b = change_alert(
        user_id="u", hackathon_id="h", change_id="c1", change_type="deadline", payload={"x": 1}
    )
    assert a.dedup_key == b.dedup_key


def test_deadline_windows_generate_idempotent_alerts() -> None:
    now = datetime(2026, 9, 22, tzinfo=UTC)
    alerts = deadline_alerts(
        user_id="u", hackathon_id="h", deadline=now + timedelta(hours=20), now=now
    )
    assert {a.payload["window"] for a in alerts} == {"7d", "1d"}
    assert len({a.dedup_key for a in alerts}) == len(alerts)
