from datetime import UTC, datetime, timedelta

from tests.conftest import make_tender


def _iso(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_ingest_and_get(client):
    t = make_tender(client)
    assert t["source"] == "placsp"
    assert t["status"] == "discovered"
    got = client.get(f"/api/tenders/{t['id']}")
    assert got.status_code == 200
    assert got.json()["title"] == t["title"]


def test_ingest_is_idempotent(client):
    a = make_tender(client)
    b = make_tender(client, title="Título actualizado")
    assert a["id"] == b["id"]  # mismo source+source_id → misma fila
    assert b["title"] == "Título actualizado"
    assert len(client.get("/api/tenders").json()) == 1


def test_list_filter_by_status(client):
    make_tender(client, source_id="A")
    make_tender(client, source_id="B")
    assert len(client.get("/api/tenders").json()) == 2
    assert client.get("/api/tenders?status=scored").json() == []


def test_list_search_by_title(client):
    make_tender(client, source_id="A", title="Plataforma de datos")
    make_tender(client, source_id="B", title="Servicio de limpieza")
    res = client.get("/api/tenders?q=datos").json()
    assert len(res) == 1
    assert res[0]["title"] == "Plataforma de datos"
    # insensible a mayúsculas
    assert len(client.get("/api/tenders?q=LIMPIEZA").json()) == 1


def test_list_pagination(client):
    for i in range(5):
        make_tender(client, source_id=f"P{i}", title=f"Licitacion {i}")
    page1 = client.get("/api/tenders?limit=2&offset=0").json()
    page2 = client.get("/api/tenders?limit=2&offset=2").json()
    page3 = client.get("/api/tenders?limit=2&offset=4").json()
    assert len(page1) == 2 and len(page2) == 2 and len(page3) == 1
    # sin solapamiento entre páginas
    ids = {t["id"] for t in page1} | {t["id"] for t in page2} | {t["id"] for t in page3}
    assert len(ids) == 5


def test_get_unknown_404(client):
    assert client.get("/api/tenders/nope").status_code == 404


def test_urgent_uses_deadline_window(client):
    soon = make_tender(client, source_id="SOON", deadline=_iso(3))
    make_tender(client, source_id="FAR", deadline=_iso(60))

    res = client.get("/api/tenders/urgent?days=7")
    assert res.status_code == 200
    ids = [item["tender"]["id"] for item in res.json()]
    assert soon["id"] in ids  # cierre en 3 días → urgente
    assert len(ids) == 1  # la de 60 días queda fuera
