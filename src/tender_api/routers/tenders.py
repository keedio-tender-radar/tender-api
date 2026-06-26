"""Endpoints de licitaciones: ingesta idempotente, listado, ficha, top y urgentes."""

from __future__ import annotations

import csv
import io
import re
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from tender_contracts import Tender as TenderContract
from tender_contracts import TenderScore as ScoreContract

from tender_api.config import settings
from tender_api.database import get_session
from tender_api.models import (
    GeneratedDocument,
    Tender,
    TenderAction,
    TenderDecision,
    TenderDocument,
    TenderScore,
)
from tender_api.schemas import (
    AskRequest,
    DecisionCreate,
    TenderCreate,
    TenderWithScore,
    score_to_contract,
    tender_to_contract,
)
from tender_api.services import analysis_client, doc_client, semaphore, storage, visual_rag_client
from tender_api.services.learning import learning_insights

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
    row.duplicate_of = _find_duplicate(session, row)
    session.commit()
    session.refresh(row)
    return tender_to_contract(row)


def _find_duplicate(session: Session, row: Tender) -> str | None:
    """Detecta la misma licitación en OTRA fuente: mismo presupuesto exacto + CPV primario.

    Conservador (exige presupuesto y CPV no nulos) para no ocultar licitaciones distintas.
    """
    if not row.budget_amount or not row.cpv:
        return None
    primary = str(row.cpv[0])
    candidates = session.scalars(
        select(Tender).where(
            Tender.budget_amount == row.budget_amount,
            Tender.source != row.source,
            Tender.duplicate_of.is_(None),
            Tender.id != row.id,
        )
    ).all()
    for c in candidates:
        if c.cpv and str(c.cpv[0]) == primary:
            return c.id
    return None


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


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(422, f"Fecha inválida: {raw}") from exc


def _aware(dt: datetime) -> datetime:
    """Normaliza a tz-aware (UTC) para comparar deadlines sin romper en naive/aware."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


@router.get("/search", response_model=list[TenderWithScore])
def search_tenders(
    session: Session = Depends(get_session),
    status: str | None = Query(default=None),
    q: str | None = Query(default=None, description="Búsqueda por título (subcadena)."),
    order: str = Query(default="recent", description="recent | score"),
    source: str | None = Query(default=None),
    contracting_body: str | None = Query(default=None, description="Órgano (subcadena)."),
    recommendation: str | None = Query(default=None),
    traffic_light: str | None = Query(default=None, description="green|yellow|red|gray"),
    deadline_before: str | None = Query(default=None),
    deadline_after: str | None = Query(default=None),
    min_score: int | None = Query(default=None),
    max_score: int | None = Query(default=None),
    max_days_remaining: int | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Listado operativo con filtros (score, semáforo, plazo, órgano) y el último score."""
    dl_before, dl_after = _parse_dt(deadline_before), _parse_dt(deadline_after)

    stmt = select(Tender).where(Tender.duplicate_of.is_(None))
    if status:
        stmt = stmt.where(Tender.status == status)
    if q:
        stmt = stmt.where(Tender.title.ilike(f"%{q}%"))
    if source:
        stmt = stmt.where(Tender.source == source)
    if contracting_body:
        stmt = stmt.where(Tender.buyer.ilike(f"%{contracting_body}%"))

    items: list[tuple] = []
    for r in session.scalars(stmt).all():
        score = _latest_score(session, r.id)
        total = score.total if score else None
        rec = score.recommendation if score else None
        days = semaphore.days_remaining(r.deadline)
        light = semaphore.traffic_light(total, rec, days)["light"]

        if recommendation and (rec or "").lower() != recommendation.lower():
            continue
        if traffic_light and light != traffic_light:
            continue
        if min_score is not None and (total is None or total < min_score):
            continue
        if max_score is not None and (total is None or total > max_score):
            continue
        if max_days_remaining is not None and (days is None or days > max_days_remaining):
            continue
        if dl_before and (r.deadline is None or _aware(r.deadline) > _aware(dl_before)):
            continue
        if dl_after and (r.deadline is None or _aware(r.deadline) < _aware(dl_after)):
            continue
        items.append((r, score, total))

    if order == "score":
        items.sort(key=lambda x: (x[2] is None, -(x[2] or 0)))
    else:
        items.sort(key=lambda x: x[0].created_at, reverse=True)

    page = items[offset : offset + limit]
    return [
        TenderWithScore(
            tender=tender_to_contract(r), score=score_to_contract(s) if s else None
        )
        for r, s, _ in page
    ]


