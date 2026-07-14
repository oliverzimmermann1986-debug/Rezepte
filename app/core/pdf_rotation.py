"""Automatische, nicht-rasternde PDF-Ausrichtung.

Die Seiten werden nicht als Bilder neu aufgebaut. Stattdessen wird nur die
PDF-Seitenrotation korrigiert. Dadurch bleiben Text-Layer, Vektoren und Bilder
im Original erhalten.

Erkennung:
1. Vorhandener Text-Layer: dominante Schreibrichtung via PyMuPDF.
2. Scan ohne Text-Layer: optional Tesseract OSD auf einer kleinen Vorschau.

Bei uneindeutigen Seiten bleibt die Rotation unverändert. Signierte und
verschlüsselte PDFs werden aus Sicherheitsgründen nicht verändert.
"""
from __future__ import annotations

import logging
import math
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_CARDINALS = (0, 90, 180, 270)


@dataclass
class PageRotation:
    page: int
    old_rotation: int
    new_rotation: int
    method: str
    confidence: float
    text_chars: int = 0

    @property
    def changed(self) -> bool:
        return self.old_rotation != self.new_rotation


@dataclass
class PdfRotationReport:
    ok: bool = True
    changed: bool = False
    pages: int = 0
    rotated_pages: int = 0
    detected_pages: int = 0
    skipped_pages: int = 0
    reason: Optional[str] = None
    error: Optional[str] = None
    decisions: list[PageRotation] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["decisions"] = [
            {**asdict(d), "changed": d.changed} for d in self.decisions
        ]
        return payload


def _nearest_cardinal(angle: float, tolerance: float = 18.0) -> Optional[int]:
    """Rundet einen Winkel auf 0/90/180/270, wenn er nahe genug liegt."""
    angle = angle % 360.0
    best = min(_CARDINALS, key=lambda x: min(abs(angle - x), 360 - abs(angle - x)))
    distance = min(abs(angle - best), 360 - abs(angle - best))
    return int(best) if distance <= tolerance else None


def _text_target_rotation(
    page: Any,
    *,
    min_chars: int = 20,
    dominance: float = 0.65,
) -> Optional[Tuple[int, float, int]]:
    """Bestimmt die Zielrotation aus der dominanten Text-Schreibrichtung.

    PyMuPDF liefert pro Textzeile einen Richtungsvektor. Der Zielwinkel ist
    der negative Textwinkel, sodass die dominante Schrift horizontal von links
    nach rechts angezeigt wird.
    """
    scores = {0: 0, 90: 0, 180: 0, 270: 0}
    total = 0
    try:
        data = page.get_text("dict")
    except Exception:
        return None

    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(str(span.get("text") or "") for span in spans)
            weight = sum(1 for ch in text if ch.isalnum())
            if weight < 2:
                continue
            direction = line.get("dir") or (1.0, 0.0)
            try:
                dx, dy = float(direction[0]), float(direction[1])
            except (TypeError, ValueError, IndexError):
                continue
            if abs(dx) + abs(dy) < 0.1:
                continue
            content_angle = math.degrees(math.atan2(dy, dx)) % 360.0
            cardinal = _nearest_cardinal(content_angle)
            if cardinal is None:
                continue
            target = int((-cardinal) % 360)
            scores[target] += weight
            total += weight

    if total < max(1, int(min_chars)):
        return None
    target, winner = max(scores.items(), key=lambda item: item[1])
    ratio = winner / total if total else 0.0
    if ratio < float(dominance):
        return None
    return target, ratio, total


def _parse_tesseract_osd(output: str) -> Optional[Tuple[int, float]]:
    """Parst Tesseract-OSD-Ausgabe: (Drehung im Uhrzeigersinn, Confidence)."""
    rotate_match = re.search(r"(?im)^\s*Rotate:\s*(0|90|180|270)\s*$", output or "")
    confidence_match = re.search(
        r"(?im)^\s*Orientation confidence:\s*([0-9]+(?:\.[0-9]+)?)\s*$",
        output or "",
    )
    if not rotate_match:
        return None
    rotate = int(rotate_match.group(1))
    confidence = float(confidence_match.group(1)) if confidence_match else 0.0
    return rotate, confidence


