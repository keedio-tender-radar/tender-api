"""Generación de documentos de oferta en Word (.docx) y PDF con marca Keedio.

Convierte los borradores (Markdown) en documentos con cabecera de marca, encabezados, listas,
tablas reales (p. ej. la matriz de cumplimiento) y una figura del desglose del scoring.
Dependencias pure-python (python-docx, fpdf2) → no requieren librerías de sistema.
"""

from __future__ import annotations

import functools
import re
from datetime import UTC, datetime
from io import BytesIO

from tender_api.config import settings

_HARD_RULE_TXT = {
    "cpv_excluded": "CPV fuera del perfil Keedio",
    "partner_needed": "Requiere partner para presentarse",
    "deadline_below_min": "Plazo por debajo del mínimo operativo",
}


def _days_remaining(deadline) -> int | None:
    if not deadline:
        return None
    dl = deadline if deadline.tzinfo else deadline.replace(tzinfo=UTC)
    return (dl - datetime.now(UTC)).days


def _risks_from_score(score) -> list[str]:
    """Riesgos reales derivados del análisis: factores negativos + reglas duras."""
    if not score:
        return []
    risks = [
        f.get("message", "")
        for f in (score.factors or [])
        if f.get("kind") == "negative" and f.get("message")
    ]
    risks += [_HARD_RULE_TXT.get(r, r) for r in (score.hard_rules or [])]
    return risks

BRAND = (0x5B, 0x94, 0xFF)

# Cronograma orientativo (plantilla): fases × meses con su tramo [inicio, fin] (1-indexado).
GANTT_PHASES: list[tuple[str, int, int]] = [
    ("Análisis y arranque", 1, 1),
    ("Diseño y arquitectura", 1, 2),
    ("Desarrollo / implantación", 2, 5),
    ("Pruebas y QA", 4, 5),
    ("Despliegue y formación", 5, 6),
    ("Soporte y cierre", 6, 6),
]
GANTT_MONTHS = 6

# Divisiones CPV (2 dígitos) para etiquetar la figura de CPV.
_CPV_DIV = {
    "30": "Equipos informáticos", "32": "Telecom.", "48": "Software/sistemas",
    "50": "Mantenimiento", "51": "Instalación", "64": "Telecomunicaciones",
    "71": "Ingeniería", "72": "Servicios TI", "73": "I+D", "79": "Consultoría",
    "80": "Formación", "85": "Salud", "90": "Medio ambiente",
}

# Organigrama propuesto (plantilla): nodo raíz → hijos.
ORG_ROOT = "Dirección de proyecto"
ORG_CHILDREN = ["Arquitecto/a", "Equipo desarrollo", "QA / Pruebas", "Soporte"]

# Mapa de riesgos 3×3 (probabilidad × impacto) → color de zona.
RISK_LEVELS = ["Baja", "Media", "Alta"]


def _scaled_phases(months: int) -> list[tuple[str, int, int]]:
    """Escala las fases plantilla (6 meses) a `months` meses, manteniendo proporciones."""
    if months == GANTT_MONTHS:
        return GANTT_PHASES
    out = []
    for name, s, e in GANTT_PHASES:
        ns = max(1, round((s - 1) / GANTT_MONTHS * months) + 1)
        ne = min(months, max(ns, round(e / GANTT_MONTHS * months)))
        out.append((name, ns, ne))
    return out


def _cpv_label(code: str) -> str:
    code = (code or "").strip()
    div = _CPV_DIV.get(code[:2])
    return f"{code} · {div}" if div else code


def _risk_color(prob: int, impact: int) -> tuple[int, int, int]:
    s = prob + impact  # 0..4
    if s <= 1:
        return (0x3F, 0xB9, 0x50)  # verde
    if s == 2:
        return (0xF2, 0xC1, 0x1E)  # ámbar
    return (0xE0, 0x5A, 0x4A)  # rojo