@router.get("/top", response_model=list[TenderWithScore])
def top_tenders(
    session: Session = Depends(get_session),
    limit: int = Query(default=5, ge=1, le=50),
):
    """Mejores oportunidades por último score (desc). Solo licitaciones ya puntuadas."""
    scored = []
    for tender in session.scalars(
        select(Tender).where(Tender.duplicate_of.is_(None))
    ).all():
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
        .where(Tender.duplicate_of.is_(None))
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

    last_ingested = session.scalar(select(func.max(Tender.created_at)))
    last_scored = session.scalar(select(func.max(TenderScore.created_at)))

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
        "last_ingested_at": last_ingested.isoformat() if last_ingested else None,
        "last_scored_at": last_scored.isoformat() if last_scored else None,
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

    # Texto del pliego (si doc-service está disponible): sirve al fallback extractivo y permite que
    # visual-rag indice al vuelo si aún no pre-ingestó el expediente.
    chunks: list[dict] = []
    if doc_client.is_configured() and tender.url:
        try:
            chunks = doc_client.extract(tender.url).get("chunks", [])
        except httpx.HTTPError:
            chunks = []
    document_text = "\n".join(c.get("content", "") for c in chunks)[:20000] or None

    if visual_rag_client.is_configured():
        try:
            res = visual_rag_client.ask(payload.question, tender.id, payload.top_k, document_text)
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
    if not chunks:
        raise HTTPException(502, "No se pudo extraer el pliego.")

    top = _rank_chunks(payload.question, chunks, payload.top_k)
    answer = top[0]["content"][:800] if top else None
    return {
        "backend": "extractive",
        "answer": answer,
        "sources": [
            {"section": c.get("section"), "content": c.get("content", "")[:600]} for c in top
        ],
    }


@router.get("/{tender_id}/traffic-light")
def get_traffic_light(tender_id: str, session: Session = Depends(get_session)) -> dict:
    """Semáforo de oportunidad (verde/amarillo/rojo/gris) a partir de score, recom. y plazo."""
    tender = _get_or_404(session, tender_id)
    score = _latest_score(session, tender_id)
    days = semaphore.days_remaining(tender.deadline)
    total = score.total if score else None
    rec = score.recommendation if score else None
    light = semaphore.traffic_light(total, rec, days)
    return {
        "external_tender_id": tender.source_id,
        "deadline": tender.deadline.isoformat() if tender.deadline else None,
        "final_score": total,
        "recommendation": rec,
        "traffic_light": light["light"],
        "traffic_light_label": light["label"],
        "traffic_light_reason": light["reason"],
        "days_remaining": days,
    }


@router.patch("/{tender_id}/deadline", response_model=TenderContract)
def update_deadline(
    tender_id: str, payload: dict, session: Session = Depends(get_session)
):
    """Actualiza manualmente la fecha final de la licitación (ISO 8601 en `deadline`)."""
    tender = _get_or_404(session, tender_id)
    raw = payload.get("deadline")
    if not raw:
        raise HTTPException(422, "Falta 'deadline' (ISO 8601).")
    try:
        tender.deadline = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(422, f"Fecha inválida: {exc}") from exc
    session.commit()
    session.refresh(tender)
    return tender_to_contract(tender)


@router.post("/{tender_id}/decision", status_code=201)
def record_decision(
    tender_id: str, payload: DecisionCreate, session: Session = Depends(get_session)
) -> dict:
    """Registra una decisión histórica (GO/NO-GO/…) y su resultado, para el aprendizaje."""
    _get_or_404(session, tender_id)
    row = TenderDecision(tender_id=tender_id, **payload.model_dump())
    session.add(row)
    session.commit()
    session.refresh(row)
    return {"id": row.id, "tender_id": tender_id, "decision": row.decision}


@router.get("/{tender_id}/learning-insights")
def get_learning_insights(tender_id: str, session: Session = Depends(get_session)) -> dict:
    """Compara con decisiones históricas similares (CPV/órgano/presupuesto)."""
    tender = _get_or_404(session, tender_id)
    return learning_insights(session, tender)


@router.post("/{tender_id}/mark-alerted", status_code=201)
def mark_alerted(tender_id: str, session: Session = Depends(get_session)) -> dict:
    """Marca una licitación como ya alertada (evita re-alertar). Uso interno del bot."""
    _get_or_404(session, tender_id)
    session.add(TenderAction(tender_id=tender_id, action="alerted", actor="alerts"))
    session.commit()
    return {"tender_id": tender_id, "alerted": True}


@router.get("/pending-alerts", response_model=list[TenderWithScore])
def pending_alerts(
    session: Session = Depends(get_session), limit: int = Query(default=10, ge=1, le=50)
):
    """Oportunidades GO aún no alertadas (sin acción 'alerted'). Para el push inmediato."""
    out: list[TenderWithScore] = []
    for t in session.scalars(select(Tender).where(Tender.duplicate_of.is_(None))).all():
        score = _latest_score(session, t.id)
        if not score or score.recommendation != "go":
            continue
        already = session.scalar(
            select(TenderAction).where(
                TenderAction.tender_id == t.id, TenderAction.action == "alerted"
            )
        )
        if already:
            continue
        out.append(
            TenderWithScore(tender=tender_to_contract(t), score=score_to_contract(score))
        )
        if len(out) >= limit:
            break
    return out


