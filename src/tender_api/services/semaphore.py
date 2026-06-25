"""Semáforo de oportunidad: combina score, recomendación y plazo en un color accionable."""

from __future__ import annotations

from datetime import UTC, datetime


def days_remaining(deadline: datetime | None) -> int | None:
    """Días hasta el cierre (negativo si vencida). None si no hay deadline."""
    if deadline is None:
        return None
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return (deadline - datetime.now(UTC)).days


def traffic_light(total: int | None, recommendation: str | None, days: int | None) -> dict:
    """Devuelve {light, label, reason} (verde/amarillo/rojo/gris)."""
    rec = (recommendation or "").lower()

    if total is None and days is None:
        return {"light": "gray", "label": "⚪ Sin datos", "reason": "Faltan score y plazo."}

    # Rojo: vencida, NO-GO o score bajo.
    if days is not None and days < 0:
        return {"light": "red", "label": "🔴 Vencida", "reason": "El plazo ya ha vencido."}
    if rec == "no_go" or (total is not None and total < 50):
        return {"light": "red", "label": "🔴 Descartar", "reason": "NO-GO o score bajo."}

    # Amarillo: revisión/partner, score medio o plazo próximo.
    if rec in ("revisar", "partner") or (total is not None and total < 70) or (
        days is not None and days <= 7
    ):
        return {
            "light": "yellow",
            "label": "🟡 Revisar",
            "reason": "Score medio, partner/revisión o plazo próximo.",
        }

    # Verde: buen score y plazo operativo.
    if (total is not None and total >= 70) and (days is None or days > 7):
        return {
            "light": "green",
            "label": "🟢 Prioritaria",
            "reason": "Buen score y plazo operativo razonable.",
        }

    return {"light": "gray", "label": "⚪ Sin datos", "reason": "Datos insuficientes."}
