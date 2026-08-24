from pathlib import Path

import pytest

from tools.merge_exact_duplicate import merge_exact_duplicate


def _recipe(db, *, name: str, folder: Path, url: str) -> int:
    folder.mkdir(parents=True)
    recipe_id = db.recipe_upsert(
        url=url,
        name=name,
        type="Hauptgericht",
        category="Pasta",
        folder_path=str(folder),
        description="Identische lange Beschreibung fuer ein sicheres Rezeptpaar.",
        thumb_filename=None,
        video_filename=None,
        source_added_at=1.0,
    )
    db.recipe_apply_extraction_result(
        recipe_id,
        ingredients=[{
            "name": "Tomate",
            "canonical_name": "tomate",
            "amount": 2,
            "unit": "Stueck",
            "raw": "2 Tomaten",
        }],
        steps=[{"instruction": "Alles kochen."}],
        servings=2,
        auto_tags=[],
        status="ok",
    )
    return recipe_id


def test_exact_duplicate_tool_is_read_only_by_default(test_db, tmp_path):
    keep_id = _recipe(
        test_db,
        name="Gleiche Pasta",
        folder=tmp_path / "keep",
        url="https://example.com/keep",
    )
    remove_id = _recipe(
        test_db,
        name="gleiche pasta",
        folder=tmp_path / "remove",
        url="https://example.com/remove",
    )
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / f"{keep_id}.mp4").write_bytes(b"same-video")
    (archive / f"{remove_id}.mp4").write_bytes(b"same-video")

    result = merge_exact_duplicate(
        test_db,
        keep_id=keep_id,
        remove_id=remove_id,
        archive_dir=archive,
        require_media_match=True,
    )

    assert result["mode"] == "dry-run"
    assert result["media_equal"] is True
    assert test_db.recipe_get(remove_id)["deleted_at"] is None


def test_exact_duplicate_tool_rejects_different_media(test_db, tmp_path):
    keep_id = _recipe(
        test_db,
        name="Gleiche Suppe",
        folder=tmp_path / "keep",
        url="https://example.com/keep-2",
    )
    remove_id = _recipe(
        test_db,
        name="gleiche suppe",
        folder=tmp_path / "remove",
        url="https://example.com/remove-2",
    )
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / f"{keep_id}.mp4").write_bytes(b"first")
    (archive / f"{remove_id}.mp4").write_bytes(b"second")

    with pytest.raises(ValueError, match="nicht bitidentisch"):
        merge_exact_duplicate(
            test_db,
            keep_id=keep_id,
            remove_id=remove_id,
            archive_dir=archive,
            require_media_match=True,
        )
