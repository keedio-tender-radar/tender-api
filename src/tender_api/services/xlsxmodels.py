"""Dos modelos de Excel de licitación, precumplimentados con los datos de la plataforma.

- Modelo 1 (exhaustivo, sector público): Go/No-Go, expediente, jornadas, coste por perfil,
  costes indirectos, precio y margen, simuladores de puntos (precio y técnica), checklist de
  documentos por sobres, riesgos, y hojas maestras BASE (tarifas / costes).
- Modelo 2 (ágil): requisitos, supuestos, costes CAPEX/OPEX, escenarios de margen y resumen.

Se rellenan con lo que la plataforma sabe (expediente, score, matriz/requisitos, riesgos,
documentos exigidos, tarifa/margen del perfil). El resto son plantillas editables con fórmulas
que recalculan. La cifra final SIEMPRE requiere revisión humana.
"""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO

BRAND = "5B94FF"
GREEN, GREEN_T = "C6EFCE", "006100"
AMBER, AMBER_T = "FFEB9C", "9C6500"
RED, RED_T = "FFC7CE", "9C0006"
MONEY = '#,##0.00 "€"'
PCT = "0.0%"
IVA = 1.21


def _styles():
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    thin = Side(style="thin", color="D9DEE8")
    return {
        "title": Font(bold=True, size=16, color=BRAND),
        "sub": Font(bold=True, size=11, color=BRAND),
        "h": Font(bold=True, color="FFFFFF", size=10),
        "hfill": PatternFill("solid", fgColor=BRAND),
        "bold": Font(bold=True),
        "muted": Font(color="808080", italic=True, size=9),
        "border": Border(left=thin, right=thin, top=thin, bottom=thin),
        "center": Alignment(horizontal="center", vertical="center", wrap_text=True),
        "wrap": Alignment(vertical="top", wrap_text=True),
        "green": PatternFill("solid", fgColor=GREEN),
        "amber": PatternFill("solid", fgColor=AMBER),
        "red": PatternFill("solid", fgColor=RED),
    }


def _headers(ws, row: int, headers: list[str], st, start: int = 1) -> None:
    for c, h in enumerate(headers, start=start):
        cell = ws.cell(row, c, h)
        cell.font, cell.fill, cell.alignment, cell.border = (
            st["h"], st["hfill"], st["center"], st["border"]
        )


def _title(ws, text: str, st) -> None:
    ws["A1"] = text
    ws["A1"].font = st["title"]


def _risks(score) -> list[str]:
    if not score:
        return []
    out = [
        f.get("message", "")
        for f in (score.factors or [])
        if f.get("kind") == "negative" and f.get("message")
    ]
    out += [str(r) for r in (score.hard_rules or [])]
    return out


def _doc_list(drafts) -> list[str]:
    for d in drafts or []:
        if getattr(d, "kind", "") == "documentos_requeridos":
            content = d.content or ""
            if content and "Pendiente de extraer" not in content:
                items = [
                    ln.strip().lstrip("-*").strip().lstrip("*").strip()
                    for ln in content.split("\n")
                    if ln.strip().startswith(("-", "*"))
                ]
                items = [i.split(":")[0].strip("* ").strip() for i in items if i]
                if items:
                    return items[:20]
    return [
        "Declaración responsable / DEUC",
        "Poderes de representación",
        "Solvencia técnica (proyectos similares)",
        "Solvencia económica (cifra de negocio)",
        "Certificados (ROLECE, AEAT, Seg. Social)",
        "Oferta técnica (memoria)",
        "Oferta económica (modelo oficial)",
        "Garantía / aval provisional",
        "Anexos y modelos firmados",
    ]


def _team_days(team: list[str], months: int) -> list[tuple[str, int]]:
    """Perfil → jornadas estimadas (reparto simple de la capacidad total del proyecto)."""
    team = [t for t in (team or []) if t] or ["Jefe de Proyecto", "Arquitecto", "Desarrollo", "QA"]
    total_days = max(20, months * 20)  # ~20 jornadas/mes
    per = max(5, round(total_days / len(team)))
    return [(t, per) for t in team]


# ----------------------------- MODELO 2: ÁGIL -----------------------------