@functools.lru_cache(maxsize=2)
def _logo_png(url: str) -> bytes | None:
    """Descarga el logo (webp/png) y lo devuelve como PNG. Cacheado; None si falla."""
    if not url:
        return None
    try:
        import httpx
        from PIL import Image

        resp = httpx.get(url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        im = Image.open(BytesIO(resp.content)).convert("RGBA")
        out = BytesIO()
        im.save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return None

# Topes por dimensión del scoring (para la figura).
SCORE_DIMS: list[tuple[str, str, int]] = [
    ("technical_fit", "Encaje técnico", 30),
    ("budget_fit", "Presupuesto", 15),
    ("technical_solvency", "Solvencia técnica", 15),
    ("economic_solvency", "Solvencia económica", 10),
    ("deadline", "Plazo", 15),
    ("partner_need", "Necesidad de partner", 5),
    ("documental_complexity", "Complejidad documental", 4),
    ("contractual_risk", "Riesgo contractual", 5),
    ("incompatibility_risk", "Incompatibilidad", 5),
]


def _parse_table(lines: list[str], i: int) -> tuple[list[list[str]], int]:
    """Lee un bloque de tabla Markdown desde la línea i. Devuelve (filas, índice siguiente)."""
    rows = []
    while i < len(lines) and lines[i].lstrip().startswith("|"):
        cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
        if not re.match(r"^[-:\s|]+$", lines[i]):  # ignora la fila separadora ---|---
            rows.append(cells)
        i += 1
    return rows, i


# ----------------------------- Word (.docx) -----------------------------

def _docx_cover(doc, tender, score) -> None:
    """Portada del entregable: marca, etiqueta, título, datos clave, valoración y sello."""
    from docx.shared import Inches, Pt, RGBColor

    logo = _logo_png(settings.keedio_logo_url)
    if logo:
        doc.add_picture(BytesIO(logo), width=Inches(2.1))
    else:
        h = doc.add_paragraph()
        run = h.add_run("KEEDIO")
        run.bold = True
        run.font.size = Pt(24)
        run.font.color.rgb = RGBColor(*BRAND)

    label = doc.add_paragraph()
    lr = label.add_run("PAQUETE DE OFERTA")
    lr.bold = True
    lr.font.size = Pt(12)
    lr.font.color.rgb = RGBColor(*BRAND)

    title = doc.add_paragraph()
    tr = title.add_run(tender.title or "(sin título)")
    tr.bold = True
    tr.font.size = Pt(24)

    meta = doc.add_paragraph()
    meta.add_run(
        f"Expediente {tender.source_id} · Fuente {tender.source}\n"
        f"Presupuesto de licitación: {tender.budget_amount or 's/d'} {tender.currency}"
    ).italic = True

    days = _days_remaining(getattr(tender, "deadline", None))
    if days is not None:
        dl = tender.deadline
        ds = dl.date().isoformat() if hasattr(dl, "date") else str(dl)[:10]
        doc.add_paragraph().add_run(
            f"Plazo hasta presentación: {days} días (cierre {ds})"
        ).italic = True

    if score:
        rec = doc.add_paragraph()
        rr = rec.add_run(f"Valoración Go/No-Go: {score.total}/100 · {score.recommendation.upper()}")
        rr.bold = True
        rr.font.size = Pt(13)
        rr.font.color.rgb = RGBColor(*BRAND)

    stamp = doc.add_paragraph()
    st = stamp.add_run(
        f"Generado el {datetime.now(UTC).date().isoformat()} · Keedio Tender Radar · Confidencial"
    )
    st.font.size = Pt(9)
    st.font.color.rgb = RGBColor(0x99, 0x99, 0x99)
    doc.add_page_break()


def build_docx(tender, drafts, score, team=None, months=None, rate=None, margin=None) -> bytes:
    from docx import Document

    months = months or GANTT_MONTHS
    phases = _scaled_phases(months)
    doc = Document()
    _docx_cover(doc, tender, score)

    # Figura: desglose del scoring (tabla con barra).
    if score:
        doc.add_heading(
            f"Desglose Go/No-Go — {score.total}/100 ({score.recommendation})", level=2
        )
        table = doc.add_table(rows=1, cols=3)
        table.style = "Light Grid Accent 1"
        hdr = table.rows[0].cells
        hdr[0].text, hdr[1].text, hdr[2].text = "Dimensión", "Valor", "Nivel"
        bd = score.breakdown or {}
        for key, label, mx in SCORE_DIMS:
            v = bd.get(key, 0)
            cells = table.add_row().cells
            cells[0].text = label
            cells[1].text = f"{v}/{mx}"
            cells[2].text = "█" * round((v / mx if mx else 0) * 10)

    # Figura: cronograma (Gantt) con la duración configurada en el perfil.
    doc.add_heading(f"Cronograma orientativo ({months} meses)", level=2)
    g = doc.add_table(rows=1, cols=months + 1)
    g.style = "Light Grid Accent 1"
    head = g.rows[0].cells
    head[0].text = "Fase"
    for mth in range(1, months + 1):
        head[mth].text = f"M{mth}"
    for name, start, end in phases:
        cells = g.add_row().cells
        cells[0].text = name
        for mth in range(1, months + 1):
            cells[mth].text = "█" if start <= mth <= end else ""

    _docx_figures(doc, tender, score, team)
    _docx_plan(doc, drafts, team, rate, margin)

    # Borradores.
    for d in drafts:
        doc.add_page_break()
        _docx_markdown(doc, d.content or "")

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _shade(cell, rgb: tuple[int, int, int]) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), f"{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}")
    tcpr.append(shd)


