"""Endpoints de licitaciones: ingesta idempotente, listado, ficha, top y urgentes."""

from __future__ import annotations

import csv
import io
import re
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session
from tender_contracts import Tender as TenderContract
from tender_contracts import TenderScore as ScoreContract

from tender_api.config import settings
from tender_api.database import get_session
from tender_api.models import Tender, TenderScore
from tender_api.schemas import (
    AskRequest,
    TenderCreate,
    TenderWithScore,
    score_to_contract,
    tender_to_contract,
)
from tender_api.services import analysis_client, doc_client, visual_rag_client

router = APIRouter(prefix="/api/tenders", tags=["tenders"])


def _latest_score(session: Session, tender_id: str) -> TenderScore | None:
    return session.scalars(
        select(TenderScore)
        .where(TenderScore.tender_id == tender_id)
        .order_by(TenderScore.created_at.desc())
    ).first()


def _get_or_404(session: Session, tender_id: str) -> Tender:
    row = session.get(Tender, tender_id)
    if not row:
        raise HTTPException(404, "Tender not found")
    return row


@router.post("", response_model=TenderContract, status_code=201)
def ingest_tender(payload: TenderCreate, session: Session = Depends(get_session)):
    """Crea o actualiza una licitación. Idempotente por (source, source_id)."""
    existing = session.scalars(
        select(Tender).where(
            Tender.source == payload.source, Tender.source_id == payload.source_id
        )
    ).first()

    data = payload.model_dump()
    if existing:
        for key, value in data.items():
            setattr(existing, key, value)
        row = existing
    else:
        row = Tender(**data)
        session.add(row)
    session.commit()
    session.refresh(row)
    return tender_to_contract(row)