# Estructura estándar de la carpeta de expediente (cuando una licitación interesa).
_WORKSPACE_FOLDERS = [
    "00_originales",
    "01_analisis",
    "02_borradores_oferta",
    "03_administrativo",
    "04_tecnico",
    "05_economico",
    "99_presentacion",
]
_REQUIRED_DOCS = [
    "Informe Go/No-Go",
    "Resumen ejecutivo",
    "Memoria técnica",
    "Matriz de cumplimiento",
    "Checklist administrativo",
    "Declaración responsable",
    "Solvencia técnica",
    "Solvencia económica",
    "Oferta económica",
    "Anexos y modelos firmados",
]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60] or "expediente"


@router.post("/{tender_id}/mark-interesting")
def mark_interesting(tender_id: str, session: Session = Depends(get_session)) -> dict:
    """Marca la licitación como interesante y devuelve el manifiesto de su carpeta de expediente.

    El almacenamiento durable de ficheros es futuro (MinIO/S3); aquí se fija el estado, se
    registra la acción y se devuelve la estructura de carpeta + documentos a preparar.
    """
    tender = _get_or_404(session, tender_id)
    tender.status = "interested"
    session.add(
        TenderAction(tender_id=tender.id, action="interested", actor="dashboard",
                     note="mark-interesting")
    )
    session.commit()
    workspace = f"{tender.source_id}-{_slug(tender.title)}"
    return {
        "tender_id": tender.id,
        "status": tender.status,
        "workspace": workspace,
        "folders": _WORKSPACE_FOLDERS,
        "required_documents": _REQUIRED_DOCS,
        "note": "Carpeta de expediente lógica; el almacenamiento de ficheros (MinIO/S3) es futuro.",
    }


def _generate_and_store_drafts(session: Session, tender: Tender) -> list[GeneratedDocument]:
    """Genera (extrae pliego → ai-analysis) y persiste los borradores, reemplazando los previos."""
    document_text = None
    if doc_client.is_configured() and tender.url:
        try:
            chunks = doc_client.extract(tender.url).get("chunks", [])
            document_text = "\n".join(c.get("content", "") for c in chunks)[:20000] or None
        except httpx.HTTPError:
            document_text = None

    score = _latest_score(session, tender.id)
    score_payload = score_to_contract(score).model_dump(mode="json") if score else None
    result = analysis_client.generate_drafts(
        tender_to_contract(tender).model_dump(mode="json"), document_text, score_payload
    )

    for old in session.scalars(
        select(GeneratedDocument).where(GeneratedDocument.tender_id == tender.id)
    ).all():
        session.delete(old)
    rows = []
    for d in result.get("drafts", []):
        row = GeneratedDocument(
            tender_id=tender.id,
            kind=d.get("kind", "doc"),
            title=d.get("title", ""),
            content=d.get("content", ""),
            generated_by="llm" if document_text else "rule-based",
        )
        session.add(row)
        rows.append(row)
    session.commit()
    return rows


@router.post("/{tender_id}/generate-offer-drafts")
def generate_offer_drafts(tender_id: str, session: Session = Depends(get_session)) -> dict:
    """Genera y persiste los borradores de oferta (Go/No-Go, memoria, matriz, checklist)."""
    tender = _get_or_404(session, tender_id)
    if not analysis_client.is_configured():
        raise HTTPException(503, "analysis-service no configurado.")
    try:
        rows = _generate_and_store_drafts(session, tender)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Generación de borradores fallida: {exc}") from exc
    return {
        "tender_id": tender_id,
        "generated": [{"kind": r.kind, "title": r.title} for r in rows],
        "count": len(rows),
    }


# Carpeta destino de cada borrador dentro del expediente.
_DRAFT_FOLDER = {
    "go_no_go": "01_analisis",
    "resumen_ejecutivo": "01_analisis",
    "memoria_tecnica": "04_tecnico",
    "matriz_cumplimiento": "02_borradores_oferta",
    "checklist_administrativo": "03_administrativo",
}
_PENDING_HUMAN = [
    "Firma electrónica y certificados (ROLECE, DEUC, poderes)",
    "Solvencias acreditadas (técnica y económica)",
    "Oferta económica en el modelo oficial del pliego",
    "Revisión humana completa antes de presentar",
]