def _docx_figures(doc, tender, score, team=None) -> None:
    from docx.shared import Pt, RGBColor

    children = team or ORG_CHILDREN

    # 1) Score total (gauge textual).
    if score:
        doc.add_heading("Score total", level=2)
        p = doc.add_paragraph()
        run = p.add_run(f"{score.total}/100")
        run.bold = True
        run.font.size = Pt(28)
        run.font.color.rgb = RGBColor(*BRAND)
        p.add_run(f"   ·   {score.recommendation.upper()}").font.size = Pt(12)

    # 2) CPV del expediente.
    cpvs = list(tender.cpv or [])
    if cpvs:
        doc.add_heading("CPV del expediente", level=2)
        for c in cpvs[:8]:
            doc.add_paragraph(_cpv_label(c), style="List Bullet")

    # Plazo real hasta presentación.
    days = _days_remaining(getattr(tender, "deadline", None))
    if days is not None:
        dl = tender.deadline
        ds = dl.date().isoformat() if hasattr(dl, "date") else str(dl)[:10]
        rp = doc.add_paragraph()
        rr = rp.add_run(f"Plazo hasta presentación: {days} días (cierre {ds})")
        rr.bold = True
        col = (
            (0xE0, 0x5A, 0x4A) if days <= 7
            else (0xF2, 0xC1, 0x1E) if days <= 21 else (0x3F, 0xB9, 0x50)
        )
        rr.font.color.rgb = RGBColor(*col)

    # 3) Riesgos REALES del análisis + matriz marco.
    doc.add_heading("Riesgos y mitigaciones", level=2)
    risks = _risks_from_score(score)
    if risks:
        for rk in risks[:8]:
            doc.add_paragraph(rk, style="List Bullet")
    else:
        doc.add_paragraph("Sin riesgos destacados en el análisis. Revisar el pliego completo.")
    doc.add_paragraph("Marco de evaluación (probabilidad × impacto):").italic = True
    rt = doc.add_table(rows=4, cols=4)
    rt.style = "Table Grid"
    rt.rows[0].cells[0].text = "Prob \\ Impacto"
    for impact in range(3):
        rt.rows[0].cells[impact + 1].text = RISK_LEVELS[impact]
    for pi in range(3):
        prob = 2 - pi
        rt.rows[pi + 1].cells[0].text = RISK_LEVELS[prob]
        for impact in range(3):
            cell = rt.rows[pi + 1].cells[impact + 1]
            _shade(cell, _risk_color(prob, impact))

    # 4) Organigrama del equipo (plantilla).
    doc.add_heading("Organigrama del equipo (plantilla)", level=2)
    top = doc.add_table(rows=1, cols=1)
    top.style = "Table Grid"
    top.rows[0].cells[0].text = ORG_ROOT
    _shade(top.rows[0].cells[0], BRAND)
    ct = doc.add_table(rows=1, cols=len(children))
    ct.style = "Table Grid"
    for i, name in enumerate(children):
        ct.rows[0].cells[i].text = name


