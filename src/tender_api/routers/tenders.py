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
    DailySnapshot,
    GeneratedDocument,
    Tender,
    TenderAction,
    TenderDecision,
    TenderDocument,
    TenderNote,
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
from tender_api.services import (
    analysis_client,
    doc_client,
    docgen,
    semaphore,
    storage,
    visual_rag_client,
    xlsxgen,
)
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


def _ics_escape(s: str) -> str:
    return (
        (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", " ")
    )


def _pliego_text(session: Session, tender: Tender, refresh: bool = False) -> str | None:
    """Texto del pliego, cacheado en tender.document_text. Lo extrae una vez y lo reutiliza.

    Evita re-extraer en cada análisis/borrador/plan. `refresh=True` fuerza una nueva extracción.
    """
    if tender.document_text and not refresh:
        return tender.document_text
    if not doc_client.is_configured() or not tender.url:
        return tender.document_text or None
    try:
        chunks = doc_client.extract(tender.url).get("chunks", [])
    except httpx.HTTPError:
        return tender.document_text or None
    text = "\n".join(c.get("content", "") for c in chunks)[:20000] or None
    if text:
        tender.document_text = text
        tender.document_extracted_at = datetime.now(UTC)
        session.commit()
    return text


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
    include_expired: bool = Query(default=False),
):
    """Mejores oportunidades ACTIVAS por último score (desc). Excluye vencidas por defecto.

    Web (Radar) y Telegram (digest) consumen este mismo endpoint → la misma foto diaria.
    """
    now = datetime.now(UTC)
    scored = []
    for tender in session.scalars(
        select(Tender).where(Tender.duplicate_of.is_(None))
    ).all():
        if not include_expired and tender.deadline and _aware(tender.deadline) < now:
            continue  # licitación vencida → fuera del ranking de activas
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


def _active_top_items(session: Session, limit: int) -> list[dict]:
    """Construye el ranking de activas (no vencidas, no duplicadas, puntuadas) con semáforo."""
    now = datetime.now(UTC)
    scored = []
    for t in session.scalars(select(Tender).where(Tender.duplicate_of.is_(None))).all():
        if t.deadline and _aware(t.deadline) < now:
            continue
        sc = _latest_score(session, t.id)
        if not sc:
            continue
        scored.append((t, sc))
    scored.sort(key=lambda p: p[1].total, reverse=True)
    items = []
    for t, sc in scored[:limit]:
        days = semaphore.days_remaining(t.deadline)
        light = semaphore.traffic_light(sc.total, sc.recommendation, days)
        items.append({
            "tender_id": t.id,
            "title": t.title,
            "source": t.source,
            "score": sc.total,
            "recommendation": sc.recommendation,
            "traffic_light": light["light"],
            "traffic_light_label": light["label"],
            "deadline": t.deadline.isoformat() if t.deadline else None,
            "days_remaining": days,
        })
    return items


@router.post("/daily-snapshot")
def create_daily_snapshot(
    session: Session = Depends(get_session),
    x_run_token: str | None = Header(default=None),
    limit: int = Query(default=15, ge=1, le=50),
) -> dict:
    """Guarda la foto diaria de activas top (histórico). Idempotente por fecha; para scheduler."""
    if settings.run_token and x_run_token != settings.run_token:
        raise HTTPException(401, "Token de ejecución inválido o ausente.")
    today = datetime.now(UTC).date()
    items = _active_top_items(session, limit)
    row = session.scalar(select(DailySnapshot).where(DailySnapshot.snapshot_date == today))
    if row:
        row.items = items
        row.count = len(items)
    else:
        row = DailySnapshot(snapshot_date=today, items=items, count=len(items))
        session.add(row)
    session.commit()
    number = session.scalar(select(func.count()).select_from(DailySnapshot)) or 0
    return {"date": today.isoformat(), "number": number, "count": len(items), "items": items}


@router.get("/daily-snapshots")
def list_daily_snapshots(
    session: Session = Depends(get_session), limit: int = Query(default=14, ge=1, le=90)
) -> list[dict]:
    """Histórico de fotos diarias (fecha, conteo, items) — para comparar días."""
    rows = session.scalars(
        select(DailySnapshot).order_by(DailySnapshot.snapshot_date.desc()).limit(limit)
    ).all()
    return [
        {
            "date": r.snapshot_date.isoformat(),
            "count": r.count,
            "items": r.items or [],
        }
        for r in rows
    ]


