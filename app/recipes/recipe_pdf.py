"""Druckbares Pflaumen-PDF für ein einzelnes Rezept."""
from __future__ import annotations

import html
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


def _ingredient_line(item: dict) -> str:
    raw = str(item.get("raw") or "").strip()
    if raw:
        return raw
    return " ".join(
        part
        for part in (
            _number(item.get("amount")),
            str(item.get("unit") or "").strip(),
            str(item.get("name") or "").strip(),
        )
        if part
    )


def build_recipe_pdf(recipe: dict) -> bytes:
    """Erzeugt ein A4-Rezept-PDF mit Zutaten, Schritten und Quellenhinweis."""
    try:
        from reportlab.lib import colors
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
    name = str(recipe.get("name") or "Unbenannt")
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=14 * mm,
        bottomMargin=16 * mm,
        title=name,
        author="Rezepte",
        subject="Rezept",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "RecipePdfTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=24,
        leading=28,
        textColor=ink,
        spaceAfter=4,
    )
    meta_style = ParagraphStyle(
        "RecipePdfMeta",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=muted,
    )
    body_style = ParagraphStyle(
        "RecipePdfBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=14,
        textColor=ink,
    )
    section_style = ParagraphStyle(
        "RecipePdfSection",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=19,
        textColor=ink,
        spaceBefore=9,
        spaceAfter=5,
    )
    ingredient_style = ParagraphStyle(
        "RecipePdfIngredient",
        parent=body_style,
        fontSize=9,
        leading=12,
    )
    step_style = ParagraphStyle(
        "RecipePdfStep",
        parent=body_style,
        fontSize=9.5,
        leading=14,
    )
    step_number_style = ParagraphStyle(
        "RecipePdfStepNumber",
        parent=body_style,
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=14,
        textColor=white,
    )

    meta = " - ".join(
        html.escape(str(value))
        for value in (recipe.get("type"), recipe.get("category"))
        if value
    )
    servings = recipe.get("servings")
    if servings:
        serving_label = "Portion" if int(servings) == 1 else "Portionen"
        meta = " - ".join(
            value for value in (meta, f"{int(servings)} {serving_label}") if value
        )

    story = [
        Table(
            [[
                [
                    Paragraph("REZEPT", meta_style),
                    Paragraph(html.escape(name), title_style),
                    Paragraph(meta or "Privates Rezept", meta_style),
                ],
                Paragraph(
                    "KOCHEN<br/><b>GENIESSEN</b>",
                    ParagraphStyle(
                        "RecipePdfMark",
                        parent=meta_style,
                        fontName="Helvetica-Bold",
                        fontSize=9,
                        leading=14,
                        alignment=1,
                        textColor=ink,
                    ),
                ),
            ]],
            colWidths=[142 * mm, 33 * mm],
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
    ]

    description = str(recipe.get("description") or "").strip()
    if description:
        story.extend([
            Spacer(1, 5 * mm),
            Table(
                [[Paragraph(
                    html.escape(description).replace("\n", "<br/>"),
                    body_style,
                )]],
                colWidths=[175 * mm],
                style=TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), cream),
                    ("BOX", (0, 0), (-1, -1), 0.6, border),
                    ("LINEBEFORE", (0, 0), (0, 0), 3, brand),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]),
            ),
        ])

    calories = recipe.get("calories_per_serving")
    if calories:
        nutrition = (
            f"Pro Portion: ca. {int(calories)} kcal"
            f" - {_number(recipe.get('protein_g') or 0)} g Eiweiß"
            f" - {_number(recipe.get('carbs_g') or 0)} g Kohlenhydrate"
            f" - {_number(recipe.get('fat_g') or 0)} g Fett"
        )
        story.extend([
            Spacer(1, 3 * mm),
            Paragraph(html.escape(nutrition), meta_style),
        ])

    ingredients = [
        _ingredient_line(item)
        for item in (recipe.get("ingredients") or [])
        if _ingredient_line(item)
    ]
    story.append(Paragraph("Zutaten", section_style))
    if ingredients:
        half = (len(ingredients) + 1) // 2
        left = ingredients[:half]
        right = ingredients[half:]
        rows = []
        for index in range(half):
            cells = []
            for column in (left, right):
                text = column[index] if index < len(column) else ""
                cells.append(
                    Paragraph(f"- {html.escape(text)}", ingredient_style)
                    if text else ""
                )
            rows.append(cells)
        story.append(Table(
            rows,
            colWidths=[87.5 * mm, 87.5 * mm],
            style=TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.35, border),
                ("BACKGROUND", (0, 0), (-1, -1), cream),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]),
        ))
    else:
        story.append(Paragraph("Noch keine Zutaten erfasst.", meta_style))

    steps = [
        str(item.get("instruction") or "").strip()
        for item in (recipe.get("steps") or [])
        if str(item.get("instruction") or "").strip()
    ]
    story.append(Paragraph("Zubereitung", section_style))
    if steps:
        for index, instruction in enumerate(steps, start=1):
            story.extend([
                KeepTogether(Table(
                    [[
                        Paragraph(str(index), step_number_style),
                        Paragraph(
                            html.escape(instruction).replace("\n", "<br/>"),
                            step_style,
                        ),
                    ]],
                    colWidths=[12 * mm, 163 * mm],
                    style=TableStyle([
                        ("BACKGROUND", (0, 0), (0, 0), brand),
                        ("BACKGROUND", (1, 0), (1, 0), cream),
                        ("BOX", (0, 0), (-1, -1), 0.45, border),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("ALIGN", (0, 0), (0, 0), "CENTER"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 7),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ]),
                )),
                Spacer(1, 2 * mm),
            ])
    else:
        story.append(Paragraph("Noch keine Zubereitungsschritte erfasst.", meta_style))

    source = str(recipe.get("url") or "").strip()
    if source.startswith(("https://", "http://")):
        story.extend([
            Spacer(1, 5 * mm),
            Paragraph(f"Quelle: {html.escape(source)}", meta_style),
        ])

    def _page_footer(canvas, document):
        canvas.saveState()
        width, _ = A4
        canvas.setStrokeColor(border)
        canvas.setLineWidth(0.5)
        canvas.line(15 * mm, 11 * mm, width - 15 * mm, 11 * mm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(muted)
        canvas.drawString(15 * mm, 7 * mm, "Rezepte")
        canvas.drawRightString(
            width - 15 * mm,
            7 * mm,
            f"Seite {document.page}",
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)
    return buffer.getvalue()