def _plan_data(drafts, team, rate, margin):
    """(reqs[(req,horas)], total_horas, precio) a partir de la matriz + heurística de horas."""
    from tender_api.services import estimate

    team = [t for t in (team or []) if t] or ["Front", "Back", "Data", "QA"]
    rate = float(rate or 45.0)
    margin = float(margin if margin is not None else 0.2)
    reqs = estimate.requirements_from_drafts(drafts)
    rows = [(req, estimate.req_hours_total(req, team)) for req, _ in reqs]
    total = sum(h for _, h in rows)
    price = total * rate * 1.04 * (1 + margin)
    return rows, total, rate, margin, price


def _docx_plan(doc, drafts, team, rate, margin) -> None:
    from docx.shared import Pt

    rows, total, rate, margin, price = _plan_data(drafts, team, rate, margin)
    if not rows:
        return
    doc.add_heading("Plan de proyecto y estimación", level=2)
    t = doc.add_table(rows=1, cols=2)
    t.style = "Light Grid Accent 1"
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "Requisito", "Horas est."
    for req, h in rows:
        c = t.add_row().cells
        c[0].text, c[1].text = req, str(h)
    tot = t.add_row().cells
    tot[0].text, tot[1].text = "TOTAL", str(total)
    tot[0].paragraphs[0].runs[0].font.bold = True
    tot[1].paragraphs[0].runs[0].font.bold = True
    p = doc.add_paragraph()
    p.add_run(
        f"Horas totales: {total} · Coste/hora: {rate:.0f} € · Margen: {margin * 100:.0f}% · "
    )
    pr = p.add_run(f"Precio estimado: {price:,.0f} €")
    pr.bold = True
    pr.font.size = Pt(12)
    doc.add_paragraph(
        "Estimación orientativa por complejidad (S/M/L). Ajustable; el Excel permite el detalle "
        "por perfil."
    ).italic = True


def _docx_markdown(doc, md: str) -> None:
    from docx.shared import Pt

    lines = md.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("|"):
            rows, i = _parse_table(lines, i)
            if rows:
                t = doc.add_table(rows=0, cols=len(rows[0]))
                t.style = "Light List Accent 1"
                for r in rows:
                    cells = t.add_row().cells
                    for j, val in enumerate(r):
                        if j < len(cells):
                            cells[j].text = val
            continue
        if stripped.startswith("### "):
            doc.add_heading(stripped[4:], level=3)
        elif stripped.startswith("## "):
            doc.add_heading(stripped[3:], level=2)
        elif stripped.startswith("# "):
            doc.add_heading(stripped[2:], level=1)
        elif stripped.startswith(("- ", "* ")):
            doc.add_paragraph(
                stripped[2:].replace("[ ]", "☐").replace("[x]", "☑"), style="List Bullet"
            )
        else:
            p = doc.add_paragraph(stripped)
            p.paragraph_format.space_after = Pt(4)
        i += 1


# ----------------------------- PDF -----------------------------

def _latin1(text: str) -> str:
    """fpdf2 con fuentes core es latin-1: descarta lo no codificable (emoji)."""
    return text.encode("latin-1", "ignore").decode("latin-1")


def _mc(pdf, h: float, txt: str) -> None:
    """multi_cell asegurando la x al margen izquierdo (evita 'not enough horizontal space')."""
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(0, h, txt)


