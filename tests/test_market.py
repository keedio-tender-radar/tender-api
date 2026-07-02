from tests.conftest import make_tender


def _award(**over) -> dict:
    base = {
        "source": "ted",
        "source_id": "A-1",
        "title": "Servicios de desarrollo",
        "buyer": "Ayuntamiento X",
        "cpv": ["72000000"],
        "budget_amount": 100000.0,
        "awarded_amount": 80000.0,
        "awarded_supplier": "Empresa Alfa",
        "award_date": "2026-01-15",
    }
    base.update(over)
    return base


def test_ingest_awards_idempotent(client):
    a = _award()
    r1 = client.post("/api/market/awards", json=[a])
    assert r1.status_code == 200
    assert r1.json() == {"created": 1, "updated": 0, "total": 1}
    # Mismo source+source_id → update, no duplica.
    r2 = client.post("/api/market/awards", json=[{**a, "awarded_amount": 75000.0}])
    assert r2.json() == {"created": 0, "updated": 1, "total": 1}


def test_competitors_and_baja(client):
    client.post(
        "/api/market/awards",
        json=[
            _award(source_id="A-1", awarded_supplier="Alfa", budget_amount=100000,
                   awarded_amount=80000),
            _award(source_id="A-2", awarded_supplier="Alfa", budget_amount=200000,
                   awarded_amount=180000),
            _award(source_id="A-3", awarded_supplier="Beta", budget_amount=100000,
                   awarded_amount=95000),
        ],
    )
    body = client.get("/api/market/competitors", params={"cpv_division": "72"}).json()
    assert body["count"] == 3
    top = body["competitors"][0]
    assert top["supplier"] == "Alfa"
    assert top["wins"] == 2
    # baja media Alfa = (0.20 + 0.10) / 2 = 0.15
    assert top["avg_baja"] == 0.15


def test_competitor_market_share(client):
    client.post(
        "/api/market/awards",
        json=[
            _award(source_id="S-1", awarded_supplier="Alfa", awarded_amount=300000),
            _award(source_id="S-2", awarded_supplier="Beta", awarded_amount=100000),
        ],
    )
    r = client.get("/api/market/competitors", params={"cpv_division": "72"})
    comp = r.json()["competitors"]
    alfa = next(c for c in comp if c["supplier"] == "Alfa")
    # Cuota Alfa = 300000 / 400000 = 0.75
    assert alfa["share"] == 0.75


def test_pricing_avg_baja(client):
    client.post(
        "/api/market/awards",
        json=[
            _award(source_id="P-1", budget_amount=100000, awarded_amount=90000),  # baja 0.10
            _award(source_id="P-2", budget_amount=100000, awarded_amount=70000),  # baja 0.30
        ],
    )
    body = client.get("/api/market/pricing", params={"cpv_division": "72"}).json()
    assert body["with_baja"] == 2
    assert body["avg_baja"] == 0.20


def test_buyers_and_cpv_and_overview(client):
    client.post(
        "/api/market/awards",
        json=[
            _award(source_id="B-1", buyer="Órgano A", cpv=["72000000"], awarded_amount=50000),
            _award(source_id="B-2", buyer="Órgano A", cpv=["72500000"], awarded_amount=60000),
            _award(source_id="B-3", buyer="Órgano B", cpv=["48000000"], awarded_amount=40000),
        ],
    )
    buyers = client.get("/api/market/buyers").json()["buyers"]
    assert buyers[0]["buyer"] == "Órgano A" and buyers[0]["awards"] == 2
    cpv = client.get("/api/market/cpv").json()["divisions"]
    assert cpv[0]["cpv_division"] == "72" and cpv[0]["awards"] == 2
    ov = client.get("/api/market/overview").json()
    assert ov["awards"] == 3
    assert ov["top_buyer"]["buyer"] == "Órgano A"
    assert ov["top_cpv_division"]["cpv_division"] == "72"


