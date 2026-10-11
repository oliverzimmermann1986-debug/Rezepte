"""Private cookbook PDF snapshots. Media are read only from the recipe store."""
from __future__ import annotations

import html
import re
import threading
import warnings
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

from ..core.safety import resolve_directory_under, resolve_regular_file_under
from ..db import RECIPE_VARIANT_PENDING_STATUS
from .household_features import _book, account_id
from .recipe_pdf import _ingredient_line

MAX_RECIPES = 100
MAX_TEXT_CHARACTERS = 250_000
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_PDF_BYTES = 30 * 1024 * 1024
_font_lock = threading.Lock()


def cookbook_snapshot(db, book_id: int) -> tuple[dict, list[dict]]:
    """One read transaction: membership, visibility and contents share a snapshot."""
    with db.conn() as c:
        c.execute("BEGIN")
        book = dict(_book(c, db, book_id))
        rows = c.execute(
            "SELECT r.* FROM household_cookbook_recipes cr JOIN recipes r ON r.id=cr.recipe_id "
            "WHERE cr.cookbook_id=? AND r.deleted_at IS NULL "
            "AND COALESCE(r.ingredients_status,'')<>? "
            "AND (r.owner_account_id IS NULL OR r.owner_account_id=?) "
            "ORDER BY r.name COLLATE NOCASE,r.id LIMIT ?",
            (book_id, RECIPE_VARIANT_PENDING_STATUS, account_id(db), MAX_RECIPES + 1),
        ).fetchall()
        if len(rows) > MAX_RECIPES:
            raise HTTPException(422, f"Ein PDF kann höchstens {MAX_RECIPES} Rezepte enthalten. Bitte das Kochbuch aufteilen.")
        if not rows:
            raise HTTPException(422, "Dieses Kochbuch enthält noch keine verfügbaren Rezepte.")
        recipes = []
        characters = len(book["name"])
        for row in rows:
            recipe = dict(row)
            recipe["url"] = recipe.get("source_url") or recipe.get("url")
            recipe["ingredients"] = [dict(item) for item in c.execute(
                "SELECT * FROM recipe_ingredients WHERE recipe_id=? ORDER BY sort_order,id LIMIT 501", (row["id"],))]
            recipe["steps"] = [dict(item) for item in c.execute(
                "SELECT * FROM recipe_steps WHERE recipe_id=? ORDER BY step_number,id LIMIT 501", (row["id"],))]
            characters += sum(len(str(recipe.get(key) or "")) for key in ("name", "description", "url", "type", "category"))
            characters += sum(len(_ingredient_line(item)) for item in recipe["ingredients"])
            characters += sum(len(str(item.get("instruction") or "")) for item in recipe["steps"])
            if characters > MAX_TEXT_CHARACTERS or len(recipe["ingredients"]) > 500 or len(recipe["steps"]) > 500:
                raise HTTPException(422, "Dieses Kochbuch ist für eine PDF-Datei zu umfangreich. Bitte in kleinere Kochbücher aufteilen.")
            recipes.append(recipe)
    return book, recipes


def local_recipe_image(recipe: dict, root: Path) -> bytes | None:
    """Bounded, normalized JPEG; never fetch URLs or accept paths outside the store."""
    if not recipe.get("thumb_filename") or not recipe.get("folder_path"):
        return None
    try:
        folder = resolve_directory_under(Path(recipe["folder_path"]), root)
        path = resolve_regular_file_under(folder / str(recipe["thumb_filename"]), folder, root)
        with path.open("rb") as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            return None
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as original:
                if original.width * original.height > MAX_IMAGE_PIXELS:
                    return None
                original.thumbnail((1200, 900))
                image = ImageOps.exif_transpose(original).convert("RGB")
                output = BytesIO()
                image.save(output, "JPEG", quality=85, optimize=True)
                return output.getvalue()
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return None


def _text(value) -> str:
    # Keep user text literal, including angle brackets; remove nonprinting controls.
    return html.escape(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or ""))).replace("\n", "<br/>")