def _pdf_cover(pdf, tender, score) -> None:
    """Portada del entregable PDF: marca, etiqueta, título, datos clave, valoración y sello."""
    pdf.add_page()
    logo = _logo_png(settings.keedio_logo_url)
    if logo:
        try:
            pdf.image(BytesIO(logo), x=pdf.l_margin, y=24, h=16)
        except Exception:
            pass
    pdf.set_xy(pdf.l_margin, 96)
    pdf.set_text_color(*BRAND)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 6, "PAQUETE DE OFERTA", ln=1)
    pdf.set_draw_color(*BRAND)
    pdf.set_line_width(0.8)
    pdf.line(pdf.l_margin, pdf.get_y() + 1, pdf.l_margin + 45, pdf.get_y() + 1)
    pdf.set_line_width(0.2)
    pdf.ln(8)
    pdf.set_text_color(20, 20, 20)
    pdf.set_font("Helvetica", "B", 24)
    _mc(pdf, 11, _latin1(tender.title or "(sin titulo)"))
    pdf.ln(3)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(90, 90, 90)
    _mc(pdf, 6, _latin1(f"Expediente {tender.source_id}  -  Fuente {tender.source}"))
    _mc(
        pdf, 6,
        _latin1(f"Presupuesto de licitacion: {tender.budget_amount or 's/d'} {tender.currency}"),
    )
    days = _days_remaining(getattr(tender, "deadline", None))
    if days is not None:
        dl = tender.deadline
        ds = dl.date().isoformat() if hasattr(dl, "date") else str(dl)[:10]
        _mc(pdf, 6, _latin1(f"Plazo hasta presentacion: {days} dias (cierre {ds})"))
    if score:
        pdf.ln(2)
        pdf.set_text_color(*BRAND)
        pdf.set_font("Helvetica", "B", 13)
        _mc(
            pdf, 7,
            _latin1(f"Valoracion Go/No-Go: {score.total}/100 - {score.recommendation.upper()}"),
        )
    pdf.set_y(-28)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(150, 150, 150)
    _mc(
        pdf, 5,
        _latin1(
            f"Generado el {datetime.now(UTC).date().isoformat()}"
            "  -  Keedio Tender Radar  -  Confidencial"
        ),
    )