def test_tender_market_context(client):
    t = make_tender(client, cpv=["72300000"])
    client.post(
        "/api/market/awards",
        json=[
            _award(source_id="C-1", cpv=["72000000"], awarded_supplier="Alfa",
                   budget_amount=100000, awarded_amount=80000),
        ],
    )
    ctx = client.get(f"/api/market/tender/{t['id']}/context").json()
    assert ctx["cpv_division"] == "72"
    assert ctx["likely_winners"][0]["supplier"] == "Alfa"
    assert ctx["expected_baja"] == 0.20


def test_tender_market_context_404(client):
    assert client.get("/api/market/tender/nope/context").status_code == 404


def test_competitor_profile(client):
    client.post(
        "/api/market/awards",
        json=[
            _award(source_id="P-1", awarded_supplier="INETUM, SL", buyer="Órgano A",
                   cpv=["72000000"], budget_amount=100000, awarded_amount=60000),
            _award(source_id="P-2", awarded_supplier="INETUM, S.L.", buyer="Órgano B",
                   cpv=["48000000"], budget_amount=200000, awarded_amount=250000),
        ],
    )
    prof = client.get("/api/market/competitor", params={"name": "INETUM S.L."}).json()
    assert prof["wins"] == 2  # fusiona variantes SL/S.L.
    assert len(prof["contracts"]) == 2
    assert {b["buyer"] for b in prof["by_buyer"]} == {"Órgano A", "Órgano B"}
    assert prof["avg_baja"] == 0.40  # solo P-1 tiene baja (0.40); P-2 adj>presup → excluida


def test_competitor_profile_404(client):
    assert client.get("/api/market/competitor", params={"name": "NoExiste"}).status_code == 404


def test_market_concentration_hhi(client):
    # Un solo adjudicatario acaparando → mercado concentrado (HHI alto).
    client.post(
        "/api/market/awards",
        json=[
            _award(source_id="H-1", awarded_supplier="Dominante", awarded_amount=900000),
            _award(source_id="H-2", awarded_supplier="Pequeña", awarded_amount=100000),
        ],
    )
    ov = client.get("/api/market/overview").json()
    conc = ov["concentration"]
    # HHI = 0.9² + 0.1² = 0.82 → concentrado
    assert conc["hhi"] == 0.82
    assert conc["label"] == "concentrado"
    assert conc["competitors"] == 2


def test_awards_csv_export(client):
    client.post(
        "/api/market/awards",
        json=[_award(source_id="C-1", awarded_supplier="Alfa", budget_amount=100000,
                     awarded_amount=60000)],
    )
    resp = client.get("/api/market/awards.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    body = resp.text
    assert "awarded_supplier" in body  # cabecera
    assert "Alfa" in body
    assert "40.0" in body  # baja % = (100000-60000)/100000


def test_baja_excludes_awarded_over_budget_consistently(client):
    # adjudicado > presupuesto (mismatch de escala) NO cuenta como baja en pricing NI en overview.
    client.post(
        "/api/market/awards",
        json=[
            _award(source_id="OK-1", budget_amount=100000, awarded_amount=60000),  # baja 0.40
            _award(source_id="BAD", budget_amount=100000, awarded_amount=410000),  # adj>presup
        ],
    )
    pricing = client.get("/api/market/pricing", params={"cpv_division": "72"}).json()
    assert pricing["with_baja"] == 1  # solo OK-1
    assert pricing["avg_baja"] == 0.40
    assert pricing["avg_awarded"] <= pricing["avg_budget"]  # coherente
    ov = client.get("/api/market/overview").json()
    assert ov["avg_baja"] == 0.40  # overview usa la MISMA definición


def test_competitor_name_normalization_merges_variants(client):
    # "SEIDOR CONSULTING, SL" y "…, S.L." son la misma empresa → deben fusionarse en un competidor.
    client.post(
        "/api/market/awards",
        json=[
            _award(source_id="N-1", awarded_supplier="SEIDOR CONSULTING, SL",
                   awarded_amount=1000),
            _award(source_id="N-2", awarded_supplier="SEIDOR CONSULTING, S.L.",
                   awarded_amount=2000),
        ],
    )
    r = client.get("/api/market/competitors", params={"cpv_division": "72"})
    comp = r.json()["competitors"]
    seidor = [c for c in comp if "SEIDOR" in c["supplier"].upper()]
    assert len(seidor) == 1
    assert seidor[0]["wins"] == 2
