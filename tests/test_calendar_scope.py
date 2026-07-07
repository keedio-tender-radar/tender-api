from tests.conftest import full_breakdown, make_tender


def _score_go(client, tid):
    bd = full_breakdown()
    client.put(
        f"/api/tenders/{tid}/score",
        json={
            "total": sum(bd.values()),
            "breakdown": bd,
            "recommendation": "go",
            "factors": [],
            "hard_rules": [],
        },
    )


def test_calendar_default_only_actionable(client):
    go = make_tender(client, source_id="CAL-GO")
    _score_go(client, go["id"])
    other = make_tender(client, source_id="CAL-OTHER")  # sin score, no seguimiento

    default = client.get("/api/tenders/calendar.ics").text
    assert f"tender-{go['id']}" in default  # GO → accionable
    assert f"tender-{other['id']}" not in default  # excluida del calendario por defecto

    all_cal = client.get("/api/tenders/calendar.ics?scope=all").text
    assert f"tender-{other['id']}" in all_cal  # scope=all las incluye


def test_calendar_includes_seguimiento(client):
    t = make_tender(client, source_id="CAL-SEG")
    client.post(f"/api/tenders/{t['id']}/mark-interesting")  # pasa a seguimiento
    default = client.get("/api/tenders/calendar.ics").text
    assert f"tender-{t['id']}" in default