def _osd_target_rotation(
    page: Any,
    *,
    min_confidence: float = 3.0,
    timeout: int = 20,
) -> Optional[Tuple[int, float]]:
    """Erkennt Scan-Ausrichtung mit Tesseract OSD auf einer 150-DPI-Vorschau."""
    if not shutil.which("tesseract"):
        return None
    try:
        pix = page.get_pixmap(dpi=150, alpha=False, annots=False)
        png = pix.tobytes(output="png")
        proc = subprocess.run(
            ["tesseract", "stdin", "stdout", "--psm", "0", "-l", "osd"],
            input=png,
            capture_output=True,
            timeout=max(5, int(timeout)),
            check=False,
        )
        output = (proc.stdout or b"").decode("utf-8", "replace") + "\n" + \
                 (proc.stderr or b"").decode("utf-8", "replace")
        parsed = _parse_tesseract_osd(output)
        if not parsed:
            return None
        rotate, confidence = parsed
        if confidence < float(min_confidence):
            return None
        current = int(page.rotation or 0) % 360
        return (current + rotate) % 360, confidence
    except (subprocess.TimeoutExpired, OSError, RuntimeError, ValueError) as exc:
        logger.debug("Tesseract OSD fehlgeschlagen: %s", exc)
        return None



def _parse_tesseract_tsv(output: str) -> Tuple[float, int, int]:
    """Bewertet Tesseract-TSV: Score, erkannte Zeichen, Wörter.

    Die absolute Confidence ist zwischen Tesseract-Versionen nicht stabil. Für
    die Orientierungswahl zählt deshalb primär der Vergleich derselben Seite in
    vier Drehungen.
    """
    score = 0.0
    chars = 0
    words = 0
    lines = (output or "").splitlines()
    for line in lines[1:]:
        cols = line.split("\t")
        if len(cols) < 12:
            continue
        text = cols[11].strip()
        clean = "".join(ch for ch in text if ch.isalnum())
        # Einzelzeichen und OCR-Symbolsalat erzeugen bei seitlichen Seiten oft
        # überraschend hohe Tesseract-Confidences. Für die Ausrichtung zählen
        # deshalb nur wortähnliche Tokens ab zwei Zeichen; längere Wörter
        # erhalten deutlich mehr Gewicht.
        if len(clean) < 2:
            continue
        try:
            conf = float(cols[10])
        except (TypeError, ValueError):
            conf = -1.0
        if conf < 0:
            continue
        words += 1
        chars += len(clean)
        word_weight = min(len(clean), 18) ** 1.35
        score += max(1.0, conf) * word_weight
    score += chars * 1.2 + words * 5.0
    return score, chars, words


def _rotate_image_clockwise(image: Any, degrees: int) -> Any:
    from PIL import Image
    degrees %= 360
    if degrees == 90:
        return image.transpose(Image.Transpose.ROTATE_270)
    if degrees == 180:
        return image.transpose(Image.Transpose.ROTATE_180)
    if degrees == 270:
        return image.transpose(Image.Transpose.ROTATE_90)
    return image


