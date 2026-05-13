"""
PDF report generator using ReportLab.

Generates two report types:
  - Daily digest: top N opportunities across all communes
  - Commune report: detailed breakdown per commune
"""
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog

log = structlog.get_logger(__name__)

# ReportLab colour palette — dark terminal aesthetic
_BLACK = (0.08, 0.08, 0.08)
_WHITE = (0.95, 0.95, 0.95)
_CYAN = (0.0, 0.85, 0.85)
_GREEN = (0.0, 0.80, 0.35)
_YELLOW = (1.0, 0.85, 0.0)
_RED = (0.95, 0.25, 0.25)
_GRAY = (0.45, 0.45, 0.45)


def _score_color(score: Optional[float]):
    if score is None:
        return _GRAY
    if score >= 75:
        return _GREEN
    if score >= 60:
        return _YELLOW
    return _RED


def _fmt_clp(value: int) -> str:
    return f"${value:,.0f}".replace(",", ".")


def _fmt_uf(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"UF {value:,.1f}"


def generate_daily_digest(
    properties: list[dict],
    output_path: str | Path,
    report_date: Optional[datetime] = None,
) -> Path:
    """
    Generate a one-page PDF digest of top-scoring properties.

    Args:
        properties: list of dicts with keys matching top_opportunities endpoint
        output_path: where to save the PDF
        report_date: defaults to today
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate, Table, TableStyle, Paragraph,
            Spacer, HRFlowable,
        )
    except ImportError:
        log.error("reportlab_not_installed", hint="pip install reportlab")
        raise

    if report_date is None:
        report_date = datetime.utcnow()

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(path),
        pagesize=landscape(A4),
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title",
        parent=styles["Heading1"],
        fontSize=16,
        textColor=colors.HexColor("#00d4d4"),
        fontName="Helvetica-Bold",
    )
    subtitle_style = ParagraphStyle(
        "Sub",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#888888"),
    )
    cell_style = ParagraphStyle(
        "Cell",
        parent=styles["Normal"],
        fontSize=8,
        fontName="Helvetica",
    )

    story = []
    story.append(Paragraph("Real Estate Intelligence — Daily Digest", title_style))
    story.append(Paragraph(
        f"Generado: {report_date.strftime('%d %b %Y %H:%M UTC')}  |  "
        f"Top {len(properties)} oportunidades activas",
        subtitle_style,
    ))
    story.append(Spacer(1, 6 * mm))

    # Table
    headers = [
        "Score", "Tipo", "Comuna", "Corredor",
        "Precio", "UF/m²", "m²", "Dorm.", "URL",
    ]
    data = [headers]
    for prop in properties:
        score = prop.get("score")
        precio_m2_uf = (prop.get("precio_m2", 0) / 38500) if prop.get("precio_m2") else None
        row = [
            f"{score:.1f}" if score else "—",
            prop.get("tipo_propiedad", ""),
            prop.get("comuna", ""),
            prop.get("corredor") or "—",
            _fmt_clp(prop.get("precio", 0)),
            _fmt_uf(precio_m2_uf),
            f"{prop.get('m2', 0):.0f}",
            str(prop.get("dormitorios") or "—"),
            Paragraph(f'<link href="{prop.get("url", "")}">{prop.get("url", "")[:40]}…</link>', cell_style),
        ]
        data.append(row)

    col_widths = [18 * mm, 22 * mm, 28 * mm, 24 * mm, 30 * mm, 18 * mm, 14 * mm, 14 * mm, None]
    table = Table(data, colWidths=col_widths, repeatRows=1)

    table_style = TableStyle([
        # Header
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#00d4d4")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        # Rows
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#12121f")),
        ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor("#cccccc")),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#12121f"), colors.HexColor("#1a1a2e")]),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#333355")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ])

    # Colour score cells
    for i, prop in enumerate(properties, start=1):
        score = prop.get("score")
        if score is not None:
            r, g, b = _score_color(score)
            table_style.add(
                "TEXTCOLOR", (0, i), (0, i),
                colors.Color(r, g, b),
            )
            table_style.add("FONTNAME", (0, i), (0, i), "Helvetica-Bold")

    table.setStyle(table_style)
    story.append(table)

    doc.build(story)
    log.info("pdf_generated", path=str(path), n_properties=len(properties))
    return path


def generate_commune_report(
    commune_data: list[dict],
    properties: list[dict],
    output_path: str | Path,
) -> Path:
    """
    Generate a per-commune breakdown PDF.
    commune_data: list of CommuneSummary dicts
    properties: all active properties (for detail section)
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    except ImportError:
        log.error("reportlab_not_installed", hint="pip install reportlab")
        raise

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )
    styles = getSampleStyleSheet()
    story = []

    title_style = ParagraphStyle(
        "T", parent=styles["Heading1"],
        fontSize=14, textColor=colors.HexColor("#00d4d4"),
    )
    story.append(Paragraph("Reporte por Comuna — Mercado Inmobiliario", title_style))
    story.append(Paragraph(
        datetime.utcnow().strftime("%d %b %Y"),
        ParagraphStyle("S", parent=styles["Normal"], fontSize=9, textColor=colors.grey),
    ))
    story.append(Spacer(1, 8 * mm))

    headers = ["Comuna", "Corredor", "N", "Score avg", "CLP/m² med.", "Min precio", "Max precio"]
    data = [headers] + [
        [
            c.get("comuna", ""),
            c.get("corredor") or "—",
            str(c.get("n_properties", 0)),
            f"{c.get('avg_score', 0):.1f}" if c.get("avg_score") else "—",
            _fmt_clp(int(c.get("median_precio_m2", 0))),
            _fmt_clp(c.get("min_precio", 0)),
            _fmt_clp(c.get("max_precio", 0)),
        ]
        for c in commune_data
    ]

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#00d4d4")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#12121f")),
        ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor("#cccccc")),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#333355")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    story.append(table)
    doc.build(story)
    log.info("commune_pdf_generated", path=str(path))
    return path