@router.get("/daily-snapshot")
def get_daily_snapshot(session: Session = Depends(get_session)) -> dict:
    """Última foto diaria persistida (web)."""
    row = session.scalar(select(DailySnapshot).order_by(DailySnapshot.snapshot_date.desc()))
    number = session.scalar(select(func.count()).select_from(DailySnapshot)) or 0
    if not row:
        return {"date": None, "number": 0, "count": 0, "items": []}
    return {
        "date": row.snapshot_date.isoformat(),
        "number": number,
        "count": row.count,
        "items": row.items or [],
    }


@router.get("/stats/market")
def market_stats(session: Session = Depends(get_session)) -> dict:
    """Inteligencia de mercado: top órganos, volumen mensual y presupuesto medio por fuente."""
    tenders = session.scalars(select(Tender).where(Tender.duplicate_of.is_(None))).all()

    by_buyer: dict[str, int] = {}
    by_month: dict[str, int] = {}
    budget_by_source: dict[str, list[float]] = {}
    for t in tenders:
        if t.buyer:
            by_buyer[t.buyer] = by_buyer.get(t.buyer, 0) + 1
        if t.created_at:
            ym = t.created_at.strftime("%Y-%m")
            by_month[ym] = by_month.get(ym, 0) + 1
        if t.budget_amount:
            budget_by_source.setdefault(t.source, []).append(t.budget_amount)

    top_buyers = dict(sorted(by_buyer.items(), key=lambda kv: kv[1], reverse=True)[:8])
    months = dict(sorted(by_month.items())[-6:])
    avg_budget_by_source = {
        s: round(sum(v) / len(v)) for s, v in budget_by_source.items() if v
    }
    return {
        "top_buyers": top_buyers,
        "by_month": months,
        "avg_budget_by_source": avg_budget_by_source,
    }


@router.get("/stats/market.csv")
def market_csv(session: Session = Depends(get_session)) -> Response:
    """Exporta la inteligencia de mercado a CSV (tipo;clave;valor)."""
    m = market_stats(session)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["tipo", "clave", "valor"])
    for k, v in m["top_buyers"].items():
        writer.writerow(["organo", k, v])
    for k, v in m["by_month"].items():
        writer.writerow(["volumen_mes", k, v])
    for k, v in m["avg_budget_by_source"].items():
        writer.writerow(["presupuesto_medio_fuente", k, v])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="mercado.csv"'},
    )


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


@router.post("/{tender_id}/pliego")
def extract_and_cache_pliego(tender_id: str, session: Session = Depends(get_session)) -> dict:
    """Extrae el pliego y lo CACHEA en la licitación (reutilizado por análisis/borradores/plan)."""
    tender = _get_or_404(session, tender_id)
    if not doc_client.is_configured():
        raise HTTPException(503, "tender-document-service no está configurado.")
    if not tender.url:
        raise HTTPException(422, "La licitación no tiene URL de documento.")
    text = _pliego_text(session, tender, refresh=True)
    return {
        "cached": bool(text),
        "chars": len(text or ""),
        "extracted_at": tender.document_extracted_at.isoformat()
        if tender.document_extracted_at
        else None,
    }


@router.get("/services")
def services_status() -> dict:
    """Estado de los servicios externos (doc-service, análisis, visual-rag, LLM)."""
    out = {
        "doc_service": doc_client.is_configured(),
        "analysis_service": analysis_client.is_configured(),
        "visual_rag": bool(settings.visual_rag_url),
        "llm": None,
    }
    if analysis_client.is_configured():
        try:
            base = settings.analysis_service_url.rstrip("/")
            h = httpx.get(f"{base}/health", timeout=8).json()
            out["llm"] = bool(h.get("llm_enabled"))
            out["llm_models"] = h.get("llm_models")
        except (httpx.HTTPError, ValueError):
            out["llm"] = None
    return out


@router.get("/calendar.ics")
def calendar_ics(session: Session = Depends(get_session)) -> Response:
    """Calendario (.ics) con los cierres de las licitaciones activas (suscribible)."""
    now = datetime.now(UTC)
    rows = session.scalars(
        select(Tender).where(Tender.duplicate_of.is_(None), Tender.deadline.is_not(None))
    ).all()
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//Keedio//Tender Radar//ES", "CALSCALE:GREGORIAN",
    ]
    for t in rows:
        dl = _aware(t.deadline)
        if dl < now - timedelta(days=1):
            continue
        score = _latest_score(session, t.id)
        tag = f"[{score.recommendation.upper()}] " if score else ""
        stamp = dl.strftime("%Y%m%dT%H%M%SZ")
        lines += [
            "BEGIN:VEVENT",
            f"UID:tender-{t.id}@keedio",
            f"DTSTAMP:{now.strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART:{stamp}",
            f"DTEND:{stamp}",
            f"SUMMARY:{_ics_escape(tag + (t.title or 'Licitación'))}",
            f"DESCRIPTION:{_ics_escape((t.buyer or '') + ' · ' + (t.url or ''))}",
            "BEGIN:VALARM",
            "TRIGGER:-P3D",
            "ACTION:DISPLAY",
            "DESCRIPTION:Cierre de licitación en 3 días",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return Response(
        content="\r\n".join(lines),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'inline; filename="tender-radar.ics"'},
    )


