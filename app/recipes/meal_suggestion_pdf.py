"""Bounded PDF snapshots; all recipe data and constraint checks come from the server."""
from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from pathlib import Path
import threading

from fastapi import HTTPException

from ..db import RECIPE_VARIANT_PENDING_STATUS
from .cookbook_pdf import _text
from .discovery import _complete_ingredients, _key, available_keys, explicit_total_minutes
from .household_features import account_id
from .meal_constraints import CATEGORY_NAMES, excluded_ingredient_match, recipe_categories, matches_weekday_rule

MAX_TEXT_CHARACTERS = 40_000
MAX_PDF_BYTES = 2 * 1024 * 1024
_font_lock = threading.Lock()
WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")


def meal_plan_snapshot(db, criteria: dict, selections: list[dict]) -> dict:
    """Visibility, ingredients, tags and recent plans are read in one transaction."""
    rows = []
    characters = sum(len(value) for value in criteria.get("excluded_ingredients", []))
    recent = set()
    with db.conn() as c:
        c.execute("BEGIN")
        if criteria["avoid_recent_days"]:
            anchor = criteria["week_start"]
            recent = {int(row[0]) for row in c.execute(
                "SELECT DISTINCT recipe_id FROM meal_plan_entries WHERE account_id=? AND planned_for BETWEEN ? AND ?",
                (account_id(db), (anchor - timedelta(days=criteria["avoid_recent_days"])).isoformat(),
                 (anchor - timedelta(days=1)).isoformat()))}
        for item in selections:
            recipe = c.execute(
                "SELECT id,name,type,category,description,ingredients_status, "
                "EXISTS(SELECT 1 FROM recipe_steps s WHERE s.recipe_id=recipes.id) AS has_steps FROM recipes "
                "WHERE id=? AND deleted_at IS NULL AND COALESCE(ingredients_status,'')<>? "
                "AND (owner_account_id IS NULL OR is_global=1 OR owner_account_id=?)",
                (item["recipe_id"], RECIPE_VARIANT_PENDING_STATUS, account_id(db)),
            ).fetchone()
            if not recipe:
                raise HTTPException(404, "Ein vorgeschlagenes Rezept ist nicht mehr verfügbar. Bitte neu laden.")
            if criteria.get("household_only") and not c.execute(
                "SELECT 1 FROM account_recipe_state WHERE account_id=? AND recipe_id=? AND is_saved=1",
                (account_id(db), item["recipe_id"]),
            ).fetchone():
                raise HTTPException(409, "Ein ausgewähltes Rezept gehört nicht mehr zum Haushalt. Bitte neu laden.")
            recipe = dict(recipe)
            ingredients = [dict(row) for row in c.execute(
                "SELECT name,canonical_name FROM recipe_ingredients WHERE recipe_id=? ORDER BY id LIMIT 501",
                (recipe["id"],))]
            tags = [str(row[0]) for row in c.execute(
                "SELECT t.name FROM tags t JOIN recipe_tags rt ON rt.tag_id=t.id WHERE rt.recipe_id=? LIMIT 101",
                (recipe["id"],))]
            characters += len(str(recipe["name"] or "")) + len(str(recipe["description"] or ""))
            characters += sum(len(str(value or "")) for ingredient in ingredients for value in ingredient.values())
            characters += sum(map(len, tags))
            if characters > MAX_TEXT_CHARACTERS or len(ingredients) > 500 or len(tags) > 100:
                raise HTTPException(422, "Die ausgewählten Rezepte enthalten zu viel Text für einen Wochenexport.")
            keys = {_key(ingredient) for ingredient in ingredients if _key(ingredient)}
            rows.append({**item, "name": recipe["name"] or "Unbenannt", "categories": recipe_categories(recipe, set(tags), keys),
                         "total_minutes": explicit_total_minutes(recipe),
                         "vegetarian": bool({tag.strip().casefold() for tag in tags} & {"vegetarisch", "vegan"}),
                         "_keys": keys,
                         "_complete": recipe["ingredients_status"] == "ok" and bool(recipe["has_steps"])
                                      and _complete_ingredients(ingredients)})
    rows.sort(key=lambda row: (row["date"], row["recipe_id"]))
    warnings = _snapshot_warnings(criteria, rows, recent)
    return {"criteria": criteria, "items": rows, "warnings": warnings}