def build_cookbook_pdf(book: dict, recipes: list[dict], root: Path) -> bytes:
    import reportlab
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import BaseDocTemplate, Frame, Image as PDFImage, PageBreak, PageTemplate, Paragraph, Spacer
    from reportlab.platypus.tableofcontents import TableOfContents

    # ReportLab ships these fonts on every supported server; no OS-font dependency.
    with _font_lock:
        if "CookbookSans" not in pdfmetrics.getRegisteredFontNames():
            fonts = Path(reportlab.__file__).parent / "fonts"
            pdfmetrics.registerFont(TTFont("CookbookSans", str(fonts / "Vera.ttf")))
            pdfmetrics.registerFont(TTFont("CookbookSans-Bold", str(fonts / "VeraBd.ttf")))
            pdfmetrics.registerFontFamily("CookbookSans", normal="CookbookSans", bold="CookbookSans-Bold")

    ink, muted, plum = [colors.HexColor(value) for value in ("#372C34", "#73686F", "#865878")]
    body = ParagraphStyle("CookbookBody", fontName="CookbookSans", fontSize=10, leading=15, textColor=ink, spaceAfter=7)
    small = ParagraphStyle("CookbookSmall", parent=body, fontSize=8, leading=12, textColor=muted)
    title = ParagraphStyle("CookbookTitle", parent=body, fontName="CookbookSans-Bold", fontSize=28, leading=35, spaceAfter=14)
    recipe_title = ParagraphStyle("CookbookRecipeTitle", parent=title, fontSize=22, leading=28)
    heading = ParagraphStyle("CookbookHeading", parent=body, fontName="CookbookSans-Bold", fontSize=13, leading=18,
                             spaceBefore=12, spaceAfter=7, keepWithNext=True)
    ingredient = ParagraphStyle("CookbookIngredient", parent=body, leftIndent=10, firstLineIndent=-10, spaceAfter=4)
    output = BytesIO()

    class CookbookDocument(BaseDocTemplate):
        def afterFlowable(self, flowable):
            if self.page > 500:
                raise HTTPException(422, "Das Kochbuch überschreitet 500 PDF-Seiten. Bitte das Kochbuch aufteilen.")
            if isinstance(flowable, Paragraph) and flowable.style.name == "CookbookRecipeTitle":
                key = flowable.cookbook_key
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(flowable.getPlainText(), key, 0)
                self.notify("TOCEntry", (0, _text(flowable.getPlainText()), self.page, key))

    doc = CookbookDocument(output, pagesize=A4, leftMargin=19 * mm, rightMargin=19 * mm,
                           topMargin=19 * mm, bottomMargin=19 * mm, title=str(book["name"]),
                           author="Quellenküche", subject="Privates Kochbuch")

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFillColor(muted)
        canvas.setStrokeColor(colors.HexColor("#E4DBE0"))
        canvas.line(19 * mm, 14 * mm, A4[0] - 19 * mm, 14 * mm)
        canvas.setFont("CookbookSans", 8)
        canvas.drawString(19 * mm, 9 * mm, "Quellenküche")
        canvas.drawRightString(A4[0] - 19 * mm, 9 * mm, f"Seite {document.page}")
        canvas.restoreState()

    doc.addPageTemplates(PageTemplate(id="cookbook", frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                                                                      leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=footer))
    story = [Spacer(1, 32 * mm), Paragraph("DEIN KOCHBUCH", ParagraphStyle("CookbookEyebrow", parent=small, textColor=plum)),
             Paragraph(_text(book["name"]), title), Paragraph(f"{len(recipes)} Rezepte aus deiner Quellenküche", body),
             Spacer(1, 8 * mm), Paragraph("Mit Zutaten, Zubereitung und Quellen zum Nachkochen, Aufbewahren und Teilen.", small), PageBreak(),
             Paragraph("Inhalt", title)]
    toc = TableOfContents()
    toc.levelStyles = [ParagraphStyle("CookbookContents", parent=body, fontSize=10, leading=16, spaceBefore=5, rightIndent=24)]
    toc.dotsMinLevel = 0
    story.append(toc)
    for position, recipe in enumerate(recipes):
        story.append(PageBreak())
        recipe_heading = Paragraph(_text(recipe.get("name") or "Unbenannt"), recipe_title)
        recipe_heading.cookbook_key = f"recipe-{position}"
        story.append(recipe_heading)
        metadata = [str(recipe.get(key)) for key in ("type", "category") if recipe.get(key)]
        if recipe.get("servings"):
            metadata.append(f"{recipe['servings']} Portionen")
        if metadata:
            story.append(Paragraph(_text(" · ".join(metadata)), small))
        photo = local_recipe_image(recipe, root)
        if photo:
            image = PDFImage(BytesIO(photo))
            scale = min(doc.width / image.imageWidth, 68 * mm / image.imageHeight)
            image.drawWidth, image.drawHeight = image.imageWidth * scale, image.imageHeight * scale
            image.hAlign = "LEFT"
            story.extend([Spacer(1, 3 * mm), image, Spacer(1, 4 * mm)])
        elif recipe.get("thumb_filename"):
            story.append(Paragraph("Rezeptbild momentan nicht verfügbar.", small))
        if recipe.get("description"):
            story.append(Paragraph(_text(recipe["description"]), body))
        story.append(Paragraph("Zutaten", heading))
        lines = [_ingredient_line(item) for item in recipe.get("ingredients", [])]
        story.extend(Paragraph("- " + _text(line), ingredient) for line in lines if line)
        if not any(lines):
            story.append(Paragraph("Noch keine Zutaten erfasst.", small))
        story.append(Paragraph("Zubereitung", heading))
        steps = [str(item.get("instruction") or "").strip() for item in recipe.get("steps", [])]
        steps = [step for step in steps if step]
        story.extend(Paragraph(f"<b>{number}.</b> {_text(step)}", body) for number, step in enumerate(steps, 1))
        if not steps:
            story.append(Paragraph("Noch keine Zubereitungsschritte erfasst.", small))
        source = str(recipe.get("url") or "")
        if source.startswith(("https://", "http://")):
            story.extend([Spacer(1, 4 * mm), Paragraph("Quelle: " + _text(source), small)])
    doc.multiBuild(story)
    result = output.getvalue()
    if len(result) > MAX_PDF_BYTES:
        raise HTTPException(422, "Die PDF-Datei ist größer als 30 MB. Bitte das Kochbuch aufteilen.")
    return result
