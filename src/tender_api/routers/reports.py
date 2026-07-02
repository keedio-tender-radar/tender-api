"""Informe por email (para dirección): radar de oportunidades + inteligencia de mercado.

Requiere email InsForge configurado (plan de pago) + DIGEST_EMAIL_TO. Protegido por RUN_TOKEN;
lo dispara un schedule. Si el email no está configurado, responde 503 sin fallar el cron.
"""

from __future__ import annotations

import html as _html

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from tender_api.config import settings
from tender_api.database import get_session
from tender_api.models import Award, Tender, TenderScore
from tender_api.routers.market import _competitors, _hhi, _pricing
from tender_api.services import email

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _top_go(session: Session, limit: int = 5) -> list[tuple[Tender, TenderScore]]:
    """Mejores oportunidades GO (último score por licitación, recomendación go)."""
    scores = session.scalars(select(TenderScore).order_by(TenderScore.created_at.desc())).all()
    seen: set[str] = set()
    out: list[tuple[Tender, TenderScore]] = []
    for s in scores:
        if s.tender_id in seen:
            continue
        seen.add(s.tender_id)
        if (s.recommendation or "").lower() != "go":
            continue
        tender = session.get(Tender, s.tender_id)
        if tender and not tender.duplicate_of:
            out.append((tender, s))
        if len(out) >= limit:
            break
    return out


def _build_html(session: Session) -> str:
    e = _html.escape
    parts = ["<h2>Keedio Tender Radar — informe</h2>"]

    parts.append("<h3>Mejores oportunidades (GO)</h3><ul>")
    top = _top_go(session)
    if not top:
        parts.append("<li>Sin oportunidades GO ahora mismo.</li>")
    for t, s in top:
        budget = f"{t.budget_amount:,.0f} €".replace(",", ".") if t.budget_amount else "s/d"
        parts.append(f"<li><b>[{s.total}]</b> {e(t.title[:90])} — {e(budget)}</li>")
    parts.append("</ul>")

    awards = list(session.scalars(select(Award)).all())
    if awards:
        pricing = _pricing(awards)
        hhi = _hhi(awards)
        comp = _competitors(awards, 5)
        baja = pricing["avg_baja"]
        baja_txt = f"{baja * 100:.1f}%" if baja is not None else "s/d"
        parts.append("<h3>Inteligencia de mercado</h3>")
        parts.append(
            f"<p>{len(awards)} adjudicaciones · baja media {baja_txt} · "
            f"mercado {e(hhi.get('label') or 's/d')} ({hhi.get('competitors')} competidores)</p>"
        )
        parts.append("<p><b>Competidores frecuentes:</b></p><ol>")
        for c in comp:
            share = f" · {c['share'] * 100:.0f}% cuota" if c.get("share") is not None else ""
            parts.append(f"<li>{e(c['supplier'])} — {c['wins']} contratos{e(share)}</li>")
        parts.append("</ol>")

    parts.append("<hr><p style='color:#888;font-size:12px'>Informe automático de Keedio Tender "
                 "Radar. Datos de fuentes públicas (PLACSP/TED).</p>")
    return "".join(parts)


@router.post("/email-digest")
def email_digest(
    session: Session = Depends(get_session), x_run_token: str = Header(default="")
) -> dict:
    """Envía el informe por email a DIGEST_EMAIL_TO. Para el scheduler."""
    if settings.run_token and x_run_token != settings.run_token:
        raise HTTPException(401, "run token inválido")
    if not email.is_configured():
        raise HTTPException(503, "Email no configurado (INSFORGE_ANON_KEY / DIGEST_EMAIL_TO).")
    html_body = _build_html(session)
    try:
        result = email.send("Keedio Tender Radar — informe", html_body)
    except Exception as exc:  # noqa: BLE001 — típicamente free-tier sin email de pago
        raise HTTPException(502, f"Envío de email falló: {exc}") from exc
    return {"sent_to": email.recipients(), "result": result}