def build_pdf(tender, drafts, score, team=None, months=None, rate=None, margin=None) -> bytes:
    from fpdf import FPDF

    class _OfferPDF(FPDF):
        def footer(self) -> None:  # pie con paginación (salvo en la portada)
            if self.page_no() <= 1:
                return
            self.set_y(-12)
            self.set_draw_color(220, 224, 232)
            self.set_line_width(0.2)
            self.line(self.l_margin, self.get_y(), 210 - self.r_margin, self.get_y())
            self.set_y(-10)
            self.set_font("Helvetica", "", 7)
            self.set_text_color(150, 150, 150)
            self.cell(95, 5, _latin1("Confidencial - Keedio Tender Radar"), align="L", ln=0)
            self.cell(95, 5, f"Pag. {self.page_no() - 1}", align="R", ln=1)

    months = months or GANTT_MONTHS
    phases = _scaled_phases(months)
    pdf = _OfferPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    _pdf_cover(pdf, tender, score)
    pdf.add_page()

    # Cabecera de marca (breve) en la primera página de contenido.
    logo = _logo_png(settings.keedio_logo_url)
    if logo:
        try:
            pdf.image(BytesIO(logo), x=10, y=8, h=12)
        except Exception:
            logo = None
    if logo:
        pdf.set_xy(10, 22)
        pdf.set_text_color(*BRAND)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 6, "Tender Radar - Paquete de oferta", ln=1)
        pdf.set_draw_color(*BRAND)
        pdf.line(10, 30, 200, 30)
        pdf.ln(6)
    else:
        pdf.set_fill_color(*BRAND)
        pdf.rect(0, 0, 210, 18, "F")
        pdf.set_xy(10, 4)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(40, 10, "KEEDIO", ln=0)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 10, "Tender Radar - Paquete de oferta", ln=1)
        pdf.ln(10)

    # (título y metadatos del expediente ya figuran en la portada)

    # Figura: barras del scoring.
    if score:
        pdf.set_text_color(20, 20, 20)
        pdf.set_font("Helvetica", "B", 11)
        _mc(pdf, 6, _latin1(f"Desglose Go/No-Go - {score.total}/100 ({score.recommendation})"))
        bd = score.breakdown or {}
        pdf.set_font("Helvetica", "", 9)
        for key, label, mx in SCORE_DIMS:
            v = bd.get(key, 0)
            ratio = (v / mx) if mx else 0
            y = pdf.get_y()
            pdf.set_text_color(60, 60, 60)
            pdf.cell(55, 6, _latin1(label), ln=0)
            x = pdf.get_x()
            pdf.set_fill_color(230, 233, 240)
            pdf.rect(x, y + 1, 100, 4, "F")
            pdf.set_fill_color(*BRAND)
            pdf.rect(x, y + 1, 100 * ratio, 4, "F")
            pdf.set_xy(x + 102, y)
            pdf.cell(0, 6, f"{v}/{mx}", ln=1)
        pdf.ln(3)

    # Figura: cronograma (Gantt) con la duración del perfil.
    pdf.set_text_color(20, 20, 20)
    pdf.set_font("Helvetica", "B", 11)
    _mc(pdf, 6, f"Cronograma orientativo ({months} meses)")
    col_w = 22.0
    cell_w = (190 - col_w) / months
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(110, 110, 110)
    pdf.set_x(pdf.l_margin + col_w)
    for mth in range(1, months + 1):
        pdf.cell(cell_w, 5, f"M{mth}", border=0, align="C", ln=0)
    pdf.ln(5)
    for name, start, end in phases:
        y = pdf.get_y()
        pdf.set_text_color(60, 60, 60)
        pdf.set_x(pdf.l_margin)
        pdf.cell(col_w, 6, _latin1(name)[:14], ln=0)
        x0 = pdf.get_x()
        for mth in range(months):
            if start <= mth + 1 <= end:
                pdf.set_fill_color(*BRAND)
                pdf.rect(x0 + mth * cell_w + 1, y + 1, cell_w - 2, 4, "F")
        pdf.ln(6)
    pdf.ln(3)

    _pdf_figures(pdf, tender, score, team)
    _pdf_plan(pdf, drafts, team, rate, margin)

    for d in drafts:
        pdf.add_page()
        _pdf_markdown(pdf, d.content or "")

    out = pdf.output()
    return bytes(out)


