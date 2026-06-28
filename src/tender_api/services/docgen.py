"""Generación de documentos de oferta en Word (.docx) y PDF con marca Keedio.

Convierte los borradores (Markdown) en documentos con cabecera de marca, encabezados, listas,
tablas reales (p. ej. la matriz de cumplimiento) y una figura del desglose del scoring.
Dependencias pure-python (python-docx, fpdf2) → no requieren librerías de sistema.
"""

from __future__ import annotations

import re
from io import BytesIO

BRAND = (0x5B, 0x94, 0xFF)

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
    from docx.shared import Pt, RGBColor

    doc = Document()

    # Cabecera de marca.
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

    # Borradores.
    for d in drafts:
        doc.add_page_break()
        _docx_markdown(doc, d.content or "")

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


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

    # Cabecera de marca.
    pdf.set_fill_color(*BRAND)
    pdf.rect(0, 0, 210, 18, "F")
    pdf.set_xy(10, 4)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "KEEDIO  ", ln=0)
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

    for d in drafts:
        pdf.add_page()
        _pdf_markdown(pdf, d.content or "")

    out = pdf.output()
    return bytes(out)


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
