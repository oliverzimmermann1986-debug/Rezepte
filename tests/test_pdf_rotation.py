from pathlib import Path

import pymupdf

from app.core.pdf_rotation import (
    _parse_tesseract_osd,
    normalize_pdf_bytes,
    normalize_pdf_path,
    rotate_pdf_tree,
)


def _pdf_bytes(*, text_rotation: int = 0, page_rotation: int = 0) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    # Genug Text für die robuste Mehrheitsentscheidung.
    text = "Zutaten Zubereitung Butter Mehl Zucker Eier Rezept Anleitung " * 3
    point = {
        0: (80, 200),
        90: (160, 700),
        180: (540, 300),
        270: (440, 80),
    }[text_rotation]
    page.insert_text(point, text, fontsize=12, rotate=text_rotation)
    page.set_rotation(page_rotation)
    data = doc.tobytes()
    doc.close()
    return data


def _page_rotation(data: bytes) -> int:
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        return int(doc[0].rotation)
    finally:
        doc.close()


def test_rotated_text_layer_is_corrected_without_rasterizing():
    source = _pdf_bytes(text_rotation=90)
    output, report = normalize_pdf_bytes(source, use_tesseract_osd=False)

    assert report.ok is True
    assert report.changed is True
    assert report.rotated_pages == 1
    assert report.decisions[0].method == "text-layer"
    assert report.decisions[0].new_rotation == 90
    assert _page_rotation(output) == 90

    # Text-Layer bleibt nach der Korrektur vorhanden.
    doc = pymupdf.open(stream=output, filetype="pdf")
    try:
        assert "Zutaten" in doc[0].get_text()
    finally:
        doc.close()


def test_wrong_existing_page_rotation_is_reset():
    source = _pdf_bytes(text_rotation=0, page_rotation=90)
    output, report = normalize_pdf_bytes(source, use_tesseract_osd=False)

    assert report.changed is True
    assert report.decisions[0].old_rotation == 90
    assert report.decisions[0].new_rotation == 0
    assert _page_rotation(output) == 0


def test_upright_pdf_is_left_byte_identical():
    source = _pdf_bytes(text_rotation=0, page_rotation=0)
    output, report = normalize_pdf_bytes(source, use_tesseract_osd=False)

    assert report.ok is True
    assert report.changed is False
    assert output == source


def test_invalid_pdf_is_non_fatal():
    source = b"not a pdf"
    output, report = normalize_pdf_bytes(source)

    assert output == source
    assert report.ok is False
    assert report.reason == "invalid_pdf"


def test_tesseract_osd_parser():
    parsed = _parse_tesseract_osd(
        "Page number: 0\nOrientation in degrees: 270\nRotate: 90\n"
        "Orientation confidence: 12.34\nScript: Latin\n"
    )
    assert parsed == (90, 12.34)
    assert _parse_tesseract_osd("Rotate: 45") is None


def test_path_and_tree_rotation_are_atomic(tmp_path: Path):
    root = tmp_path / "recipes"
    root.mkdir()
    pdf = root / "sideways.pdf"
    pdf.write_bytes(_pdf_bytes(text_rotation=90))

    report = normalize_pdf_path(pdf, use_tesseract_osd=False)
    assert report.changed is True
    assert _page_rotation(pdf.read_bytes()) == 90

    # Zweiter Lauf ist idempotent.
    result = rotate_pdf_tree(root, use_tesseract_osd=False)
    assert result["scanned"] == 1
    assert result["changed"] == 0
    assert result["unchanged"] == 1