def _pdf_figures(pdf, tender, score, team=None) -> None:
    children = team or ORG_CHILDREN
    # 1) Gauge del score total.
    if score:
        pdf.set_text_color(20, 20, 20)
        pdf.set_font("Helvetica", "B", 11)
        _mc(pdf, 6, "Score total")
        cx, cy, r = 30, pdf.get_y() + 16, 14
        pdf.set_draw_color(220, 224, 232)
        pdf.set_line_width(3)
        pdf.ellipse(cx - r, cy - r, 2 * r, 2 * r)
        rr = r * (score.total / 100) ** 0.5
        pdf.set_fill_color(*BRAND)
        pdf.ellipse(cx - rr, cy - rr, 2 * rr, 2 * rr, "F")
        pdf.set_line_width(0.2)
        pdf.set_xy(cx - r, cy - 3)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(2 * r, 6, str(score.total), align="C")
        pdf.set_xy(cx + r + 6, cy - 6)
        pdf.set_text_color(60, 60, 60)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 5, f"/100  -  {score.recommendation.upper()}", ln=1)
        pdf.set_y(cy + r + 4)

    # 2) CPV del expediente.
    cpvs = list(tender.cpv or [])
    if cpvs:
        pdf.set_text_color(20, 20, 20)
        pdf.set_font("Helvetica", "B", 11)
        _mc(pdf, 6, "CPV del expediente")
        pdf.set_font("Helvetica", "", 9)
        for c in cpvs[:6]:
            pdf.set_text_color(60, 60, 60)
            pdf.set_x(pdf.l_margin)
            pdf.set_fill_color(*BRAND)
            pdf.rect(pdf.l_margin, pdf.get_y() + 1.5, 3, 3, "F")
            pdf.set_x(pdf.l_margin + 5)
            pdf.cell(0, 6, _latin1(_cpv_label(c)), ln=1)
        pdf.ln(2)

    # Plazo real hasta presentación (dato del expediente).
    days = _days_remaining(getattr(tender, "deadline", None))
    if days is not None:
        color = (
            (0xE0, 0x5A, 0x4A) if days <= 7
            else (0xF2, 0xC1, 0x1E) if days <= 21 else (0x3F, 0xB9, 0x50)
        )
        pdf.set_text_color(*color)
        pdf.set_font("Helvetica", "B", 11)
        dl = tender.deadline
        ds = dl.date().isoformat() if hasattr(dl, "date") else str(dl)[:10]
        _mc(pdf, 6, _latin1(f"Plazo hasta presentacion: {days} dias (cierre {ds})"))
        pdf.ln(1)

    # 3) Mapa de riesgos: riesgos REALES del análisis + matriz marco.
    pdf.set_text_color(20, 20, 20)
    pdf.set_font("Helvetica", "B", 11)
    _mc(pdf, 6, "Riesgos y mitigaciones")
    risks = _risks_from_score(score)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(60, 60, 60)
    if risks:
        for rk in risks[:8]:
            _mc(pdf, 5, _latin1("  - " + rk))
    else:
        _mc(pdf, 5, "Sin riesgos destacados en el análisis. Revisar el pliego completo.")
    pdf.ln(1)
    pdf.set_text_color(110, 110, 110)
    pdf.set_font("Helvetica", "I", 8)
    _mc(pdf, 4, "Marco de evaluación (probabilidad x impacto):")
    cell = 18
    x0 = pdf.l_margin + 22
    y0 = pdf.get_y()
    pdf.set_font("Helvetica", "", 7)
    for pi in range(3):  # filas: probabilidad alta arriba
        prob = 2 - pi
        pdf.set_text_color(110, 110, 110)
        pdf.set_xy(pdf.l_margin, y0 + pi * cell + cell / 2 - 2)
        pdf.cell(22, 4, _latin1("P:" + RISK_LEVELS[prob]), ln=0)
        for impact in range(3):
            pdf.set_fill_color(*_risk_color(prob, impact))
            pdf.rect(x0 + impact * cell, y0 + pi * cell, cell - 1, cell - 1, "F")
    pdf.set_text_color(110, 110, 110)
    for impact in range(3):
        pdf.set_xy(x0 + impact * cell, y0 + 3 * cell + 1)
        pdf.cell(cell, 4, _latin1("I:" + RISK_LEVELS[impact]), align="C")
    pdf.set_y(y0 + 3 * cell + 8)

    # 4) Organigrama del equipo (plantilla).
    pdf.set_text_color(20, 20, 20)
    pdf.set_font("Helvetica", "B", 11)
    _mc(pdf, 6, "Organigrama del equipo (plantilla)")
    bw, bh = 50, 9
    top_x = pdf.l_margin + (190 - bw) / 2
    top_y = pdf.get_y()
    pdf.set_draw_color(*BRAND)
    pdf.set_fill_color(*BRAND)
    pdf.rect(top_x, top_y, bw, bh, "F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_xy(top_x, top_y + 1.5)
    pdf.cell(bw, 6, _latin1(ORG_ROOT), align="C")
    org = children[:6]
    n = len(org)
    cw = min(44.0, (190 - (n - 1) * 4) / n)
    total_w = n * cw + (n - 1) * 4
    cx0 = pdf.l_margin + (190 - total_w) / 2
    cy = top_y + bh + 12
    pdf.set_draw_color(150, 160, 180)
    pdf.line(top_x + bw / 2, top_y + bh, top_x + bw / 2, cy - 6)
    pdf.set_font("Helvetica", "", 7)
    for i, name in enumerate(org):
        bx = cx0 + i * (cw + 4)
        pdf.line(top_x + bw / 2, cy - 6, bx + cw / 2, cy - 6)
        pdf.line(bx + cw / 2, cy - 6, bx + cw / 2, cy)
        pdf.set_fill_color(230, 233, 240)
        pdf.rect(bx, cy, cw, bh, "F")
        pdf.set_text_color(40, 40, 40)
        pdf.set_xy(bx, cy + 1.5)
        pdf.cell(cw, 6, _latin1(name), align="C")
    pdf.set_y(cy + bh + 4)


def _pdf_plan(pdf, drafts, team, rate, margin) -> None:
    rows, total, rate, margin, price = _plan_data(drafts, team, rate, margin)
    if not rows:
        return
    pdf.add_page()
    pdf.set_text_color(20, 20, 20)
    pdf.set_font("Helvetica", "B", 12)
    _mc(pdf, 7, "Plan de proyecto y estimación")
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_x(pdf.l_margin)
    pdf.cell(150, 6, "Requisito", border=1, ln=0)
    pdf.cell(40, 6, "Horas est.", border=1, ln=1)
    pdf.set_font("Helvetica", "", 8)
    for req, h in rows:
        pdf.set_x(pdf.l_margin)
        pdf.cell(150, 6, _latin1(req)[:78], border=1, ln=0)
        pdf.cell(40, 6, str(h), border=1, ln=1)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_x(pdf.l_margin)
    pdf.cell(150, 6, "TOTAL", border=1, ln=0)
    pdf.cell(40, 6, str(total), border=1, ln=1)
    pdf.ln(3)
    pdf.set_font("Helvetica", "", 10)
    _mc(pdf, 5, _latin1(f"Coste/hora: {rate:.0f} EUR  -  Margen: {margin * 100:.0f}%"))
    pdf.set_text_color(*BRAND)
    pdf.set_font("Helvetica", "B", 12)
    _mc(pdf, 7, _latin1(f"Precio estimado: {price:,.0f} EUR"))
    pdf.set_text_color(110, 110, 110)
    pdf.set_font("Helvetica", "I", 8)
    _mc(pdf, 4, "Estimacion orientativa por complejidad (S/M/L); detalle por perfil en el Excel.")


def _pdf_markdown(pdf, md: str) -> None:
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("|"):
            rows, i = _parse_table(lines, i)
            _pdf_table(pdf, rows)
            continue
        if stripped.startswith("### "):
            pdf.set_font("Helvetica", "B", 11)
            _mc(pdf, 6, _latin1(stripped[4:]))
        elif stripped.startswith("## "):
            pdf.set_font("Helvetica", "B", 12)
            _mc(pdf, 7, _latin1(stripped[3:]))
        elif stripped.startswith("# "):
            pdf.set_font("Helvetica", "B", 14)
            _mc(pdf, 8, _latin1(stripped[2:]))
        elif stripped.startswith(("- ", "* ")):
            pdf.set_font("Helvetica", "", 10)
            _mc(pdf, 5, _latin1("  - " + stripped[2:]))
        else:
            pdf.set_font("Helvetica", "", 10)
            _mc(pdf, 5, _latin1(stripped))
        i += 1


def _pdf_table(pdf, rows: list[list[str]]) -> None:
    if not rows:
        return
    cols = len(rows[0])
    w = 190 / cols
    pdf.set_font("Helvetica", "", 8)
    for ri, r in enumerate(rows):
        pdf.set_font("Helvetica", "B" if ri == 0 else "", 8)
        for c in range(cols):
            txt = _latin1(r[c]) if c < len(r) else ""
            pdf.cell(w, 6, txt[:40], border=1, ln=0)
        pdf.ln(6)
    pdf.ln(2)
