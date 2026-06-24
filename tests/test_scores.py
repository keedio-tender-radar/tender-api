from tests.conftest import full_breakdown, make_tender


def _score_body(**bd) -> dict:
    breakdown = full_breakdown(bd or None)
    return {
        "total": sum(breakdown.values()),
        "breakdown": breakdown,
        "recommendation": "go",
        "factors": [{"kind": "positive", "message": "CPV preferido"}],
    }


def test_put_and_get_score(client):
    t = make_tender(client)
    resp = client.put(f"/api/tenders/{t['id']}/score", json=_score_body())
    assert resp.status_code == 201, resp.text
    assert resp.json()["total"] == 92
    # marca la licitación como 'scored'
    assert client.get(f"/api/tenders/{t['id']}").json()["status"] == "scored"
    # get del último score
    got = client.get(f"/api/tenders/{t['id']}/score")
    assert got.json()["recommendation"] == "go"


def test_put_score_total_mismatch_422(client):
    t = make_tender(client)
    body = _score_body()
    body["total"] = 50  # no coincide con la suma del breakdown
    assert client.put(f"/api/tenders/{t['id']}/score", json=body).status_code == 422


def test_score_unknown_tender_404(client):
    assert client.get("/api/tenders/nope/score").status_code == 404
    assert client.put("/api/tenders/nope/score", json=_score_body()).status_code == 404


def test_top_orders_by_score(client):
    low = make_tender(client, source_id="LOW")
    high = make_tender(client, source_id="HIGH")
    client.put(f"/api/tenders/{low['id']}/score", json=_score_body(technical_fit=10, budget_fit=5))
    client.put(f"/api/tenders/{high['id']}/score", json=_score_body())
    top = client.get("/api/tenders/top").json()
    assert [item["tender"]["id"] for item in top][0] == high["id"]
    assert all(item["score"] is not None for item in top)