def _ocr_vote_target_rotation(
    page: Any,
    *,
    language: str = "deu+eng",
    dpi: int = 180,
    min_chars: int = 12,
    score_margin: float = 1.10,
    timeout: int = 25,
) -> Optional[Tuple[int, float, int]]:
    """Fallback für Scan-PDFs, bei denen OSD keine Entscheidung trifft.

    Die Seite wird in vier Ausrichtungen lokal mit Tesseract gelesen. Die
    Ausrichtung mit dem deutlich besten OCR-Score gewinnt. Das ist langsamer
    als OSD, aber bei kurzen Rezeptseiten und Fotos mit Text erheblich robuster.
    """
    if not shutil.which("tesseract"):
        return None
    try:
        import pymupdf
        from PIL import Image, ImageOps, ImageFilter

        pix = page.get_pixmap(
            dpi=max(120, min(260, int(dpi))), colorspace=pymupdf.csGRAY,
            alpha=False, annots=False,
        )
        image = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        image = ImageOps.autocontrast(image, cutoff=0.5)
        image = image.filter(ImageFilter.UnsharpMask(radius=1.0, percent=110, threshold=3))
        # Rechenzeit begrenzen, ohne kleine Schrift unlesbar zu machen.
        image.thumbnail((2200, 2200), Image.Resampling.LANCZOS)

        results = []
        languages = [str(language or "deu+eng")[:80], "eng"]
        for degrees in _CARDINALS:
            candidate = _rotate_image_clockwise(image, degrees)
            buf = __import__("io").BytesIO()
            candidate.save(buf, format="PNG", optimize=True)
            best_for_angle = (0.0, 0, 0)
            for lang in languages:
                cmd = ["tesseract", "stdin", "stdout"]
                if lang:
                    cmd.extend(["-l", lang])
                cmd.extend(["--psm", "6", "tsv"])
                proc = subprocess.run(
                    cmd, input=buf.getvalue(), capture_output=True,
                    timeout=max(8, int(timeout)), check=False,
                )
                text = (proc.stdout or b"").decode("utf-8", "replace")
                parsed = _parse_tesseract_tsv(text)
                if parsed[0] > best_for_angle[0]:
                    best_for_angle = parsed
                if parsed[1] >= min_chars:
                    break
            results.append((degrees, *best_for_angle))

        ranked = sorted(results, key=lambda item: item[1], reverse=True)
        if not ranked:
            return None
        best = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        degrees, score, chars, _words = best
        if chars < max(4, int(min_chars)) or score <= 0:
            return None
        if second_score > 0 and score < second_score * max(1.01, float(score_margin)):
            return None
        current = int(page.rotation or 0) % 360
        confidence = score / max(second_score, 1.0)
        return (current + int(degrees)) % 360, confidence, int(chars)
    except (subprocess.TimeoutExpired, OSError, RuntimeError, ValueError) as exc:
        logger.debug("Tesseract OCR-Vote fehlgeschlagen: %s", exc)
        return None


def normalize_pdf_bytes(
    pdf_bytes: bytes,
    *,
    enabled: bool = True,
    use_tesseract_osd: bool = True,
    min_text_chars: int = 20,
    text_dominance: float = 0.65,
    osd_min_confidence: float = 1.0,
    max_osd_pages: int = 100,
    use_ocr_vote: bool = True,
    ocr_language: str = "deu+eng",
    ocr_vote_dpi: int = 180,
    ocr_vote_min_chars: int = 12,
    ocr_vote_margin: float = 1.10,
) -> Tuple[bytes, PdfRotationReport]:
    """Normalisiert die Seitenrotation eines PDFs und liefert Bytes + Report.

    Fehler sind nicht fatal: Das Original wird unverändert zurückgegeben.
    """
    report = PdfRotationReport()
    if not enabled:
        report.reason = "disabled"
        return pdf_bytes, report
    if not pdf_bytes:
        report.ok = False
        report.reason = "empty"
        return pdf_bytes, report

    try:
        import pymupdf
    except ImportError as exc:
        report.ok = False
        report.reason = "pymupdf_missing"
        report.error = str(exc)
        return pdf_bytes, report

    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        report.ok = False
        report.reason = "invalid_pdf"
        report.error = str(exc)
        return pdf_bytes, report

    try:
        report.pages = len(doc)
        if getattr(doc, "needs_pass", False):
            report.reason = "encrypted"
            return pdf_bytes, report

        # Eine Änderung würde vorhandene digitale Signaturen ungültig machen.
        try:
            if int(doc.get_sigflags() or 0) > 0:
                report.reason = "signed_pdf"
                return pdf_bytes, report
        except Exception:
            pass

        for page_index in range(len(doc)):
            page = doc[page_index]
            old_rotation = int(page.rotation or 0) % 360
            detected = _text_target_rotation(
                page,
                min_chars=min_text_chars,
                dominance=text_dominance,
            )
            if detected:
                new_rotation, confidence, chars = detected
                decision = PageRotation(
                    page=page_index + 1,
                    old_rotation=old_rotation,
                    new_rotation=int(new_rotation),
                    method="text-layer",
                    confidence=round(float(confidence), 4),
                    text_chars=int(chars),
                )
            elif use_tesseract_osd and page_index < max(0, int(max_osd_pages)):
                osd = _osd_target_rotation(
                    page,
                    min_confidence=osd_min_confidence,
                )
                if osd:
                    new_rotation, confidence = osd
                    decision = PageRotation(
                        page=page_index + 1,
                        old_rotation=old_rotation,
                        new_rotation=int(new_rotation),
                        method="tesseract-osd",
                        confidence=round(float(confidence), 4),
                        text_chars=0,
                    )
                elif use_ocr_vote:
                    voted = _ocr_vote_target_rotation(
                        page, language=ocr_language, dpi=ocr_vote_dpi,
                        min_chars=ocr_vote_min_chars, score_margin=ocr_vote_margin,
                    )
                    if not voted:
                        report.skipped_pages += 1
                        continue
                    new_rotation, confidence, chars = voted
                    decision = PageRotation(
                        page=page_index + 1, old_rotation=old_rotation,
                        new_rotation=int(new_rotation), method="tesseract-ocr-vote",
                        confidence=round(float(confidence), 4), text_chars=int(chars),
                    )
                else:
                    report.skipped_pages += 1
                    continue
            elif use_ocr_vote and page_index < max(0, int(max_osd_pages)):
                voted = _ocr_vote_target_rotation(
                    page, language=ocr_language, dpi=ocr_vote_dpi,
                    min_chars=ocr_vote_min_chars, score_margin=ocr_vote_margin,
                )
                if not voted:
                    report.skipped_pages += 1
                    continue
                new_rotation, confidence, chars = voted
                decision = PageRotation(
                    page=page_index + 1, old_rotation=old_rotation,
                    new_rotation=int(new_rotation), method="tesseract-ocr-vote",
                    confidence=round(float(confidence), 4), text_chars=int(chars),
                )
            else:
                report.skipped_pages += 1
                continue

            report.detected_pages += 1
            report.decisions.append(decision)
            if decision.changed:
                page.set_rotation(decision.new_rotation)
                report.rotated_pages += 1

        report.changed = report.rotated_pages > 0
        if not report.changed:
            report.reason = report.reason or "already_upright_or_uncertain"
            return pdf_bytes, report

        output = doc.tobytes(garbage=3, deflate=True, clean=False)
        # Minimaler Integritätscheck vor dem Ersetzen/Speichern.
        check = pymupdf.open(stream=output, filetype="pdf")
        try:
            if len(check) != report.pages:
                raise ValueError("Seitenzahl hat sich bei PDF-Normalisierung geändert")
        finally:
            check.close()
        return output, report
    except Exception as exc:
        logger.warning("PDF-Auto-Rotation fehlgeschlagen: %s", exc)
        report.ok = False
        report.changed = False
        report.error = str(exc)
        report.reason = "processing_error"
        return pdf_bytes, report
    finally:
        doc.close()