def _reanalyze_one(session: Session, tender: Tender) -> TenderScore:
    """Extrae el pliego, re-puntúa con su contenido y persiste el score. Lanza httpx.HTTPError."""
    document_text = _pliego_text(session, tender, refresh=True)
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
        summary=(result.get("analysis") or {}).get("summary"),
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


@router.get("/{tender_id}/duplicates")
def list_duplicates(tender_id: str, session: Session = Depends(get_session)) -> list[dict]:
    """Otras publicaciones de la misma licitación (otras fuentes) marcadas como duplicadas."""
    _get_or_404(session, tender_id)
    rows = session.scalars(
        select(Tender).where(Tender.duplicate_of == tender_id)
    ).all()
    return [
        {"id": r.id, "source": r.source, "source_id": r.source_id, "url": r.url, "title": r.title}
        for r in rows
    ]


@router.get("/{tender_id}/analysis")
def get_analysis(tender_id: str, session: Session = Depends(get_session)) -> dict:
    """Resumen y factores del análisis IA (del último score), para mostrar en la ficha."""
    _get_or_404(session, tender_id)
    score = _latest_score(session, tender_id)
    if not score:
        return {"summary": None, "factors": [], "recommendation": None}
    return {
        "summary": score.summary,
        "factors": score.factors or [],
        "recommendation": score.recommendation,
        "model_version": score.model_version,
    }


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


@router.post("/{tender_id}/mark-reminded", status_code=201)
def mark_reminded(tender_id: str, session: Session = Depends(get_session)) -> dict:
    """Marca una licitación como ya recordada (cierre próximo). Uso interno del bot."""
    _get_or_404(session, tender_id)
    session.add(TenderAction(tender_id=tender_id, action="reminded", actor="reminders"))
    session.commit()
    return {"tender_id": tender_id, "reminded": True}


@router.get("/closing-soon", response_model=list[TenderWithScore])
def closing_soon(
    session: Session = Depends(get_session),
    days: int = Query(default=7, ge=1, le=60),
):
    """Licitaciones en seguimiento (interested/partner) que cierran pronto y sin recordatorio."""
    now = datetime.now(UTC)
    limit_dt = now + timedelta(days=days)
    rows = session.scalars(
        select(Tender)
        .where(Tender.duplicate_of.is_(None))
        .where(Tender.status.in_(["interested", "partner"]))
        .where(Tender.deadline.is_not(None))
        .where(Tender.deadline >= now)
        .where(Tender.deadline <= limit_dt)
        .order_by(Tender.deadline.asc())
    ).all()
    out = []
    for t in rows:
        reminded = session.scalar(
            select(TenderAction).where(
                TenderAction.tender_id == t.id, TenderAction.action == "reminded"
            )
        )
        if reminded:
            continue
        score = _latest_score(session, t.id)
        out.append(
            TenderWithScore(
                tender=tender_to_contract(t),
                score=score_to_contract(score) if score else None,
            )
        )
    return out


@router.post("/{tender_id}/notes", status_code=201)
def add_note(tender_id: str, payload: dict, session: Session = Depends(get_session)) -> dict:
    """Añade una nota/comentario del equipo a la licitación."""
    _get_or_404(session, tender_id)
    body = (payload.get("body") or "").strip()
    if not body:
        raise HTTPException(422, "La nota no puede estar vacía.")
    row = TenderNote(tender_id=tender_id, author=payload.get("author"), body=body)
    session.add(row)
    session.commit()
    session.refresh(row)
    return {"id": row.id, "author": row.author, "body": row.body,
            "created_at": row.created_at.isoformat()}


