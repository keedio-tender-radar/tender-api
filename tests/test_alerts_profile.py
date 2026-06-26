from tests.conftest import full_breakdown, make_tender


def _score(client, tid, rec):
    bd = full_breakdown()
    client.put(
        f"/api/tenders/{tid}/score",
        json={"total": sum(bd.values()), "breakdown": bd, "recommendation": rec},
    )


def test_pending_alerts(client):
    go = make_tender(client, source_id="GO")
    nogo = make_tender(client, source_id="NOGO")
    _score(client, go["id"], "go")
    _score(client, nogo["id"], "no_go")
    res = client.get("/api/tenders/pending-alerts").json()
    ids = {x["tender"]["id"] for x in res}
    assert go["id"] in ids and nogo["id"] not in ids
    # tras marcar 'alerted', ya no aparece
    client.post(f"/api/tenders/{go['id']}/mark-alerted")
    res2 = client.get("/api/tenders/pending-alerts").json()
    assert go["id"] not in {x["tender"]["id"] for x in res2}


def test_profile_get_default_and_update(client):
    p = client.get("/api/profile").json()
    assert "datos" in p["keywords_positive"]
    assert "72" in p["cpv_preferred"]
    upd = client.put("/api/profile", json={"keywords_positive": ["rag", "llm"], "areas": ["IA"]})
    assert upd.status_code == 200
    assert upd.json()["keywords_positive"] == ["rag", "llm"]
    # persistido
    assert client.get("/api/profile").json()["keywords_positive"] == ["rag", "llm"]
    # campos no enviados se conservan
    assert client.get("/api/profile").json()["cpv_preferred"] == ["72", "48"]
