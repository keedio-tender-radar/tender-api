from datetime import UTC, datetime, timedelta

from tender_api.services import semaphore
from tests.conftest import full_breakdown, make_tender

_LOW_BD = {
    "technical_fit": 10,
    "budget_fit": 5,
    "technical_solvency": 5,
    "economic_solvency": 3,
    "deadline": 5,
    "partner_need": 3,
    "documental_complexity": 3,
    "contractual_risk": 3,
    "incompatibility_risk": 3,
}  # suma = 40


def _put_score(client, tid, breakdown, rec):
    client.put(
        f"/api/tenders/{tid}/score",
        json={"total": sum(breakdown.values()), "breakdown": breakdown, "recommendation": rec},
    )


def test_traffic_light_logic():
    assert semaphore.traffic_light(85, "go", 20)["light"] == "green"
    assert semaphore.traffic_light(85, "go", 3)["light"] == "yellow"
    assert semaphore.traffic_light(60, "revisar", 30)["light"] == "yellow"
    assert semaphore.traffic_light(30, "no_go", 30)["light"] == "red"
    assert semaphore.traffic_light(85, "go", -1)["light"] == "red"
    assert semaphore.traffic_light(None, None, None)["light"] == "gray"


def test_traffic_light_endpoint(client):
    t = make_tender(client)
    bd = full_breakdown()
    _put_score(client, t["id"], bd, "go")
    future = (datetime.now(UTC) + timedelta(days=20)).isoformat()
    client.patch(f"/api/tenders/{t['id']}/deadline", json={"deadline": future})
    body = client.get(f"/api/tenders/{t['id']}/traffic-light").json()
    assert body["traffic_light"] == "green"
    assert body["final_score"] == sum(bd.values())
    assert body["days_remaining"] >= 19


def test_decision_and_learning(client):
    # Histórico: licitación ganada con CPV y órgano.
    past = make_tender(client, source_id="PAST", cpv=["72000000"], buyer="Servicio de Salud")
    client.post(
        f"/api/tenders/{past['id']}/decision",
        json={"decision": "GO", "outcome": "ganada", "final_score": 88, "tags": ["salud"]},
    )
    # Nueva licitación similar (mismo CPV y órgano).
    new = make_tender(client, source_id="NEW", cpv=["72000000"], buyer="Servicio de Salud")
    insights = client.get(f"/api/tenders/{new['id']}/learning-insights").json()
    assert insights["similar_count"] == 1
    assert insights["won_similar_count"] == 1
    assert insights["average_historical_score"] == 88


def test_search_filters(client):
    a = make_tender(client, source_id="A", buyer="Diputacion de Madrid")
    b = make_tender(client, source_id="B", buyer="Ayuntamiento")
    _put_score(client, a["id"], full_breakdown(), "go")
    _put_score(client, b["id"], _LOW_BD, "no_go")

    # min_score deja solo A
    res = client.get("/api/tenders/search?min_score=70").json()
    ids = {x["tender"]["id"] for x in res}
    assert a["id"] in ids and b["id"] not in ids

    # traffic_light=red deja solo B (no_go)
    red = client.get("/api/tenders/search?traffic_light=red").json()
    assert {x["tender"]["id"] for x in red} == {b["id"]}

    # contracting_body filtra por órgano
    dip = client.get("/api/tenders/search?contracting_body=diputacion").json()
    assert {x["tender"]["id"] for x in dip} == {a["id"]}
