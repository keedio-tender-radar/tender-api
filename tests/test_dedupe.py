from tests.conftest import make_tender


def test_cross_source_dedupe(client):
    # Misma licitación en PLACSP y TED: mismo presupuesto + mismo CPV primario.
    placsp = make_tender(client, source="placsp", source_id="P1",
                         budget_amount=620000.0, cpv=["72300000"])
    ted = make_tender(client, source="ted", source_id="T1",
                      budget_amount=620000.0, cpv=["72300000"], title="España – Plataforma datos")
    # la segunda (ted) queda marcada como duplicada de la primera
    assert client.get(f"/api/tenders/{ted['id']}").json()["id"] == ted["id"]
    # no aparece en search (se excluyen duplicados)
    res = client.get("/api/tenders/search?limit=100").json()
    ids = {x["tender"]["id"] for x in res}
    assert placsp["id"] in ids
    assert ted["id"] not in ids


def test_no_dedupe_different_cpv(client):
    a = make_tender(client, source="placsp", source_id="A", budget_amount=500000.0,
                    cpv=["72000000"])
    b = make_tender(client, source="ted", source_id="B", budget_amount=500000.0,
                    cpv=["48000000"])
    res = client.get("/api/tenders/search?limit=100").json()
    ids = {x["tender"]["id"] for x in res}
    assert a["id"] in ids and b["id"] in ids  # CPV distinto → no son duplicadas


def test_duplicates_endpoint(client):
    placsp = make_tender(client, source="placsp", source_id="C1",
                         budget_amount=730000.0, cpv=["48000000"])
    ted = make_tender(client, source="ted", source_id="D1",
                      budget_amount=730000.0, cpv=["48000000"])
    dups = client.get(f"/api/tenders/{placsp['id']}/duplicates").json()
    assert [d["id"] for d in dups] == [ted["id"]]
    assert dups[0]["source"] == "ted"