@router.get("", response_model=list[TenderContract])
def list_tenders(
    session: Session = Depends(get_session),
    status: str | None = Query(default=None),
    q: str | None = Query(default=None, description="Búsqueda por título (subcadena)."),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(Tender).order_by(Tender.created_at.desc())
    if status:
        stmt = stmt.where(Tender.status == status)
    if q:
        stmt = stmt.where(Tender.title.ilike(f"%{q}%"))
    rows = session.scalars(stmt.offset(offset).limit(limit)).all()
    return [tender_to_contract(r) for r in rows]


@router.get("/search", response_model=list[TenderWithScore])
def search_tenders(
    session: Session = Depends(get_session),
    status: str | None = Query(default=None),
    q: str | None = Query(default=None, description="Búsqueda por título (subcadena)."),
    order: str = Query(default="recent", description="recent | score"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Listado con búsqueda/paginación que incluye el último score de cada licitación.

    `order=score` ordena por la nota del último score (desc, sin score al final).
    """
    stmt = select(Tender)
    if status:
        stmt = stmt.where(Tender.status == status)
    if q:
        stmt = stmt.where(Tender.title.ilike(f"%{q}%"))

    if order == "score":
        latest = (
            select(
                TenderScore.tender_id.label("tid"),
                func.max(TenderScore.created_at).label("mx"),
            )
            .group_by(TenderScore.tender_id)
            .subquery()
        )
        latest_total = (
            select(TenderScore.tender_id.label("tid"), TenderScore.total.label("total"))
            .join(
                latest,
                and_(
                    TenderScore.tender_id == latest.c.tid,
                    TenderScore.created_at == latest.c.mx,
                ),
            )
            .subquery()
        )
        stmt = stmt.outerjoin(latest_total, latest_total.c.tid == Tender.id).order_by(
            latest_total.c.total.desc().nullslast(), Tender.created_at.desc()
        )
    else:
        stmt = stmt.order_by(Tender.created_at.desc())

    rows = session.scalars(stmt.offset(offset).limit(limit)).all()
    out = []
    for r in rows:
        score = _latest_score(session, r.id)
        out.append(
            TenderWithScore(
                tender=tender_to_contract(r),
                score=score_to_contract(score) if score else None,
            )
        )
    return out


@router.get("/top", response_model=list[TenderWithScore])
def top_tenders(
    session: Session = Depends(get_session),
    limit: int = Query(default=5, ge=1, le=50),
):
    """Mejores oportunidades por último score (desc). Solo licitaciones ya puntuadas."""
    scored = []
    for tender in session.scalars(select(Tender)).all():
        score = _latest_score(session, tender.id)
        if score is not None:
            scored.append((tender, score))
    scored.sort(key=lambda pair: pair[1].total, reverse=True)
    return [
        TenderWithScore(tender=tender_to_contract(t), score=score_to_contract(s))
        for t, s in scored[:limit]
    ]


@router.get("/urgent", response_model=list[TenderWithScore])
def urgent_tenders(
    session: Session = Depends(get_session),
    days: int | None = Query(default=None, ge=1, le=90),
):
    """Licitaciones con cierre dentro de `days` (por defecto settings.urgent_days)."""
    horizon_days = days or settings.urgent_days
    now = datetime.now(UTC)
    limit_dt = now + timedelta(days=horizon_days)

    rows = session.scalars(
        select(Tender)
        .where(Tender.deadline.is_not(None))
        .where(Tender.deadline >= now)
        .where(Tender.deadline <= limit_dt)
        .order_by(Tender.deadline.asc())
    ).all()
    return [
        TenderWithScore(
            tender=tender_to_contract(t),
            score=(s := _latest_score(session, t.id)) and score_to_contract(s),
        )
        for t in rows
    ]


@router.get("/export.csv")
def export_csv(
    session: Session = Depends(get_session),
    status: str | None = Query(default=None),
    q: str | None = Query(default=None),
):
    """Exporta las licitaciones (con su último score) a CSV, respetando los filtros."""
    stmt = select(Tender).order_by(Tender.created_at.desc())
    if status:
        stmt = stmt.where(Tender.status == status)
    if q:
        stmt = stmt.where(Tender.title.ilike(f"%{q}%"))
    rows = session.scalars(stmt.limit(1000)).all()

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(
        ["source", "source_id", "title", "buyer", "budget_amount", "currency",
         "deadline", "status", "score", "recommendation", "url"]
    )
    for r in rows:
        score = _latest_score(session, r.id)
        writer.writerow(
            [
                r.source, r.source_id, r.title, r.buyer or "",
                "" if r.budget_amount is None else r.budget_amount,
                r.currency,
                r.deadline.isoformat() if r.deadline else "",
                r.status,
                "" if score is None else score.total,
                "" if score is None else score.recommendation,
                r.url or "",
            ]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="tender-radar.csv"'},
    )


@router.get("/stats")
def stats(session: Session = Depends(get_session)) -> dict:
    """Agregados para el panel de visión general (conteos y presupuesto de oportunidades)."""

    def _grouped(column) -> dict[str, int]:
        rows = session.execute(select(column, func.count()).group_by(column)).all()
        return {str(k): int(n) for k, n in rows}

    total = session.scalar(select(func.count()).select_from(Tender)) or 0
    by_status = _grouped(Tender.status)
    by_source = _grouped(Tender.source)
    by_recommendation = _grouped(TenderScore.recommendation)

    go_budget_total = (
        session.scalar(
            select(func.coalesce(func.sum(Tender.budget_amount), 0.0))
            .select_from(Tender)
            .join(TenderScore, TenderScore.tender_id == Tender.id)
            .where(TenderScore.recommendation == "go")
        )
        or 0.0
    )

    scored_count = session.scalar(select(func.count()).select_from(TenderScore)) or 0
    avg_score = session.scalar(select(func.avg(TenderScore.total))) or 0

    # Top CPV (las listas CPV son JSON por licitación → se agregan en Python; volumen pequeño).
    counter: dict[str, int] = {}
    for (cpvs,) in session.execute(select(Tender.cpv)).all():
        for code in cpvs or []:
            counter[code] = counter.get(code, 0) + 1
    by_cpv = dict(sorted(counter.items(), key=lambda kv: kv[1], reverse=True)[:8])

    return {
        "total": total,
        "by_status": by_status,
        "by_source": by_source,
        "by_recommendation": by_recommendation,
        "by_cpv": by_cpv,
        "go_count": by_recommendation.get("go", 0),
        "go_budget_total": float(go_budget_total),
        "scored_count": scored_count,
        "avg_score": round(float(avg_score)),
    }


@router.post("/{tender_id}/extract")
def extract_document(tender_id: str, session: Session = Depends(get_session)) -> dict:
    """Extrae el texto del documento del anuncio vía tender-document-service."""
    tender = _get_or_404(session, tender_id)
    if not doc_client.is_configured():
        raise HTTPException(503, "tender-document-service no está configurado.")
    if not tender.url:
        raise HTTPException(422, "La licitación no tiene URL de documento.")
    try:
        result = doc_client.extract(tender.url)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Extracción fallida: {exc}") from exc
    # Limita los fragmentos devueltos para no inflar la respuesta.
    result["chunks"] = (result.get("chunks") or [])[:5]
    return result


def _reanalyze_one(session: Session, tender: Tender) -> TenderScore:
    """Extrae el pliego, re-puntúa con su contenido y persiste el score. Lanza httpx.HTTPError."""
    extraction = doc_client.extract(tender.url)
    chunks = extraction.get("chunks", [])
    document_text = "\n".join(c.get("content", "") for c in chunks)[:20000]
    result = analysis_client.analyze(
        tender_to_contract(tender).model_dump(mode="json"), document_text
    )
    sc = result["score"]
    row = TenderScore(
        tender_id=tender.id,
        total=sc["total"],
        breakdown=sc["breakdown"],
        recommendation=sc["recommendation"],
        hard_rules=sc.get("hard_rules", []),
        factors=sc.get("factors", []),
        model_version="1.0.0+doc" if result.get("used_document") else "1.0.0",
    )
    session.add(row)
    tender.status = "scored"
    session.commit()
    session.refresh(row)
    return row


@router.post("/reanalyze-relevant")
def reanalyze_relevant(
    session: Session = Depends(get_session),
    x_run_token: str | None = Header(default=None),
    limit: int = Query(default=5, ge=1, le=20),
):
    """Re-analiza con su pliego las licitaciones relevantes (GO/REVISAR) aún no basadas en doc.

    Pensado para el scheduler diario. Protegido por X-Run-Token si `run_token` está configurado.
    """
    if settings.run_token and x_run_token != settings.run_token:
        raise HTTPException(401, "Token de ejecución inválido o ausente.")
    if not doc_client.is_configured() or not analysis_client.is_configured():
        raise HTTPException(503, "doc-service o analysis-service no configurados.")

    candidates: list[Tender] = []
    for tender in session.scalars(select(Tender).where(Tender.url.is_not(None))).all():
        score = _latest_score(session, tender.id)
        if (
            score is not None
            and score.recommendation in ("go", "revisar")
            and not score.model_version.endswith("+doc")
        ):
            candidates.append(tender)
        if len(candidates) >= limit:
            break

    reanalyzed, errors = 0, []
    for tender in candidates:
        try:
            _reanalyze_one(session, tender)
            reanalyzed += 1
        except Exception as exc:  # noqa: BLE001 — una con error no tumba el lote
            session.rollback()
            errors.append(f"{tender.id}: {type(exc).__name__}")
    return {"candidates": len(candidates), "reanalyzed": reanalyzed, "errors": errors}


@router.post("/{tender_id}/reanalyze", response_model=ScoreContract)
def reanalyze(tender_id: str, session: Session = Depends(get_session)):
    """Extrae el pliego y re-puntúa la licitación con su contenido (doc-service + ai-analysis)."""
    tender = _get_or_404(session, tender_id)
    if not doc_client.is_configured() or not analysis_client.is_configured():
        raise HTTPException(503, "doc-service o analysis-service no configurados.")
    if not tender.url:
        raise HTTPException(422, "La licitación no tiene URL de documento.")
    try:
        row = _reanalyze_one(session, tender)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Re-análisis fallido: {exc}") from exc
    return score_to_contract(row)


def _rank_chunks(question: str, chunks: list[dict], top_k: int) -> list[dict]:
    """QA extractivo: ordena los fragmentos por solape de palabras con la pregunta."""
    qwords = {w for w in re.findall(r"\w+", question.lower()) if len(w) > 2}
    scored = []
    for c in chunks:
        cwords = {w for w in re.findall(r"\w+", (c.get("content") or "").lower()) if len(w) > 2}
        overlap = len(qwords & cwords)
        if overlap:
            scored.append((overlap, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:top_k]]


@router.post("/{tender_id}/ask")
def ask(tender_id: str, payload: AskRequest, session: Session = Depends(get_session)) -> dict:
    """Pregunta sobre el pliego. Usa tender-visual-rag si está configurado; si no, QA extractivo."""
    tender = _get_or_404(session, tender_id)

    if visual_rag_client.is_configured():
        try:
            res = visual_rag_client.ask(payload.question, tender.id, payload.top_k)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"visual-rag falló: {exc}") from exc
        return {
            "backend": "visual-rag",
            "answer": res.get("answer"),
            "sources": res.get("sources") or res.get("hits") or [],
        }

    # Fallback extractivo sobre el texto del pliego (doc-service).
    if not doc_client.is_configured():
        raise HTTPException(503, "Ni visual-rag ni doc-service configurados.")
    if not tender.url:
        raise HTTPException(422, "La licitación no tiene URL de documento.")
    try:
        extraction = doc_client.extract(tender.url)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Extracción fallida: {exc}") from exc

    top = _rank_chunks(payload.question, extraction.get("chunks", []), payload.top_k)
    answer = top[0]["content"][:800] if top else None
    return {
        "backend": "extractive",
        "answer": answer,
        "sources": [
            {"section": c.get("section"), "content": c.get("content", "")[:600]} for c in top
        ],
    }


@router.get("/{tender_id}", response_model=TenderContract)
def get_tender(tender_id: str, session: Session = Depends(get_session)):
    return tender_to_contract(_get_or_404(session, tender_id))