@router.get("/{tender_id}/activity")
def activity(tender_id: str, session: Session = Depends(get_session)) -> list[dict]:
    """Historial unificado (acciones + decisiones + notas) en orden cronológico inverso."""
    _get_or_404(session, tender_id)
    events: list[dict] = []
    for a in session.scalars(
        select(TenderAction).where(TenderAction.tender_id == tender_id)
    ).all():
        events.append({
            "kind": "action", "at": a.created_at.isoformat(),
            "text": a.action, "actor": a.actor, "detail": a.note,
        })
    for d in session.scalars(
        select(TenderDecision).where(TenderDecision.tender_id == tender_id)
    ).all():
        outcome = f" → {d.outcome}" if d.outcome else ""
        events.append({
            "kind": "decision", "at": d.created_at.isoformat(),
            "text": f"{d.decision}{outcome}", "actor": None, "detail": d.reason,
        })
    for n in session.scalars(
        select(TenderNote).where(TenderNote.tender_id == tender_id)
    ).all():
        events.append({
            "kind": "note", "at": n.created_at.isoformat(),
            "text": n.body, "actor": n.author, "detail": None,
        })
    events.sort(key=lambda e: e["at"], reverse=True)
    return events


@router.get("/{tender_id}/notes")
def list_notes(tender_id: str, session: Session = Depends(get_session)) -> list[dict]:
    """Lista las notas de la licitación (más recientes primero)."""
    _get_or_404(session, tender_id)
    rows = session.scalars(
        select(TenderNote)
        .where(TenderNote.tender_id == tender_id)
        .order_by(TenderNote.created_at.desc())
    ).all()
    return [
        {"id": r.id, "author": r.author, "body": r.body, "created_at": r.created_at.isoformat()}
        for r in rows
    ]


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
    document_text = _pliego_text(session, tender)

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


@router.get("/{tender_id}/package.md")
def download_package_md(tender_id: str, session: Session = Depends(get_session)) -> Response:
    """Descarga todos los borradores del expediente concatenados en un único Markdown."""
    tender = _get_or_404(session, tender_id)
    rows = session.scalars(
        select(GeneratedDocument)
        .where(GeneratedDocument.tender_id == tender_id)
        .order_by(GeneratedDocument.created_at)
    ).all()
    parts = [f"# Paquete de oferta — {tender.source_id} · {tender.title}", ""]
    if not rows:
        parts.append("_Sin borradores generados aún._")
    for r in rows:
        parts.append(f"\n\n---\n\n{r.content}")
    md = "\n".join(parts)
    return Response(
        content=md,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{_slug(tender.title)}-paquete.md"'
        },
    )


def _drafts_for(session: Session, tender_id: str) -> list[GeneratedDocument]:
    return session.scalars(
        select(GeneratedDocument)
        .where(GeneratedDocument.tender_id == tender_id)
        .order_by(GeneratedDocument.created_at)
    ).all()


@router.get("/{tender_id}/plan.xlsx")
def download_project_plan(tender_id: str, session: Session = Depends(get_session)) -> Response:
    """Módulo de planificación/estimación en Excel: requerimientos, cronograma y costes."""
    from tender_api.routers.profile import _get_or_create

    tender = _get_or_404(session, tender_id)
    p = _get_or_create(session)
    data = xlsxgen.build_project_plan(
        tender, _latest_score(session, tender_id),
        team=list(p.team or []), months=p.project_months,
        rate=p.hourly_rate, margin=p.margin,
        drafts=_drafts_for(session, tender_id),
    )
    media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(
        content=data,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{_slug(tender.title)}-plan.xlsx"'
        },
    )


@router.get("/{tender_id}/package.docx")
def download_package_docx(tender_id: str, session: Session = Depends(get_session)) -> Response:
    """Paquete de oferta en Word (.docx) con marca Keedio, tablas y figuras."""
    from tender_api.routers.profile import _get_or_create

    tender = _get_or_404(session, tender_id)
    p = _get_or_create(session)
    data = docgen.build_docx(
        tender, _drafts_for(session, tender_id), _latest_score(session, tender_id),
        team=list(p.team or []), months=p.project_months, rate=p.hourly_rate, margin=p.margin,
    )
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f'attachment; filename="{_slug(tender.title)}-oferta.docx"'
        },
    )


@router.get("/{tender_id}/package.pdf")
def download_package_pdf(tender_id: str, session: Session = Depends(get_session)) -> Response:
    """Paquete de oferta en PDF con marca Keedio, tablas y figuras."""
    from tender_api.routers.profile import _get_or_create

    tender = _get_or_404(session, tender_id)
    p = _get_or_create(session)
    data = docgen.build_pdf(
        tender, _drafts_for(session, tender_id), _latest_score(session, tender_id),
        team=list(p.team or []), months=p.project_months, rate=p.hourly_rate, margin=p.margin,
    )
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{_slug(tender.title)}-oferta.pdf"'},
    )


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