def build_plan_agil(tender, score, drafts, team, months, rate, margin) -> bytes:
    from openpyxl import Workbook
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Font, PatternFill

    from tender_api.services import estimate

    months = int(months or 6)
    rate = float(rate or 45.0)
    margin = float(margin if margin is not None else 0.2)
    budget = float(getattr(tender, "budget_amount", None) or 0)
    reqs = estimate.requirements_from_drafts(drafts)
    st = _styles()

    wb = Workbook()

    # HOJA1_Requisitos
    ws = wb.active
    ws.title = "HOJA1_Requisitos"
    _title(ws, "Requisitos bloqueantes — filtro rápido", st)
    _headers(ws, 3, ["Requisito", "Fuente", "¿Cumplimos? (Sí/No)", "Impacto € (est.)", "Notas"], st)
    rows = reqs or [("(añade los requisitos bloqueantes del pliego)", "")]
    for i, (req, _desc) in enumerate(rows[:20]):
        r = 4 + i
        ws.cell(r, 1, req).alignment = st["wrap"]
        ws.cell(r, 2, "Pliego")
        ws.cell(r, 3, "Sí")
        ws.cell(r, 4, 0).number_format = MONEY
        for c in range(1, 6):
            ws.cell(r, c).border = st["border"]
    ws.conditional_formatting.add(
        f"C4:C{3 + len(rows[:20])}",
        FormulaRule(formula=['LOWER($C4)="no"'], fill=st["red"], font=Font(color=RED_T)),
    )
    for col, w in zip("ABCDE", [46, 14, 18, 16, 30], strict=False):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A4"

    # HOJA2_Supuestos (motor)
    ws = wb.create_sheet("HOJA2_Supuestos")
    _title(ws, "Supuestos del proyecto (motor de cálculo)", st)
    sup = [
        ("Duración (meses)", months),
        ("Jornadas/mes por persona", 20),
        ("Coste medio por jornada (€)", round(rate * 8, 2)),
        ("Margen objetivo (%)", margin),
        ("IVA (%)", 0.21),
        ("Presupuesto máximo cliente sin IVA (€)", budget),
    ]
    for i, (k, v) in enumerate(sup):
        r = 3 + i
        ws.cell(r, 1, k).font = st["bold"]
        cell = ws.cell(r, 2, v)
        if "€" in k:
            cell.number_format = MONEY
        elif "%" in k:
            cell.number_format = PCT
        cell.fill = PatternFill("solid", fgColor="FFF6CC")
    ws.cell(9, 1, "Cambia estos valores y el resto del Excel se recalcula.").font = st["muted"]
    ws.column_dimensions["A"].width = 40
    ws.column_dimensions["B"].width = 16

    # HOJA3_Costes_Base (CAPEX / OPEX)
    ws = wb.create_sheet("HOJA3_Costes_Base")
    _title(ws, "Costes base — CAPEX (inversión) y OPEX (recurrente)", st)
    _headers(
        ws, 3, ["Concepto", "Tipo", "Unidades", "Cantidad", "Coste unitario (€)", "Total (€)"], st
    )
    # Equipo (OPEX principal) calculado desde Supuestos.
    equipo_jornadas = max(20, months * 20)
    base_rows = [
        ("Equipo de proyecto", "OPEX", "jornadas", equipo_jornadas, "='HOJA2_Supuestos'!B5"),
        ("Licencias software", "OPEX", "meses", months, 0),
        ("Infraestructura / cloud", "OPEX", "meses", months, 0),
        ("Hardware / equipamiento", "CAPEX", "uds", 0, 0),
        ("Puesta en marcha / configuración", "CAPEX", "uds", 1, 0),
        ("Viajes y dietas", "OPEX", "uds", 0, 0),
    ]
    for i, (concepto, tipo, ud, cant, cu) in enumerate(base_rows):
        r = 4 + i
        ws.cell(r, 1, concepto)
        ws.cell(r, 2, tipo)
        ws.cell(r, 3, ud)
        ws.cell(r, 4, cant)
        cu_cell = ws.cell(r, 5, cu)
        cu_cell.number_format = MONEY
        tot = ws.cell(r, 6, f"=D{r}*E{r}")
        tot.number_format = MONEY
        for c in range(1, 7):
            ws.cell(r, c).border = st["border"]
    last = 3 + len(base_rows)
    ws.cell(last + 2, 1, "Total CAPEX (€)").font = st["bold"]
    ws.cell(last + 2, 6, f'=SUMIFS(F4:F{last},B4:B{last},"CAPEX")').number_format = MONEY
    ws.cell(last + 3, 1, "Total OPEX (€)").font = st["bold"]
    ws.cell(last + 3, 6, f'=SUMIFS(F4:F{last},B4:B{last},"OPEX")').number_format = MONEY
    ws.cell(last + 4, 1, "Coste base total (€)").font = st["bold"]
    ws.cell(last + 4, 6, f"=F{last + 2}+F{last + 3}").number_format = MONEY
    ws.cell(last + 4, 6).font = st["bold"]
    for col, w in zip("ABCDEF", [32, 10, 12, 12, 18, 18], strict=False):
        ws.column_dimensions[col].width = w

    coste_total_ref = f"'HOJA3_Costes_Base'!F{last + 4}"

    # HOJA4_Escenarios (sensibilidad de margen)
    ws = wb.create_sheet("HOJA4_Escenarios")
    _title(ws, "Escenarios de margen y precio", st)
    _headers(
        ws, 3,
        ["Escenario", "Margen", "Coste base (€)", "Precio sin IVA (€)", "Precio con IVA (€)",
         "vs Presupuesto"], st,
    )
    escenarios = [("Pesimista", 0.15), ("Objetivo", margin), ("Optimista", 0.25)]
    for i, (nombre, m) in enumerate(escenarios):
        r = 4 + i
        ws.cell(r, 1, nombre).font = st["bold"]
        ws.cell(r, 2, m).number_format = PCT
        ws.cell(r, 3, f"={coste_total_ref}").number_format = MONEY
        ws.cell(r, 4, f"=C{r}*(1+B{r})").number_format = MONEY
        ws.cell(r, 5, f"=D{r}*{IVA}").number_format = MONEY
        ws.cell(r, 6, f"=IF('HOJA2_Supuestos'!B8=0,\"s/d\",IF(D{r}<='HOJA2_Supuestos'!B8,"
                      '"Dentro","FUERA de presupuesto"))')
        for c in range(1, 7):
            ws.cell(r, c).border = st["border"]
    ws.conditional_formatting.add(
        "F4:F6", FormulaRule(formula=['$F4="Dentro"'], fill=st["green"], font=Font(color=GREEN_T))
    )
    ws.conditional_formatting.add(
        "F4:F6",
        FormulaRule(formula=['LEFT($F4,5)="FUERA"'], fill=st["red"], font=Font(color=RED_T)),
    )
    for col, w in zip("ABCDEF", [16, 10, 16, 18, 18, 20], strict=False):
        ws.column_dimensions[col].width = w

    # HOJA5_Resumen (dashboard)
    ws = wb.create_sheet("HOJA5_Resumen")
    _title(ws, f"Resumen ejecutivo — {getattr(tender, 'title', '') or ''}", st)
    kpis = [
        ("Presupuesto máximo (sin IVA)", "='HOJA2_Supuestos'!B8", MONEY),
        ("Total CAPEX (inversión)", f"='HOJA3_Costes_Base'!F{last + 2}", MONEY),
        ("Total OPEX (recurrente)", f"='HOJA3_Costes_Base'!F{last + 3}", MONEY),
        ("Coste base total", f"={coste_total_ref}", MONEY),
        ("Margen objetivo", "='HOJA2_Supuestos'!B6", PCT),
        ("Precio objetivo sin IVA", "='HOJA4_Escenarios'!D5", MONEY),
        ("Precio objetivo con IVA", "='HOJA4_Escenarios'!E5", MONEY),
        ("Valoración vs presupuesto", "='HOJA4_Escenarios'!F5", None),
    ]
    for i, (k, v, fmt) in enumerate(kpis):
        r = 3 + i
        ws.cell(r, 1, k).font = st["bold"]
        cell = ws.cell(r, 2, v)
        if fmt:
            cell.number_format = fmt
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 22

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ----------------------------- MODELO 1: EXHAUSTIVO -----------------------------