@router.post("/{tender_id}/prepare-submission-package")
def prepare_submission_package(tender_id: str, session: Session = Depends(get_session)) -> dict:
    """Empaqueta los borradores en las carpetas del expediente y devuelve el manifiesto.

    Genera los borradores si aún no existen. El almacenamiento de binarios es futuro (MinIO/S3).
    """
    tender = _get_or_404(session, tender_id)
    if not analysis_client.is_configured():
        raise HTTPException(503, "analysis-service no configurado.")

    rows = session.scalars(
        select(GeneratedDocument).where(GeneratedDocument.tender_id == tender_id)
    ).all()
    if not rows:
        try:
            rows = _generate_and_store_drafts(session, tender)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Generación de borradores fallida: {exc}") from exc

    package: dict[str, list[str]] = {f: [] for f in _WORKSPACE_FOLDERS}
    for r in rows:
        package[_DRAFT_FOLDER.get(r.kind, "02_borradores_oferta")].append(f"{r.kind}.md")

    tender.status = "interested"
    session.commit()
    manifest_lines = [f"# Expediente {tender.source_id} — {tender.title}", ""]
    for folder in _WORKSPACE_FOLDERS:
        manifest_lines.append(f"## {folder}")
        files = package[folder]
        if files:
            manifest_lines.extend(f"- {f}" for f in files)
        else:
            manifest_lines.append("- (vacío)")
    return {
        "tender_id": tender_id,
        "workspace": f"{tender.source_id}-{_slug(tender.title)}",
        "package": package,
        "documents": len(rows),
        "required_documents": _REQUIRED_DOCS,
        "pending_human": _PENDING_HUMAN,
        "manifest_md": "\n".join(manifest_lines),
        "note": "Borradores listos en el expediente. Subida de binarios y presentación: "
        "revisión humana + MinIO/S3.",
    }


@router.get("/{tender_id}/generated-documents")
def generated_documents(tender_id: str, session: Session = Depends(get_session)) -> list[dict]:
    """Lista los borradores de oferta generados para la licitación."""
    _get_or_404(session, tender_id)
    rows = session.scalars(
        select(GeneratedDocument)
        .where(GeneratedDocument.tender_id == tender_id)
        .order_by(GeneratedDocument.created_at)
    ).all()
    return [
        {
            "id": r.id,
            "kind": r.kind,
            "title": r.title,
            "content": r.content,
            "generated_by": r.generated_by,
        }
        for r in rows
    ]


@router.post("/{tender_id}/documents/upload", status_code=201)
async def upload_document(
    tender_id: str,
    file: UploadFile = File(...),
    folder: str = Form(default="00_originales"),
    session: Session = Depends(get_session),
) -> dict:
    """Sube un binario original (PCAP/PPT/anexo) al expediente en S3/MinIO."""
    tender = _get_or_404(session, tender_id)
    if not storage.is_configured():
        raise HTTPException(503, "Almacenamiento S3/MinIO no configurado (modo solo-lógico).")
    data = await file.read()
    key = f"{tender.id}/{folder}/{file.filename}"
    try:
        storage.put_bytes(key, data, file.content_type or "application/octet-stream")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Subida fallida: {exc}") from exc
    row = TenderDocument(
        tender_id=tender.id,
        folder=folder,
        filename=file.filename or "documento",
        storage_key=key,
        content_type=file.content_type,
        size=len(data),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return {"id": row.id, "filename": row.filename, "folder": row.folder, "size": row.size}


@router.get("/{tender_id}/documents")
def list_documents(tender_id: str, session: Session = Depends(get_session)) -> list[dict]:
    """Lista los binarios del expediente (con URL prefirmada si el almacenamiento está activo)."""
    _get_or_404(session, tender_id)
    rows = session.scalars(
        select(TenderDocument).where(TenderDocument.tender_id == tender_id)
    ).all()
    out = []
    for r in rows:
        item = {"id": r.id, "filename": r.filename, "folder": r.folder, "size": r.size}
        if storage.is_configured():
            try:
                item["url"] = storage.presigned_get(r.storage_key)
            except Exception:  # noqa: BLE001
                item["url"] = None
        out.append(item)
    return out


@router.get("/{tender_id}/required-documents")
def required_documents(tender_id: str, session: Session = Depends(get_session)) -> dict:
    """Documentos que típicamente exige la oferta (requiere revisión humana antes de presentar)."""
    tender = _get_or_404(session, tender_id)
    return {
        "external_tender_id": tender.source_id,
        "required_offer_documents": _REQUIRED_DOCS,
        "status": "draft_requirements",
        "note": "Lista estándar; ajústala según el pliego concreto antes de presentar.",
    }


@router.get("/{tender_id}", response_model=TenderContract)
def get_tender(tender_id: str, session: Session = Depends(get_session)):
    return tender_to_contract(_get_or_404(session, tender_id))
