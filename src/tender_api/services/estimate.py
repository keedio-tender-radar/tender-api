"""Estimación de esfuerzo por requisito (heurística por complejidad) y extracción de requisitos.

Compartido por el Excel (prerelleno de horas) y por los documentos Word/PDF (sección de plan).
Heurística simple y transparente: nivel S/M/L por palabras clave + longitud → horas base, repartidas
entre los roles del equipo con pesos por tipo de perfil. Es una SUGERENCIA editable, no un cálculo
cerrado.
"""

from __future__ import annotations

# Horas base por nivel de complejidad.
_BASE = {"S": 24, "M": 48, "L": 80}

_HIGH = (
    "integrac", "motor", "arquitectura", "migrac", "dashboard", "cuadro de mando", "multi",
    "plataforma", "seguridad", "interoper", "machine learning", " ia ", "etl", "tiempo real",
    "orquest", "pipeline", "modelo",
)
_LOW = ("ajuste", "mejora", "exportac", "etiqueta", "copia", "texto", "menor", "peque")


def complexity(req_text: str) -> str:
    t = f" {(req_text or '').lower()} "
    score = sum(1 for k in _HIGH if k in t) - sum(1 for k in _LOW if k in t)
    if len(t) > 90:
        score += 1
    if score >= 2:
        return "L"
    if score >= 1:
        return "M"
    return "S"


def _role_weight(role: str) -> int:
    r = (role or "").lower()
    if any(k in r for k in ("front", "back", "desarrollo", "dev", "full")):
        return 3
    mid = ("data", "ux", "diseñ", "arquitect", "sys", "sistema", "devops", "cloud")
    if any(k in r for k in mid):
        return 2
    if any(k in r for k in ("qa", "prueba", "test", "soporte", "pm", "jefe", "direcc", "gesti")):
        return 1
    return 2


def suggest_hours(req_text: str, team: list[str]) -> dict[str, int]:
    """Reparte las horas base del requisito entre los roles del equipo según su peso."""
    team = [t for t in (team or []) if t]
    if not team:
        return {}
    base = _BASE[complexity(req_text)]
    weights = {t: _role_weight(t) for t in team}
    total_w = sum(weights.values()) or 1
    return {t: max(1, round(base * w / total_w)) for t, w in weights.items()}


def req_hours_total(req_text: str, team: list[str]) -> int:
    return sum(suggest_hours(req_text, team).values())


def requirements_from_drafts(drafts) -> list[tuple[str, str]]:
    """Extrae (requisito, descripción) de las tablas de los borradores (matriz de cumplimiento)."""
    from tender_api.services.docgen import _parse_table

    out: list[tuple[str, str]] = []
    ordered = sorted(drafts or [], key=lambda d: 0 if "matriz" in (d.kind or "") else 1)
    for d in ordered:
        lines = (d.content or "").split("\n")
        i = 0
        while i < len(lines):
            if lines[i].lstrip().startswith("|"):
                rows, i = _parse_table(lines, i)
                if len(rows) >= 2 and "requisito" in " ".join(rows[0]).lower():
                    for r in rows[1:]:
                        req = (r[0] if r else "").strip()
                        desc = " · ".join(c.strip() for c in r[2:] if c.strip())
                        if req:
                            out.append((req, desc))
            else:
                i += 1
        if out:
            break
    return out
