from datetime import UTC, datetime, timedelta

from tender_api.routers.tenders import _reminder_band


def test_reminder_band_escalation():
    now = datetime.now(UTC)
    assert _reminder_band(now + timedelta(hours=20)) == "1"  # < 1 día
    assert _reminder_band(now + timedelta(days=2)) == "3"  # ≤ 3 días
    assert _reminder_band(now + timedelta(days=5)) == "7"  # ≤ 7 días
    assert _reminder_band(now + timedelta(days=20)) is None  # lejano
    assert _reminder_band(now - timedelta(days=1)) is None  # vencido
    assert _reminder_band(None) is None
