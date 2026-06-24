from tests.conftest import full_breakdown, make_tender


def _score(client, tender_id, recommendation="go", **bd):
    breakdown = full_breakdown(bd or None)
    client.put(
        f"/api/tenders/{tender_id}/score",
        json={
            "total": sum(breakdown.values()),
            "breakdown": breakdown,
            "recommendation": recommendation,
        },
    )


def test_stats_empty(client):
    s = client.get("/api/tenders/stats").json()
    assert s["total"] == 0
    assert s["go_count"] == 0
    assert s["go_budget_total"] == 0.0


def test_stats_aggregates(client):
    a = make_tender(client, source_id="A", source="placsp", budget_amount=620000)
    b = make_tender(client, source_id="B", source="ted", budget_amount=300000)
    c = make_tender(client, source_id="C", source="ted", budget_amount=90000)
    _score(client, a["id"], "go")
    _score(client, b["id"], "go")
    _score(client, c["id"], "no_go")

    s = client.get("/api/tenders/stats").json()
    assert s["total"] == 3
    assert s["by_source"]["ted"] == 2
    assert s["by_source"]["placsp"] == 1
    assert s["by_recommendation"]["go"] == 2
    assert s["go_count"] == 2
    # presupuesto de las dos GO (620000 + 300000)
    assert s["go_budget_total"] == 920000.0
