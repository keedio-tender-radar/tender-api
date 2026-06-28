"""Generación de documentos de oferta en Word (.docx) y PDF con marca Keedio.

Convierte los borradores (Markdown) en documentos con cabecera de marca, encabezados, listas,
tablas reales (p. ej. la matriz de cumplimiento) y una figura del desglose del scoring.
Dependencias pure-python (python-docx, fpdf2) → no requieren librerías de sistema.
"""

from __future__ import annotations

import functools
import re
from io import BytesIO

from tender_api.config import settings

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

def build_docx(tender, drafts, score) -> bytes:
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor

    doc = Document()

    # Cabecera de marca: logo real si se puede descargar; si no, marca textual.
    logo = _logo_png(settings.keedio_logo_url)
    if logo:
        doc.add_picture(BytesIO(logo), width=Inches(1.9))
    else:
        h = doc.add_paragraph()
        run = h.add_run("KEEDIO")
        run.bold = True
        run.font.size = Pt(22)
        run.font.color.rgb = RGBColor(*BRAND)
    sub = doc.add_paragraph()
    sr = sub.add_run("Tender Radar · Paquete de oferta")
    sr.font.size = Pt(11)
    sr.font.color.rgb = RGBColor(0x80, 0x80, 0x80)
    doc.add_heading(tender.title or "(sin título)", level=1)
    meta = doc.add_paragraph()
    meta.add_run(
        f"Expediente: {tender.source_id} · Fuente: {tender.source} · "
        f"Presupuesto: {tender.budget_amount or 's/d'} {tender.currency}"
    ).italic = True

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

    # Figura: cronograma orientativo (Gantt) como plantilla.
    doc.add_heading("Cronograma orientativo (plantilla)", level=2)
    g = doc.add_table(rows=1, cols=GANTT_MONTHS + 1)
    g.style = "Light Grid Accent 1"
    head = g.rows[0].cells
    head[0].text = "Fase"
    for mth in range(1, GANTT_MONTHS + 1):
        head[mth].text = f"M{mth}"
    for name, start, end in GANTT_PHASES:
        cells = g.add_row().cells
        cells[0].text = name
        for mth in range(1, GANTT_MONTHS + 1):
            cells[mth].text = "█" if start <= mth <= end else ""

    _docx_figures(doc, tender, score)

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


def _docx_figures(doc, tender, score) -> None:
    from docx.shared import Pt, RGBColor

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

    # 3) Mapa de riesgos 3×3 (probabilidad × impacto).
    doc.add_heading("Mapa de riesgos (plantilla)", level=2)
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
    children = doc.add_table(rows=1, cols=len(ORG_CHILDREN))
    children.style = "Table Grid"
    for i, name in enumerate(ORG_CHILDREN):
        children.rows[0].cells[i].text = name


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


def build_pdf(tender, drafts, score) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Cabecera: logo real si se puede descargar; si no, barra de marca con texto.
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

    pdf.set_text_color(20, 20, 20)
    pdf.set_font("Helvetica", "B", 14)
    _mc(pdf, 7, _latin1(tender.title or "(sin titulo)"))
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(110, 110, 110)
    _mc(
        pdf, 5,
        _latin1(f"Expediente: {tender.source_id} - Fuente: {tender.source} - "
                f"Presupuesto: {tender.budget_amount or 's/d'} {tender.currency}"),
    )
    pdf.ln(3)

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

    # Figura: cronograma orientativo (Gantt).
    pdf.set_text_color(20, 20, 20)
    pdf.set_font("Helvetica", "B", 11)
    _mc(pdf, 6, "Cronograma orientativo (plantilla)")
    col_w = 22.0
    cell_w = (190 - col_w) / GANTT_MONTHS
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(110, 110, 110)
    pdf.set_x(pdf.l_margin + col_w)
    for mth in range(1, GANTT_MONTHS + 1):
        pdf.cell(cell_w, 5, f"M{mth}", border=0, align="C", ln=0)
    pdf.ln(5)
    for name, start, end in GANTT_PHASES:
        y = pdf.get_y()
        pdf.set_text_color(60, 60, 60)
        pdf.set_x(pdf.l_margin)
        pdf.cell(col_w, 6, _latin1(name)[:14], ln=0)
        x0 = pdf.get_x()
        for mth in range(GANTT_MONTHS):
            if start <= mth + 1 <= end:
                pdf.set_fill_color(*BRAND)
                pdf.rect(x0 + mth * cell_w + 1, y + 1, cell_w - 2, 4, "F")
        pdf.ln(6)
    pdf.ln(3)

    _pdf_figures(pdf, tender, score)

    for d in drafts:
        pdf.add_page()
        _pdf_markdown(pdf, d.content or "")

    out = pdf.output()
    return bytes(out)


def _pdf_figures(pdf, tender, score) -> None:
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

    # 3) Mapa de riesgos (probabilidad × impacto).
    pdf.set_text_color(20, 20, 20)
    pdf.set_font("Helvetica", "B", 11)
    _mc(pdf, 6, "Mapa de riesgos (plantilla)")
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
    cw = 44
    n = len(ORG_CHILDREN)
    total_w = n * cw + (n - 1) * 4
    cx0 = pdf.l_margin + (190 - total_w) / 2
    cy = top_y + bh + 12
    pdf.set_draw_color(150, 160, 180)
    pdf.line(top_x + bw / 2, top_y + bh, top_x + bw / 2, cy - 6)
    pdf.set_font("Helvetica", "", 8)
    for i, name in enumerate(ORG_CHILDREN):
        bx = cx0 + i * (cw + 4)
        pdf.line(top_x + bw / 2, cy - 6, bx + cw / 2, cy - 6)
        pdf.line(bx + cw / 2, cy - 6, bx + cw / 2, cy)
        pdf.set_fill_color(230, 233, 240)
        pdf.rect(bx, cy, cw, bh, "F")
        pdf.set_text_color(40, 40, 40)
        pdf.set_xy(bx, cy + 1.5)
        pdf.cell(cw, 6, _latin1(name), align="C")
    pdf.set_y(cy + bh + 4)


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