def _snapshot_warnings(criteria: dict, rows: list[dict], recent: set[int]) -> list[str]:
    warnings = []
    if len(rows) != criteria["count"]:
        warnings.append(f"Aktuell {len(rows)} von {criteria['count']} gewünschten Gerichten ausgewählt.")
    vegetarian = sum(row["vegetarian"] for row in rows)
    if vegetarian < criteria["vegetarian_count"]:
        warnings.append(f"Aktuell {vegetarian} von mindestens {criteria['vegetarian_count']} vegetarisch markierten Gerichten.")
    for quota in criteria["category_quotas"]:
        actual = sum(quota["category"] in row["categories"] for row in rows)
        if actual != quota["count"]:
            warnings.append(f"{CATEGORY_NAMES[quota['category']]}: aktuell {actual}, gewünscht exakt {quota['count']}.")
    excluded = available_keys(criteria["excluded_ingredients"])
    excluded_ids = set(criteria["exclude_recipe_ids"])
    for row in rows:
        prefix = f"{WEEKDAYS[row['date'].weekday()]}: {row['name']}"
        if not row["_complete"]:
            warnings.append(f"{prefix} - Zutaten oder Rezeptprüfung sind nicht vollständig.")
        if criteria["max_minutes"] is not None and (row["total_minutes"] is None or row["total_minutes"] > criteria["max_minutes"]):
            warnings.append(f"{prefix} - die allgemeine Zeitgrenze ist nicht nachgewiesen.")
        if excluded_ingredient_match(row["_keys"], excluded):
            warnings.append(f"{prefix} - enthält eine ausgeschlossene Zutat.")
        if row["recipe_id"] in recent:
            warnings.append(f"{prefix} - wurde im gewählten Rückblick bereits geplant.")
        if row["recipe_id"] in excluded_ids:
            warnings.append(f"{prefix} - gehört zu den ausgeschlossenen Rezepten.")
    for rule in criteria.get("weekday_rules", []):
        matches = [row for row in rows if row["date"].isoweekday() == rule["weekday"]]
        day = WEEKDAYS[rule["weekday"] - 1]
        if not matches:
            warnings.append(f"{day}: Für die Tagesregel ist noch kein Gericht ausgewählt.")
        elif not all(matches_weekday_rule(row, rule) for row in matches):
            warnings.append(f"{day}: Das ausgewählte Gericht erfüllt die Tagesregel nicht.")
    return warnings


def _rule_text(rule):
    parts = []
    if rule.get("category"):
        parts.append(CATEGORY_NAMES[rule["category"]])
    if rule.get("min_minutes") is not None:
        parts.append(f"mindestens {rule['min_minutes']} Min.")
    if rule.get("max_minutes") is not None:
        parts.append(f"höchstens {rule['max_minutes']} Min.")
    if rule.get("vegetarian"):
        parts.append("vegetarisch")
    return ", ".join(parts) or "freies Gericht"


