"""Aprendizaje del histórico: compara una licitación con decisiones anteriores similares."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from tender_api.models import Tender, TenderDecision


def _similarity(tender: Tender, other: Tender, decision: TenderDecision) -> float:
    """Puntúa la similitud entre la licitación y una decisión histórica (CPV/órgano/presupuesto)."""
    score = 0.0
    cpv_a = {str(c) for c in (tender.cpv or [])}
    cpv_b = {str(c) for c in (other.cpv or [])} | {str(c) for c in (decision.tags or [])}
    score += 2.0 * len(cpv_a & cpv_b)
    if tender.buyer and other.buyer and tender.buyer.lower() in other.buyer.lower():
        score += 2.0
    if tender.budget_amount and other.budget_amount:
        lo, hi = sorted((tender.budget_amount, other.budget_amount))
        if hi and lo / hi >= 0.7:  # presupuestos dentro de ±30%
            score += 1.0
    return score


def learning_insights(session: Session, tender: Tender, limit: int = 10) -> dict:
    """Agrega precedentes similares para ajustar Go/No-Go y precio."""
    ranked: list[tuple[float, TenderDecision, Tender]] = []
    for d in session.scalars(select(TenderDecision)).all():
        other = d.tender
        if other is None or other.id == tender.id:
            continue
        sim = _similarity(tender, other, d)
        if sim > 0:
            ranked.append((sim, d, other))
    ranked.sort(key=lambda x: x[0], reverse=True)
    top = ranked[:limit]

    submitted = [d for _, d, _ in top if (d.outcome or "") in ("ganada", "perdida", "presentada")]
    won = [d for _, d, _ in top if d.outcome == "ganada"]
    lost = [d for _, d, _ in top if d.outcome == "perdida"]
    scores = [d.final_score for _, d, _ in top if d.final_score is not None]
    avg = round(sum(scores) / len(scores), 1) if scores else None

    if top:
        rec = "Hay precedentes similares; usar el histórico para ajustar Go/No-Go y precio."
    else:
        rec = "Sin precedentes similares en el histórico."

    return {
        "external_tender_id": tender.source_id,
        "similar_count": len(top),
        "submitted_similar_count": len(submitted),
        "won_similar_count": len(won),
        "lost_similar_count": len(lost),
        "average_historical_score": avg,
        "recommendation": rec,
        "similar_tenders": [
            {
                "tender_id": other.id,
                "title": other.title,
                "buyer": other.buyer,
                "decision": d.decision,
                "outcome": d.outcome,
                "final_score": d.final_score,
                "similarity": sim,
            }
            for sim, d, other in top
        ],
    }
