from tests.conftest import full_breakdown, make_tender


def test_export_csv(client):
    a = make_tender(client, source_id="A", title="Plataforma de datos")
    make_tender(client, source_id="B", title="Servicio de limpieza")
    bd = full_breakdown()
    client.put(
        f"/api/tenders/{a['id']}/score",
        json={"total": sum(bd.values()), "breakdown": bd, "recommendation": "go"},
    )

    resp = client.get("/api/tenders/export.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "tender-radar.csv" in resp.headers["content-disposition"]
    body = resp.text
    assert body.splitlines()[0].startswith("source;source_id;title")
    assert "Plataforma de datos" in body
    assert ";go;" in body or ";go\r" in body or body.count("go") >= 1


def test_export_csv_respects_filter(client):
    make_tender(client, source_id="A", title="Plataforma de datos")
    make_tender(client, source_id="B", title="Servicio de limpieza")
    body = client.get("/api/tenders/export.csv?q=datos").text
    assert "Plataforma de datos" in body
    assert "limpieza" not in body