def normalize_pdf_path(path: Path, **kwargs: Any) -> PdfRotationReport:
    """Normalisiert eine PDF-Datei atomar. Symlinks werden nicht verfolgt."""
    path = Path(path)
    if path.is_symlink():
        return PdfRotationReport(ok=False, reason="symlink")
    try:
        original = path.read_bytes()
    except Exception as exc:
        return PdfRotationReport(ok=False, reason="read_error", error=str(exc))

    output, report = normalize_pdf_bytes(original, **kwargs)
    if not report.changed:
        return report

    try:
        from .safety import atomic_write_bytes

        mode = path.stat().st_mode & 0o777
        atomic_write_bytes(path, output)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    except Exception as exc:
        report.ok = False
        report.changed = False
        report.error = str(exc)
        report.reason = "write_error"
    return report


def rotate_pdf_tree(root: Path, **kwargs: Any) -> Dict[str, Any]:
    """Batch-Normalisierung für bereits vorhandene PDFs unterhalb von root."""
    root = Path(root)
    result: Dict[str, Any] = {
        "root": str(root),
        "scanned": 0,
        "changed": 0,
        "rotated_pages": 0,
        "unchanged": 0,
        "errors": 0,
        "files": [],
    }
    if not root.exists() or not root.is_dir():
        result["errors"] = 1
        result["error"] = "root_missing"
        return result

    for pdf in sorted(root.rglob("*.pdf")):
        if not pdf.is_file() or pdf.is_symlink():
            continue
        result["scanned"] += 1
        report = normalize_pdf_path(pdf, **kwargs)
        if not report.ok:
            result["errors"] += 1
        elif report.changed:
            result["changed"] += 1
            result["rotated_pages"] += report.rotated_pages
        else:
            result["unchanged"] += 1
        result["files"].append({"path": str(pdf), **report.as_dict()})
    return result
