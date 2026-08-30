"""Druckbares Pflaumen-PDF für Wochenplan und Einkaufsliste."""
from __future__ import annotations

from io import BytesIO
from typing import Any


def _number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _shopping_line(item: dict) -> str:
    amount = _number(item.get("amount"))
    unit = str(item.get("unit") or "").strip()
    name = str(item.get("name") or "?").strip()
    return " ".join(part for part in (amount, unit, name) if part)


def build_meal_plan_pdf(week: dict) -> bytes:
    """Erzeugt ein A4-PDF aus dem Payload der Wochenplan-API."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            KeepTogether,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:  # pragma: no cover - Deployment-Fehlkonfiguration
        raise RuntimeError(
            "PDF-Erstellung ist nicht installiert (reportlab fehlt)"
        ) from exc

    brand = colors.HexColor("#8A577F")
    brand_light = colors.HexColor("#EBDDEA")
    cream = colors.HexColor("#FFFDF8")
    ink = colors.HexColor("#3E2B39")
    muted = colors.HexColor("#74636F")
    border = colors.HexColor("#E0D2DC")
    white = colors.white

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=14 * mm,
        bottomMargin=16 * mm,
        title=f"Wochenplan {week.get('week_start', '')}",
        author="Rezepte",
        subject="Wochenplan mit gemeinsamer Einkaufsliste",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "MealPlanTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=25,
        leading=29,
        textColor=ink,
        alignment=TA_LEFT,
        spaceAfter=3,
    )
    range_style = ParagraphStyle(
        "MealPlanRange",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=muted,
    )
    day_style = ParagraphStyle(
        "MealPlanDay",
        parent=styles["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=white,
        spaceAfter=0,
    )
    recipe_style = ParagraphStyle(
        "MealPlanRecipe",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=ink,
    )
    small_style = ParagraphStyle(
        "MealPlanSmall",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=10,
        textColor=muted,
    )
    day_date_style = ParagraphStyle(
        "MealPlanDayDate",
        parent=small_style,
        textColor=white,
    )
    section_style = ParagraphStyle(
        "MealPlanSection",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=19,
        textColor=ink,
        spaceBefore=8,
        spaceAfter=4,
    )
    shop_style = ParagraphStyle(
        "MealPlanShop",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=ink,
    )
    empty_style = ParagraphStyle(
        "MealPlanEmpty",
        parent=styles["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=8,
        leading=11,
        textColor=muted,
    )

    start = str(week.get("week_start") or "")
    end = str(week.get("week_end") or "")
    display_range = (
        f"{start[8:10]}.{start[5:7]}.{start[:4]} - "
        f"{end[8:10]}.{end[5:7]}.{end[:4]}"
    )
    summary = week.get("summary") or {}
    story = [
        Table(
            [[
                [
                    Paragraph("WOCHENPLAN", small_style),
                    Paragraph("Gemeinsam planen", title_style),
                    Paragraph(display_range, range_style),
                ],
                Paragraph(
                    f"<b>{int(summary.get('planned_meals') or 0)}</b> Gerichte<br/>"
                    f"<b>{int(summary.get('shopping_items') or 0)}</b> Einkaufspositionen",
                    ParagraphStyle(
                        "MealPlanHeaderStats",
                        parent=small_style,
                        fontSize=9,
                        leading=14,
                        textColor=ink,
                        alignment=TA_CENTER,
                    ),
                ),
            ]],
            colWidths=[130 * mm, 45 * mm],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), brand_light),
                ("BOX", (0, 0), (-1, -1), 0.8, border),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]),
        ),
        Spacer(1, 7 * mm),
    ]

    for day in week.get("days") or []:
        recipe_blocks = []
        for item in day.get("items") or []:
            servings = int(item.get("planned_servings") or 1)
            suffix = "Portion" if servings == 1 else "Portionen"
            scaling = (
                f"Mengen x {_number(item.get('multiplier'))}"
                if item.get("scalable")
                else "Originalmengen"
            )
            recipe_blocks.extend([
                Paragraph(
                    f"<b>{item.get('recipe_name') or 'Unbenannt'}</b> - "
                    f"{servings} {suffix}",
                    recipe_style,
                ),
                Paragraph(scaling, small_style),
                Spacer(1, 2.5 * mm),
            ])
        if not recipe_blocks:
            recipe_blocks = [Paragraph("Noch nichts geplant", empty_style)]

        day_date = str(day.get("date") or "")
        date_label = f"{day_date[8:10]}.{day_date[5:7]}."
        day_heading = [
            Paragraph(str(day.get("label") or ""), day_style),
            Paragraph(date_label, day_date_style),
        ]
        story.extend([
            KeepTogether(Table(
                [[day_heading, recipe_blocks]],
                colWidths=[34 * mm, 141 * mm],
                style=TableStyle([
                    ("BACKGROUND", (0, 0), (0, 0), brand),
                    ("BACKGROUND", (1, 0), (1, 0), cream),
                    ("BOX", (0, 0), (-1, -1), 0.7, border),
                    ("LINEBEFORE", (1, 0), (1, 0), 0.7, border),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]),
            )),
            Spacer(1, 2.2 * mm),
        ])

    story.extend([
        Spacer(1, 3 * mm),
        Paragraph("Gemeinsame Einkaufsliste", section_style),
        Paragraph(
            "Gleiche Zutaten und Einheiten sind zusammengeführt. "
            "Als nicht einkaufen markierte Zutaten sind nicht enthalten.",
            range_style,
        ),
        Spacer(1, 3 * mm),
    ])

    shopping = week.get("shopping_preview") or []
    if shopping:
        half = (len(shopping) + 1) // 2
        left = shopping[:half]
        right = shopping[half:]
        shopping_rows = []
        for index in range(half):
            row = []
            for column in (left, right):
                if index < len(column):
                    row.append(Paragraph(f"[ ]  {_shopping_line(column[index])}", shop_style))
                else:
                    row.append("")
            shopping_rows.append(row)
        story.append(Table(
            shopping_rows,
            colWidths=[87.5 * mm, 87.5 * mm],
            style=TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.35, border),
                ("BACKGROUND", (0, 0), (-1, -1), cream),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]),
            repeatRows=0,
        ))
    else:
        story.append(Paragraph(
            "Noch keine einkaufbaren Zutaten für diese Woche.",
            empty_style,
        ))

    def _page_footer(canvas, document):
        canvas.saveState()
        width, _ = A4
        canvas.setStrokeColor(border)
        canvas.setLineWidth(0.5)
        canvas.line(15 * mm, 11 * mm, width - 15 * mm, 11 * mm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(muted)
        canvas.drawString(15 * mm, 7 * mm, "Rezepte - Wochenplan")
        canvas.drawRightString(
            width - 15 * mm,
            7 * mm,
            f"Seite {document.page}",
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)
    return buffer.getvalue()
