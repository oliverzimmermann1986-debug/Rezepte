"""Konservative PDF-/Scan-Aufbereitung für Rezeptimporte.

Die Standardfunktionen (Drehen, Leerseiten entfernen, Weißränder beschneiden)
ändern keine Bildauflösung. Optionales Deskew für echte Scans rastert nur die
betroffene Seite neu und ist deshalb standardmäßig deaktiviert.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from .pdf_rotation import normalize_pdf_bytes

logger = logging.getLogger(__name__)


@dataclass
class PdfPageAnalysis:
    page: int
    text_chars: int = 0
    dark_ratio: float = 0.0
    blank: bool = False
    crop_possible: bool = False
    crop_saving_percent: float = 0.0
    skew_angle: float = 0.0


@dataclass
class PdfProcessReport:
    ok: bool = True
    changed: bool = False
    pages_before: int = 0
    pages_after: int = 0
    rotated_pages: int = 0
    orientation_detected_pages: int = 0
    orientation_skipped_pages: int = 0
    rotation_reason: Optional[str] = None
    rotation_decisions: list[dict] = field(default_factory=list)
    cropped_pages: int = 0
    removed_blank_pages: int = 0
    deskewed_pages: int = 0
    ocr_pages: int = 0
    contrast_pages: int = 0
    sharpened_pages: int = 0
    original_backup: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    reason: Optional[str] = None
    error: Optional[str] = None
    pages: list[PdfPageAnalysis] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["pages"] = [asdict(p) for p in self.pages]
        return data


def _render_gray(page: Any, dpi: int = 96):
    import pymupdf
    from PIL import Image
    pix = page.get_pixmap(dpi=max(72, min(200, int(dpi))), colorspace=pymupdf.csGRAY,
                             alpha=False, annots=False)
    return Image.frombytes("L", (pix.width, pix.height), pix.samples)


def _safe_render_dpi(page: Any, requested_dpi: int, *, max_pixels: int = 24_000_000) -> int:
    """Begrenzt den Speicherbedarf ungewöhnlich großer PDF-Seiten.

    24 Mio. RGB-Pixel benötigen grob 72 MiB nur für das Rohbild. Ohne diese
    Begrenzung können A2/A1-Scans bei 300–400 DPI den Webdienst vom OOM-Killer
    beenden, was im Browser nur als „PDF-Verarbeitung fehlgeschlagen“ erscheint.
    """
    dpi = max(180, min(400, int(requested_dpi or 300)))
    try:
        width_in = max(0.1, float(page.rect.width) / 72.0)
        height_in = max(0.1, float(page.rect.height) / 72.0)
        estimated = width_in * height_in * dpi * dpi
        if estimated <= max_pixels:
            return dpi
        scale = (max_pixels / estimated) ** 0.5
        return max(150, int(dpi * scale))
    except Exception:
        return dpi


def _content_bbox(image, threshold: int = 245):
    # Schwarz = Inhalt, Weiß = Hintergrund; getbbox liefert Content-Rechteck.
    mask = image.point(lambda p: 255 if p < threshold else 0)
    return mask.getbbox(), mask


def _analyze_page(page: Any, page_no: int, *, dpi: int = 96,
                  blank_dark_ratio: float = 0.0015) -> PdfPageAnalysis:
    try:
        text_chars = sum(1 for ch in (page.get_text("text") or "") if ch.isalnum())
    except Exception:
        text_chars = 0
    image = _render_gray(page, dpi=dpi)
    bbox, mask = _content_bbox(image)
    hist = mask.histogram()
    dark_pixels = hist[255] if len(hist) > 255 else 0
    total = max(1, image.width * image.height)
    dark_ratio = dark_pixels / total
    blank = text_chars < 4 and dark_ratio < blank_dark_ratio
    crop_possible = False
    saving = 0.0
    if bbox and not blank:
        x0, y0, x1, y1 = bbox
        content_area = max(1, (x1 - x0) * (y1 - y0))
        saving = max(0.0, 100.0 * (1.0 - content_area / total))
        # Nur sinnvolle Ränder markieren; minimale Druckerränder bleiben erhalten.
        crop_possible = saving >= 5.0 and (x0 > 5 or y0 > 5 or x1 < image.width - 5 or y1 < image.height - 5)
    return PdfPageAnalysis(
        page=page_no, text_chars=text_chars, dark_ratio=round(dark_ratio, 6),
        blank=blank, crop_possible=crop_possible,
        crop_saving_percent=round(saving, 2),
    )


def _estimate_skew(image, max_angle: float = 4.0, step: float = 0.5) -> Tuple[float, float]:
    """Projektionsprofil-Schätzung für Scan-Seiten. Returnt Winkel + Gewinn."""
    from PIL import ImageOps
    small = ImageOps.autocontrast(image.copy())
    small.thumbnail((900, 900))
    binary = small.point(lambda p: 0 if p < 210 else 255)

    def score(img) -> float:
        # Anzahl dunkler Pixel pro Zeile; Textzeilen erzeugen starke Peaks.
        px = img.load(); w, h = img.size
        rows = []
        for y in range(h):
            rows.append(sum(1 for x in range(w) if px[x, y] < 128))
        if not rows:
            return 0.0
        mean = sum(rows) / len(rows)
        return sum((v - mean) ** 2 for v in rows) / len(rows)

    base = score(binary)
    best_angle, best = 0.0, base
    angle = -max_angle
    while angle <= max_angle + 0.001:
        if abs(angle) >= 0.25:
            rotated = binary.rotate(angle, resample=0, expand=True, fillcolor=255)
            value = score(rotated)
            if value > best:
                best_angle, best = angle, value
        angle += step
    gain = (best - base) / max(base, 1.0)
    if abs(best_angle) < 0.5 or gain < 0.08:
        return 0.0, gain
    return round(best_angle, 2), gain


def analyze_pdf_bytes(pdf_bytes: bytes, *, detect_skew: bool = False,
                      max_pages: int = 100) -> PdfProcessReport:
    report = PdfProcessReport()
    try:
        import pymupdf
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        report.ok = False; report.reason = "invalid_pdf"; report.error = str(exc)
        return report
    try:
        report.pages_before = report.pages_after = len(doc)
        if getattr(doc, "needs_pass", False):
            report.reason = "encrypted"; return report
        for idx in range(min(len(doc), max(1, int(max_pages)))):
            page = doc[idx]
            info = _analyze_page(page, idx + 1)
            if detect_skew and info.text_chars < 20 and not info.blank:
                try:
                    angle, _gain = _estimate_skew(_render_gray(page, dpi=120))
                    info.skew_angle = angle
                except Exception:
                    pass
            report.pages.append(info)
        return report
    finally:
        doc.close()


def _crop_page(page: Any, analysis: PdfPageAnalysis, *, margin_points: float = 18.0) -> bool:
    if not analysis.crop_possible:
        return False
    image = _render_gray(page, dpi=96)
    bbox, _ = _content_bbox(image)
    if not bbox:
        return False
    x0, y0, x1, y1 = bbox
    rect = page.rect
    sx = rect.width / image.width
    sy = rect.height / image.height
    crop = type(rect)(
        rect.x0 + x0 * sx - margin_points,
        rect.y0 + y0 * sy - margin_points,
        rect.x0 + x1 * sx + margin_points,
        rect.y0 + y1 * sy + margin_points,
    )
    crop &= rect
    # Keine schmalen Mittelstreifen erzeugen. Gerade bei gedrehten Scans kann
    # eine aggressive Bounding-Box sonst sichtbaren Text abschneiden.
    if crop.width < rect.width * 0.70 or crop.height < rect.height * 0.70:
        return False
    if crop.width > rect.width * 0.98 and crop.height > rect.height * 0.98:
        return False
    page.set_cropbox(crop)
    return True


def process_pdf_bytes(
    pdf_bytes: bytes,
    *,
    auto_rotate: bool = True,
    use_tesseract_osd: bool = True,
    remove_blank_pages: bool = True,
    auto_crop: bool = True,
    deskew_scans: bool = False,
    ocr_scans: bool = False,
    improve_contrast: bool = True,
    sharpen_scans: bool = True,
    scan_dpi: int = 300,
    ocr_language: str = "deu+eng",
    min_text_chars: int = 20,
    text_dominance: float = 0.60,
    osd_min_confidence: float = 1.0,
    max_osd_pages: int = 100,
    use_ocr_vote: bool = True,
) -> Tuple[bytes, PdfProcessReport]:
    report = PdfProcessReport()
    source = pdf_bytes
    rotation_report = None
    if auto_rotate:
        source, rotation_report = normalize_pdf_bytes(
            source, enabled=True, use_tesseract_osd=use_tesseract_osd,
            min_text_chars=min_text_chars, text_dominance=text_dominance,
            osd_min_confidence=osd_min_confidence, max_osd_pages=max_osd_pages,
            use_ocr_vote=use_ocr_vote, ocr_language=ocr_language,
            ocr_vote_dpi=min(240, max(150, int(scan_dpi * 0.65))),
        )
        report.rotated_pages = rotation_report.rotated_pages
        report.orientation_detected_pages = rotation_report.detected_pages
        report.orientation_skipped_pages = rotation_report.skipped_pages
        report.rotation_reason = rotation_report.reason
        report.rotation_decisions = [
            {**asdict(item), "changed": item.changed} for item in rotation_report.decisions
        ]

    try:
        import pymupdf
        from PIL import Image
        doc = pymupdf.open(stream=source, filetype="pdf")
    except Exception as exc:
        report.ok = False; report.reason = "invalid_pdf"; report.error = str(exc)
        return pdf_bytes, report

    try:
        report.pages_before = len(doc)
        if getattr(doc, "needs_pass", False):
            report.reason = "encrypted"; return pdf_bytes, report
        try:
            if int(doc.get_sigflags() or 0) > 0:
                report.reason = "signed_pdf"; return pdf_bytes, report
        except Exception:
            pass

        analyses = [_analyze_page(doc[i], i + 1) for i in range(len(doc))]
        report.pages = analyses

        # Raster-Operationen gelten ausschließlich für echte Scan-Seiten.
        # Text-/Vektor-PDFs bleiben unangetastet. OCR erzeugt einen unsichtbaren
        # Text-Layer und verbessert dadurch Suche, Copy&Paste und Barrierefreiheit.
        if deskew_scans or ocr_scans or improve_contrast or sharpen_scans:
            from PIL import ImageOps, ImageFilter, ImageEnhance
            angles = []
            raster_flags = []
            for idx, info in enumerate(analyses):
                is_scan = info.text_chars < max(4, min_text_chars) and not info.blank
                angle = 0.0
                if is_scan and deskew_scans:
                    try:
                        angle, _ = _estimate_skew(_render_gray(doc[idx], dpi=120))
                    except Exception:
                        angle = 0.0
                info.skew_angle = angle
                angles.append(angle)
                raster_flags.append(is_scan and (abs(angle) >= 0.5 or ocr_scans or improve_contrast or sharpen_scans))

            if any(raster_flags):
                rebuilt = pymupdf.open()
                for idx, needs_raster in enumerate(raster_flags):
                    if not needs_raster:
                        rebuilt.insert_pdf(doc, from_page=idx, to_page=idx)
                        continue
                    page = doc[idx]
                    requested_dpi = max(180, min(400, int(scan_dpi or 300)))
                    render_dpi = _safe_render_dpi(page, requested_dpi)
                    if render_dpi < requested_dpi:
                        report.warnings.append(
                            f"Seite {idx + 1}: DPI aus Speicherschutz von {requested_dpi} auf {render_dpi} reduziert"
                        )
                    pix = page.get_pixmap(dpi=render_dpi, colorspace=pymupdf.csRGB, alpha=False, annots=False)
                    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    if improve_contrast:
                        image = ImageOps.autocontrast(image, cutoff=0.5)
                        image = ImageEnhance.Contrast(image).enhance(1.08)
                        report.contrast_pages += 1
                    if sharpen_scans:
                        image = image.filter(ImageFilter.UnsharpMask(radius=1.2, percent=125, threshold=3))
                        report.sharpened_pages += 1
                    angle = angles[idx]
                    if abs(angle) >= 0.5:
                        image = image.rotate(angle, resample=Image.Resampling.BICUBIC,
                                             expand=False, fillcolor="white")
                        report.deskewed_pages += 1
                    buf = io.BytesIO(); image.save(buf, format="PNG", optimize=True)
                    png = buf.getvalue()
                    inserted = False
                    if ocr_scans:
                        try:
                            ocr_pix = pymupdf.Pixmap(png)
                            ocr_pix.set_dpi(render_dpi, render_dpi)
                            one_page_pdf = ocr_pix.pdfocr_tobytes(
                                language=(ocr_language or "deu+eng")[:80], compress=True
                            )
                            ocr_doc = pymupdf.open(stream=one_page_pdf, filetype="pdf")
                            try:
                                rebuilt.insert_pdf(ocr_doc)
                            finally:
                                ocr_doc.close()
                            report.ocr_pages += 1
                            inserted = True
                        except Exception as exc:
                            report.warnings.append(f"OCR Seite {idx + 1} übersprungen: {exc}")
                    if not inserted:
                        new_page = rebuilt.new_page(width=page.rect.width, height=page.rect.height)
                        new_page.insert_image(new_page.rect, stream=png)
                doc.close(); doc = rebuilt
                analyses = [_analyze_page(doc[i], i + 1) for i in range(len(doc))]
                report.pages = analyses

        if auto_crop:
            for idx, info in enumerate(analyses):
                try:
                    if _crop_page(doc[idx], info):
                        report.cropped_pages += 1
                except Exception as exc:
                    logger.debug("PDF crop page %s skipped: %s", idx + 1, exc)

        if remove_blank_pages and len(doc) > 1:
            blank_indexes = [i for i, info in enumerate(analyses) if info.blank]
            # Niemals alle Seiten entfernen.
            if 0 < len(blank_indexes) < len(doc):
                for idx in reversed(blank_indexes):
                    doc.delete_page(idx)
                report.removed_blank_pages = len(blank_indexes)

        report.pages_after = len(doc)
        report.changed = bool(
            report.rotated_pages or report.cropped_pages or
            report.removed_blank_pages or report.deskewed_pages or
            report.ocr_pages or report.contrast_pages or report.sharpened_pages
        )
        if not report.changed:
            report.reason = "no_changes"
            return pdf_bytes, report
        output = doc.tobytes(garbage=4, deflate=True, clean=False)
        check = pymupdf.open(stream=output, filetype="pdf")
        try:
            if len(check) != report.pages_after:
                raise ValueError("Seitenzahl nach PDF-Aufbereitung inkonsistent")
        finally:
            check.close()
        return output, report
    except Exception as exc:
        logger.warning("PDF-Aufbereitung fehlgeschlagen: %s", exc)
        report.ok = False; report.changed = False; report.reason = "processing_error"; report.error = str(exc)
        return pdf_bytes, report
    finally:
        try:
            doc.close()
        except Exception:
            pass


def backup_original_pdf(path: Path, backup_root: Optional[Path] = None,
                        data: Optional[bytes] = None) -> Path:
    """Sichert ein PDF kollisionsfrei. Der Pfad-Hash trennt gleichnamige
    Rezepte; time_ns verhindert Kollisionen innerhalb derselben Sekunde."""
    path = Path(path)
    root = Path(backup_root) if backup_root else path.parent / ".pdf-originals"
    root.mkdir(parents=True, exist_ok=True)
    path_hash = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:10]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = root / f"{path.stem}-{path_hash}-{stamp}-{time.time_ns() % 1_000_000_000:09d}{path.suffix}"
    with open(backup, "xb") as fh:
        fh.write(path.read_bytes() if data is None else data)
        fh.flush(); os.fsync(fh.fileno())
    os.chmod(backup, 0o600)
    return backup


def process_pdf_path(path: Path, *, backup_root: Optional[Path] = None,
                     keep_original: bool = True, **kwargs: Any) -> PdfProcessReport:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        return PdfProcessReport(ok=False, reason="invalid_path")
    original = path.read_bytes()
    output, report = process_pdf_bytes(original, **kwargs)
    if not report.changed:
        return report
    try:
        if keep_original:
            report.original_backup = str(backup_original_pdf(path, backup_root, original))
        from .safety import atomic_write_bytes
        mode = path.stat().st_mode & 0o777
        atomic_write_bytes(path, output)
        try: os.chmod(path, mode)
        except OSError: pass
    except Exception as exc:
        report.ok = False; report.changed = False; report.reason = "write_error"; report.error = str(exc)
    return report


def find_recipe_pdfs(recipe_root: Path) -> Iterable[Path]:
    recipe_root = Path(recipe_root).resolve()
    if not recipe_root.is_dir():
        return []
    result = []
    for pdf in recipe_root.rglob("*.pdf"):
        try:
            if not pdf.is_file() or pdf.is_symlink() or ".pdf-originals" in pdf.parts:
                continue
            pdf.resolve().relative_to(recipe_root)
            result.append(pdf)
        except Exception:
            continue
    return sorted(result)