def build_meal_plan_pdf(snapshot: dict) -> bytes:
    import reportlab
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    with _font_lock:
        if "MealPlanSans" not in pdfmetrics.getRegisteredFontNames():
            fonts = Path(reportlab.__file__).parent / "fonts"
            pdfmetrics.registerFont(TTFont("MealPlanSans", str(fonts / "Vera.ttf")))
            pdfmetrics.registerFont(TTFont("MealPlanSans-Bold", str(fonts / "VeraBd.ttf")))
            pdfmetrics.registerFontFamily("MealPlanSans", normal="MealPlanSans", bold="MealPlanSans-Bold")
    ink, muted, plum = [colors.HexColor(value) for value in ("#372C34", "#73686F", "#865878")]
    body = ParagraphStyle("MealPlanBody", fontName="MealPlanSans", fontSize=9, leading=14, textColor=ink, spaceAfter=6)
    small = ParagraphStyle("MealPlanSmall", parent=body, fontSize=8, leading=12, textColor=muted)
    heading = ParagraphStyle("MealPlanHeading", parent=body, fontName="MealPlanSans-Bold", fontSize=13,
                             leading=18, spaceBefore=12, spaceAfter=8, keepWithNext=True)
    title = ParagraphStyle("MealPlanTitle", parent=heading, fontSize=25, leading=32, textColor=plum)
    criteria, rows = snapshot["criteria"], snapshot["items"]
    anchor = criteria["week_start"]
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, leftMargin=19 * mm, rightMargin=19 * mm,
                            topMargin=18 * mm, bottomMargin=20 * mm, title="Wochenvorschläge",
                            author="Quellenküche", subject="Planungsentwurf mit Vorgaben und Tagesregeln")

    def footer(canvas, document):
        if document.page > 25:
            raise HTTPException(422, "Der Wochenexport überschreitet 25 Seiten. Bitte Texte kürzen.")
        canvas.saveState()
        canvas.setFont("MealPlanSans", 8)
        canvas.setFillColor(muted)
        canvas.drawString(19 * mm, 10 * mm, "Quellenküche · Wochenvorschläge")
        canvas.drawRightString(A4[0] - 19 * mm, 10 * mm, f"Seite {document.page}")
        canvas.restoreState()

    story = [Paragraph("Wochenvorschläge", title),
             Paragraph(f"{anchor.strftime('%d.%m.%Y')} bis {(anchor + timedelta(days=6)).strftime('%d.%m.%Y')}", body),
             Paragraph("Planungsentwurf. Dieser Export übernimmt keine Gerichte in den Wochenplan.", small),
             Paragraph("Ausgewählte Gerichte", heading)]
    # Individual paragraphs can split over pages, including unusually long recipe names.
    for row in rows:
        date_label = f"{WEEKDAYS[row['date'].weekday()]}, {row['date'].strftime('%d.%m.')}"
        story.append(Paragraph(_text(date_label), ParagraphStyle("MealPlanDay", parent=body, fontName="MealPlanSans-Bold", keepWithNext=True)))
        story.append(Paragraph(_text(row["name"]), body))
        metadata = [f"{row['servings']} Portionen",
                    f"{row['total_minutes']} Min. Gesamtzeit" if row["total_minutes"] is not None else "Gesamtzeit unbekannt"]
        if row["vegetarian"]:
            metadata.append("vegetarisch markiert")
        story.extend([Paragraph(_text(" · ".join(metadata)), small), Spacer(1, 2 * mm)])
    story.append(Paragraph("Deine Wochenvorgaben", heading))
    story.append(Paragraph("Rezeptauswahl: " + ("nur aus dem Haushalt" if criteria.get("household_only") else "globaler Bestand und Haushalt"), body))
    general = [f"{criteria['count']} Gerichte", f"mindestens {criteria['vegetarian_count']} vegetarisch markiert",
               f"höchstens {criteria['max_minutes']} Min. pro Gericht" if criteria["max_minutes"] is not None else "keine allgemeine Zeitgrenze"]
    story.append(Paragraph(_text(" · ".join(general)), body))
    for quota in criteria["category_quotas"]:
        actual = sum(quota["category"] in row["categories"] for row in rows)
        story.append(Paragraph(_text(f"{CATEGORY_NAMES[quota['category']]}: exakt {quota['count']} gewünscht, {actual} ausgewählt"), body))
    if criteria["excluded_ingredients"]:
        story.append(Paragraph("Ausgeschlossene Zutaten: " + _text(", ".join(criteria["excluded_ingredients"])), body))
    if criteria["avoid_recent_days"]:
        story.append(Paragraph(f"Keine Wiederholungen aus den {criteria['avoid_recent_days']} Tagen vor dieser Woche.", body))
    if criteria.get("weekday_rules"):
        story.append(Paragraph("Tagesregeln", heading))
        for rule in sorted(criteria["weekday_rules"], key=lambda value: value["weekday"]):
            story.append(Paragraph(_text(WEEKDAYS[rule["weekday"] - 1] + ": " + _rule_text(rule)), body))
    story.append(Paragraph("Prüfung dieser Auswahl", heading))
    if snapshot["warnings"]:
        story.extend(Paragraph("- " + _text(warning), body) for warning in snapshot["warnings"])
    else:
        story.append(Paragraph("Die aktuelle Auswahl erfüllt die überprüfbaren Vorgaben anhand der gespeicherten Rezeptdaten.", body))
    story.append(Paragraph("Die Prüfung bezieht sich auf diese Auswahl und die aktuell gespeicherten Rezeptdaten. "
                           "Kategorien werden aus Zutatennamen erkannt; ein Gericht kann zu mehreren Kategorien zählen. "
                           "Vegetarisch richtet sich nach Rezept-Tags. Zutatenausschlüsse ersetzen keine Allergenprüfung.", small))
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    result = output.getvalue()
    if len(result) > MAX_PDF_BYTES:
        raise HTTPException(422, "Die PDF-Datei ist größer als 2 MB. Bitte Texte kürzen.")
    return result