def build_plan_exhaustivo(tender, score, drafts, team, months, rate, margin) -> bytes:
    from openpyxl import Workbook
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Font

    months = int(months or 6)
    rate = float(rate or 45.0)
    margin = float(margin if margin is not None else 0.2)
    day_rate = round(rate * 8, 2)
    budget = float(getattr(tender, "budget_amount", None) or 0)
    cur = getattr(tender, "currency", "EUR") or "EUR"
    risks = _risks(score)
    docs = _doc_list(drafts)
    team_days = _team_days(team, months)
    st = _styles()

    wb = Workbook()

    # 00_RESUMEN_GO_NO_GO
    ws = wb.active
    ws.title = "00_RESUMEN_GO_NO_GO"
    _title(ws, "Resumen ejecutivo — Go / No-Go", st)
    total = getattr(score, "total", None) if score else None
    rec = (getattr(score, "recommendation", "") or "").upper() if score else "—"
    resumen = [
        ("Licitación", getattr(tender, "title", "") or "", None),
        ("Presupuesto base (sin IVA)", budget, MONEY),
        ("Precio de la oferta (sin IVA)", "='06_PRECIO_Y_MARGEN'!B10", MONEY),
        ("Baja sobre presupuesto", "=IF(B4=0,\"s/d\",1-B5/B4)", PCT),
        ("Valoración del radar (score)", f"{total}/100" if total is not None else "s/d", None),
        ("Recomendación", rec, None),
        ("Nº de riesgos detectados", len(risks), None),
        ("VEREDICTO", "=IF(B5=0,\"COMPLETAR OFERTA\",IF(AND(B5<=B4,B9<=3),"
                      '"GO — viable","REVISAR"))', None),
    ]
    for i, (k, v, fmt) in enumerate(resumen):
        r = 3 + i
        ws.cell(r, 1, k).font = st["bold"]
        cell = ws.cell(r, 2, v)
        if fmt:
            cell.number_format = fmt
    ws.cell(10, 2).font = Font(bold=True, size=13, color=BRAND)
    ws.column_dimensions["A"].width = 32
    ws.column_dimensions["B"].width = 30

    # 01_DATOS_EXPEDIENTE
    ws = wb.create_sheet("01_DATOS_EXPEDIENTE")
    _title(ws, "Datos del expediente", st)
    datos = [
        ("Organismo / cliente", getattr(tender, "buyer", "") or "s/d"),
        ("Nº de expediente", getattr(tender, "source_id", "") or "s/d"),
        ("Fuente", getattr(tender, "source", "") or "s/d"),
        ("Título del proyecto", getattr(tender, "title", "") or ""),
        ("Presupuesto base", f"{budget:,.2f} {cur}" if budget else "s/d"),
        ("Fecha límite de presentación", str(getattr(tender, "deadline", "") or "s/d")[:10]),
        ("CPV", ", ".join(getattr(tender, "cpv", []) or []) or "s/d"),
        ("Objeto del contrato", getattr(tender, "summary", "") or ""),
    ]
    for i, (k, v) in enumerate(datos):
        r = 3 + i
        ws.cell(r, 1, k).font = st["bold"]
        ws.cell(r, 2, v).alignment = st["wrap"]
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 60

    # 02_JORNADAS_PLIEGO
    ws = wb.create_sheet("02_JORNADAS_PLIEGO")
    _title(ws, "Dimensionamiento del equipo — jornadas", st)
    _headers(
        ws, 3,
        ["Perfil", "Jornadas exigidas (pliego)", "Jornadas imputadas (real)", "Notas"], st,
    )
    for i, (perfil, jor) in enumerate(team_days):
        r = 4 + i
        ws.cell(r, 1, perfil)
        ws.cell(r, 2, "")  # editable: lo que exige el pliego
        ws.cell(r, 3, jor)
        for c in range(1, 5):
            ws.cell(r, c).border = st["border"]
    tr = 4 + len(team_days)
    ws.cell(tr, 1, "TOTAL").font = st["bold"]
    ws.cell(tr, 3, f"=SUM(C4:C{tr - 1})").font = st["bold"]
    for col, w in zip("ABCD", [26, 22, 22, 30], strict=False):
        ws.column_dimensions[col].width = w

    # 04_COSTE_POR_PERFIL
    ws = wb.create_sheet("04_COSTE_POR_PERFIL")
    _title(ws, "Coste directo por perfil", st)
    _headers(
        ws, 3,
        ["Perfil", "Jornadas", "Coste/jornada (€)", "Coste total (€)", "Hipótesis / notas"], st,
    )
    for i, (perfil, _jor) in enumerate(team_days):
        r = 4 + i
        ws.cell(r, 1, perfil)
        ws.cell(r, 2, f"='02_JORNADAS_PLIEGO'!C{4 + i}")
        ws.cell(r, 3, day_rate).number_format = MONEY
        ws.cell(r, 4, f"=B{r}*C{r}").number_format = MONEY
        for c in range(1, 6):
            ws.cell(r, c).border = st["border"]
    cr = 4 + len(team_days)
    ws.cell(cr, 1, "TOTAL COSTE DIRECTO").font = st["bold"]
    ws.cell(cr, 4, f"=SUM(D4:D{cr - 1})").number_format = MONEY
    ws.cell(cr, 4).font = st["bold"]
    for col, w in zip("ABCDE", [26, 12, 18, 18, 34], strict=False):
        ws.column_dimensions[col].width = w
    coste_directo_ref = f"'04_COSTE_POR_PERFIL'!D{cr}"

    # 05_COSTES_INDIRECTOS
    ws = wb.create_sheet("05_COSTES_INDIRECTOS")
    _title(ws, "Costes indirectos (OPEX y compras)", st)
    _headers(
        ws, 3,
        ["Concepto", "Categoría", "Cantidad", "Coste unitario (€)", "Total (€)", "¿Repercutible?"],
        st,
    )
    ind = [
        ("Licencias de software", "Software", 0, 0),
        ("Hardware / equipamiento", "Hardware", 0, 0),
        ("Infraestructura / cloud", "Cloud", months, 0),
        ("Viajes y dietas", "Viajes", 0, 0),
        ("Fianza / aval", "Garantías", 1, 0),
        ("Seguros de responsabilidad", "Seguros", 1, 0),
    ]
    for i, (concepto, cat, cant, cu) in enumerate(ind):
        r = 4 + i
        ws.cell(r, 1, concepto)
        ws.cell(r, 2, cat)
        ws.cell(r, 3, cant)
        ws.cell(r, 4, cu).number_format = MONEY
        ws.cell(r, 5, f"=C{r}*D{r}").number_format = MONEY
        ws.cell(r, 6, "Sí")
        for c in range(1, 7):
            ws.cell(r, c).border = st["border"]
    ir = 4 + len(ind)
    ws.cell(ir, 1, "TOTAL INDIRECTOS").font = st["bold"]
    ws.cell(ir, 5, f"=SUM(E4:E{ir - 1})").number_format = MONEY
    ws.cell(ir, 5).font = st["bold"]
    for col, w in zip("ABCDEF", [30, 14, 12, 18, 18, 14], strict=False):
        ws.column_dimensions[col].width = w
    coste_indirecto_ref = f"'05_COSTES_INDIRECTOS'!E{ir}"

    # 06_PRECIO_Y_MARGEN
    ws = wb.create_sheet("06_PRECIO_Y_MARGEN")
    _title(ws, "Precio y margen", st)
    layout = [
        (3, "Coste directo (equipo)", f"={coste_directo_ref}", MONEY),
        (4, "Coste indirecto (compras/licencias)", f"={coste_indirecto_ref}", MONEY),
        (5, "Costes generales (%)", 0.04, PCT),
        (6, "Coste base total", "=B3+B4", MONEY),
        (7, "Con costes generales", "=B6*(1+B5)", MONEY),
        (8, "Margen de beneficio (%)", margin, PCT),
        (10, "PRECIO OFERTA (sin IVA)", "=B7*(1+B8)", MONEY),
        (11, "IVA (21%)", "=B10*0.21", MONEY),
        (12, "PRECIO OFERTA (con IVA)", "=B10+B11", MONEY),
    ]
    for r, k, v, fmt in layout:
        ws.cell(r, 1, k).font = st["bold"]
        cell = ws.cell(r, 2, v)
        if fmt:
            cell.number_format = fmt
    ws.cell(10, 1).font = Font(bold=True, size=12, color=BRAND)
    ws.cell(10, 2).font = Font(bold=True, size=12, color=BRAND)
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 22

    # 07_SIM_PUNTOS_PRECIO
    ws = wb.create_sheet("07_SIM_PUNTOS_PRECIO")
    _title(ws, "Simulador de puntos por precio (baja lineal)", st)
    params = [
        ("Puntos máximos por precio", 40),
        ("Presupuesto base (sin IVA)", budget),
        ("Nuestra oferta (sin IVA)", "='06_PRECIO_Y_MARGEN'!B10"),
    ]
    for i, (k, v) in enumerate(params):
        r = 3 + i
        ws.cell(r, 1, k).font = st["bold"]
        ws.cell(r, 2, v).number_format = MONEY if "€" in k or "oferta" in k.lower() else "0"
    _headers(ws, 7, ["Oferta simulada (€)", "Baja (%)", "Puntos (lineal)"], st)
    sims = ["=B5", "=B4*0.9", "=B4*0.8", "=B4*0.7"]
    for i, f in enumerate(sims):
        r = 8 + i
        ws.cell(r, 1, f).number_format = MONEY
        ws.cell(r, 2, f"=IF($B$4=0,0,1-A{r}/$B$4)").number_format = PCT
        # puntos lineales: proporcional a la baja respecto a la mejor (aquí, baja/baja_max*Pmax)
        ws.cell(r, 3, "=IF($B$4=0,0,($B$4-A{r})/$B$4*$B$3*2.5)".replace("{r}", str(r)))
        for c in range(1, 4):
            ws.cell(r, c).border = st["border"]
    nota = ws.cell(13, 1, "Sustituye la fórmula por la del pliego (lineal, proporcional…).")
    nota.font = st["muted"]
    for col, w in zip("ABC", [22, 12, 16], strict=False):
        ws.column_dimensions[col].width = w

    # 08_PUNT_TECNICA
    ws = wb.create_sheet("08_PUNT_TECNICA")
    _title(ws, "Simulador de puntuación técnica", st)
    _headers(ws, 3, ["Criterio", "Puntos máx.", "¿Cumplimos? (0-1)", "Puntos obtenidos"], st)
    crits = [
        ("Metodología y plan de trabajo", 15),
        ("Arquitectura / solución técnica", 15),
        ("Equipo y certificaciones", 10),
        ("Casos de éxito / experiencia", 10),
        ("Mejoras y valor añadido", 10),
    ]
    for i, (crit, mx) in enumerate(crits):
        r = 4 + i
        ws.cell(r, 1, crit)
        ws.cell(r, 2, mx)
        ws.cell(r, 3, 1)
        ws.cell(r, 4, f"=B{r}*C{r}")
        for c in range(1, 5):
            ws.cell(r, c).border = st["border"]
    pr = 4 + len(crits)
    ws.cell(pr, 1, "TOTAL TÉCNICA").font = st["bold"]
    ws.cell(pr, 2, f"=SUM(B4:B{pr - 1})").font = st["bold"]
    ws.cell(pr, 4, f"=SUM(D4:D{pr - 1})").font = st["bold"]
    for col, w in zip("ABCD", [36, 12, 18, 16], strict=False):
        ws.column_dimensions[col].width = w

    # 09_CHECKLIST_DOCS
    ws = wb.create_sheet("09_CHECKLIST_DOCS")
    _title(ws, "Checklist de documentos por sobres", st)
    _headers(
        ws, 3,
        ["Sobre", "Documento", "Cláusula", "Responsable", "Estado"], st,
    )
    r = 4
    for sobre in ("Administrativo", "Técnico", "Económico"):
        for doc in docs:
            ws.cell(r, 1, sobre)
            ws.cell(r, 2, doc).alignment = st["wrap"]
            ws.cell(r, 3, "")
            ws.cell(r, 4, "")
            ws.cell(r, 5, "Pendiente")
            for c in range(1, 6):
                ws.cell(r, c).border = st["border"]
            r += 1
        if sobre != "Económico":  # solo el bloque completo para admin; técnico/econ resumidos
            break
    last_doc = r - 1
    ws.conditional_formatting.add(
        f"E4:E{last_doc}",
        FormulaRule(formula=['$E4="Completado"'], fill=st["green"], font=Font(color=GREEN_T)),
    )
    ws.conditional_formatting.add(
        f"E4:E{last_doc}",
        FormulaRule(formula=['$E4="Pendiente"'], fill=st["amber"], font=Font(color=AMBER_T)),
    )
    ws.conditional_formatting.add(
        f"E4:E{last_doc}",
        FormulaRule(formula=['$E4="En riesgo"'], fill=st["red"], font=Font(color=RED_T)),
    )
    for col, w in zip("ABCDE", [16, 42, 16, 20, 14], strict=False):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A4"

    # 10_RIESGOS_CRITICOS
    ws = wb.create_sheet("10_RIESGOS_CRITICOS")
    _title(ws, "Riesgos críticos y mitigación", st)
    _headers(
        ws, 3,
        ["Riesgo", "Probabilidad (1-3)", "Impacto (1-3)", "Exposición", "Plan de mitigación"], st,
    )
    risk_rows = risks or ["(sin riesgos destacados por el análisis; revisar el pliego)"]
    for i, rk in enumerate(risk_rows[:12]):
        r = 4 + i
        ws.cell(r, 1, rk).alignment = st["wrap"]
        ws.cell(r, 2, 2)
        ws.cell(r, 3, 2)
        ws.cell(r, 4, f"=B{r}*C{r}")
        ws.cell(r, 5, "")
        for c in range(1, 6):
            ws.cell(r, c).border = st["border"]
    lastr = 3 + len(risk_rows[:12])
    ws.conditional_formatting.add(
        f"D4:D{lastr}", FormulaRule(formula=["$D4>=6"], fill=st["red"], font=Font(color=RED_T))
    )
    ws.conditional_formatting.add(
        f"D4:D{lastr}",
        FormulaRule(formula=["AND($D4>=3,$D4<6)"], fill=st["amber"], font=Font(color=AMBER_T)),
    )
    ws.conditional_formatting.add(
        f"D4:D{lastr}", FormulaRule(formula=["$D4<3"], fill=st["green"], font=Font(color=GREEN_T))
    )
    for col, w in zip("ABCDE", [44, 16, 14, 12, 40], strict=False):
        ws.column_dimensions[col].width = w

    # BASE_Tarifas (maestro editable)
    ws = wb.create_sheet("BASE_Tarifas")
    _title(ws, "BASE · Tarifario salarial por perfil (editable)", st)
    _headers(ws, 3, ["Perfil", "Coste/jornada (€)", "Convenio / notas"], st)
    for i, (perfil, _j) in enumerate(team_days):
        r = 4 + i
        ws.cell(r, 1, perfil)
        ws.cell(r, 2, day_rate).number_format = MONEY
        for c in range(1, 4):
            ws.cell(r, c).border = st["border"]
    for col, w in zip("ABC", [26, 18, 34], strict=False):
        ws.column_dimensions[col].width = w

    # BASE_Costes (maestro hardware/licencias editable)
    ws = wb.create_sheet("BASE_Costes")
    _title(ws, "BASE · Costes estándar de hardware / software (editable)", st)
    _headers(ws, 3, ["Elemento", "Categoría", "Coste unitario (€)"], st)
    base_items = [
        ("Servidor / VM estándar", "Hardware", 0),
        ("Licencia software corporativo", "Software", 0),
        ("Almacenamiento (TB/mes)", "Cloud", 0),
    ]
    for i, (el, cat, cu) in enumerate(base_items):
        r = 4 + i
        ws.cell(r, 1, el)
        ws.cell(r, 2, cat)
        ws.cell(r, 3, cu).number_format = MONEY
        for c in range(1, 4):
            ws.cell(r, c).border = st["border"]
    ws.sheet_state = "hidden"
    for col, w in zip("ABC", [30, 14, 18], strict=False):
        ws.column_dimensions[col].width = w

    # Portada al frente
    _cover(wb, tender, score, st)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _cover(wb, tender, score, st) -> None:
    """Inserta una hoja Portada al principio del libro exhaustivo."""
    from openpyxl.styles import Font

    ws = wb.create_sheet("Portada", 0)
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 92
    ws["B2"] = "KEEDIO"
    ws["B2"].font = Font(bold=True, size=28, color=BRAND)
    ws["B4"] = "MODELO DE LICITACIÓN Y COSTES (exhaustivo)"
    ws["B4"].font = st["sub"]
    ws["B6"] = getattr(tender, "title", "") or "(sin título)"
    ws["B6"].font = Font(bold=True, size=18)
    ws["B8"] = f"Expediente {getattr(tender, 'source_id', '') or 's/d'}"
    if score:
        ws["B10"] = f"Valoración Go/No-Go: {getattr(score, 'total', '?')}/100 · " + (
            getattr(score, "recommendation", "") or ""
        ).upper()
        ws["B10"].font = st["sub"]
    ws["B12"] = (
        f"Generado el {datetime.now(UTC).date().isoformat()} · Keedio Tender Radar · Confidencial"
    )
    ws["B12"].font = Font(size=9, color="999999")
