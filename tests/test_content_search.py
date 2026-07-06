from tests.conftest import full_breakdown, make_tender


def _score(client, tid, summary):
    bd = full_breakdown()
    client.put(
        f"/api/tenders/{tid}/score",
        json={
            "total": sum(bd.values()),
            "breakdown": bd,
            "recommendation": "go",
            "factors": [],
            "hard_rules": [],
            "summary": summary,
        },
    )


def test_score_summary_fills_empty_tender_summary(client):
    t = make_tender(client, source_id="S-NOSUM")  # make_tender no fija summary
    assert client.get(f"/api/tenders/{t['id']}").json().get("summary") in (None, "")
    _score(client, t["id"], "Contrato de plataforma de datos con RAG y citas.")
    got = client.get(f"/api/tenders/{t['id']}").json()
    assert "RAG" in (got.get("summary") or "")


def test_score_summary_does_not_overwrite_existing(client):
    t = make_tender(client, source_id="S-SUM", summary="Resumen original del anuncio.")
    _score(client, t["id"], "Resumen distinto del analisis.")
    got = client.get(f"/api/tenders/{t['id']}").json()
    assert got["summary"] == "Resumen original del anuncio."


def test_search_matches_summary_content(client):
    make_tender(
        client, source_id="S-K8S", title="Servicios TI", summary="Despliegue en Kubernetes."
    )
    r = client.get("/api/tenders/search", params={"q": "Kubernetes"})
    titles = [x["tender"]["title"] for x in r.json()]
    assert "Servicios TI" in titles  # el término está en el resumen, no en el título


def test_search_filters_by_cpv_prefix(client):
    make_tender(client, source_id="C-72", cpv=["72300000"], title="TIC")
    make_tender(client, source_id="C-48", cpv=["48000000"], title="Software paquete")
    r = client.get("/api/tenders/search", params={"cpv": "72"})
    titles = [x["tender"]["title"] for x in r.json()]
    assert "TIC" in titles and "Software paquete" not in titles
