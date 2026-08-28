from io import BytesIO

from PIL import Image

from app.recipes import image_generation


def _jpeg(color: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (80, 60), color).save(output, format="JPEG")
    return output.getvalue()


class _Config:
    def __init__(self, recipe_root, data_root):
        self.values = {
            "paths": {"recipe_dir": str(recipe_root), "data_dir": str(data_root)},
            "ai": {
                "openai": {"api_key": "test-key"},
                "image_generation": {
                    "enabled": True,
                    "model": "gpt-image-2",
                    "size": "1536x1024",
                    "quality": "medium",
                    "output_format": "jpeg",
                },
            },
        }

    def get(self, *keys, default=None):
        current = self.values
        for key in keys:
            if not isinstance(current, dict) or key not in current:
                return default
            current = current[key]
        return current


def test_generated_image_is_backed_up_and_restorable(test_db, tmp_path, monkeypatch):
    recipe_root = tmp_path / "recipes"
    folder = recipe_root / "Hauptgericht" / "Suppe" / "Kartoffelsuppe"
    folder.mkdir(parents=True)
    original = _jpeg("red")
    active = folder / "thumb-generated.jpg"
    active.write_bytes(original)
    recipe_id = test_db.recipe_upsert(
        url="https://koch.example/suppe",
        name="Kartoffelsuppe",
        type="Hauptgericht",
        category="Suppe",
        folder_path=str(folder),
        description="Kartoffelsuppe mit Kartoffeln und Brühe",
        thumb_filename=active.name,
        video_filename=None,
        source_added_at=None,
    )
    test_db.recipe_apply_extraction_result(
        recipe_id,
        ingredients=[{"name": "Kartoffeln", "canonical_name": "kartoffel", "unit": "g"}],
        steps=[], servings=4, auto_tags=[],
    )

    class FakeAnalyzer:
        def generate_recipe_image(self, _prompt, **_kwargs):
            return _jpeg("green")

    config = _Config(recipe_root, tmp_path / "data")
    monkeypatch.setattr(image_generation, "get_config", lambda: config)
    monkeypatch.setattr(image_generation, "build_analyzer", lambda _cfg: FakeAnalyzer())

    result = image_generation.generate_recipe_image(recipe_id, batch_id="batch-test-123")
    assert result["backup_id"] is not None
    assert active.read_bytes() != original
    backup = test_db.recipe_image_backup_get(result["backup_id"])
    backup_file = image_generation.image_backup_root() / backup["backup_path"]
    assert backup_file.read_bytes() == original

    restored = image_generation.restore_recipe_image_backup(result["backup_id"])
    assert restored["ok"] is True
    assert active.read_bytes() == original
    assert test_db.recipe_get(recipe_id)["image_generation_status"] == "restored"


def test_backfill_never_generates_when_backup_phase_fails(test_db, monkeypatch):
    run_id = test_db.maintenance_start("recipe_image_backfill", "test")
    monkeypatch.setattr(image_generation, "ensure_image_generation_configured", lambda: {})
    monkeypatch.setattr(test_db, "recipes_for_image_backfill", lambda: [{"id": 1}, {"id": 2}])
    calls = []

    def fail_second(recipe, _batch_id):
        if recipe["id"] == 2:
            raise RuntimeError("backup failed")
        return 1

    monkeypatch.setattr(image_generation, "backup_recipe_image", fail_second)
    monkeypatch.setattr(
        image_generation,
        "generate_recipe_image",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    result = image_generation.run_image_backfill(
        {"run_id": run_id, "batch_id": "batch-test-456"}
    )
    assert result["phase"] == "backup_failed"
    assert result["generated"] == 0
    assert calls == []
