from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tender_api.database import Base
from tender_api.models import Award, Tender
from tender_api.routers.market import buyer_profile


def test_buyer_profile_combines_tenders_and_awards():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    session = sessionmaker(bind=eng)()

    session.add(
        Tender(
            source="placsp", source_id="T1", title="Plataforma", buyer="Ayto X",
            cpv=["72300000"], budget_amount=100000,
        )
    )
    session.add(
        Award(
            source="placsp", source_id="A1", buyer="Ayto X", cpv=["72"], cpv_division="72",
            budget_amount=100000, awarded_amount=80000, awarded_supplier="Alfa SL", num_bidders=4,
        )
    )
    session.commit()

    p = buyer_profile(session, "Ayto X")
    assert p["tenders_seen"] == 1
    assert "72" in p["recurring_cpv"]
    assert p["awards_count"] == 1
    assert p["avg_baja"] == 0.2  # (100000-80000)/100000
    assert p["avg_bidders"] == 4
    assert p["top_winners"][0]["supplier"] == "Alfa SL"

    assert buyer_profile(session, None) is None
    assert buyer_profile(session, "Órgano sin datos") is None
    session.close()
