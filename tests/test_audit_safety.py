import json
from pathlib import Path

from tests.conftest import _create_recipe


class _RecipeRootConfig:
    def __init__(self, root):
        self.root = root

    def get(self, *keys, default=None):
        if keys == ("paths", "recipe_dir"):
            return str(self.root)
        return default


def test_delete_by_path_only_removes_exact_registered_conflict_recipe_folder(
    client, test_db, tmp_path, monkeypatch
):
    from app.routes import api_audit

    root = tmp_path / "recipes"
    conflict = root / "Hauptgericht" / "Pasta" / "Doppelte_Pasta"
    conflict.mkdir(parents=True)
    (conflict / "info.json").write_text("{}", encoding="utf-8")
    test_db.sync_error_record(str(conflict.resolve()), "duplicate", "Testkonflikt")
    monkeypatch.setattr(api_audit, "get_config", lambda: _RecipeRootConfig(root))

    response = client.post(
        "/api/audit/recipe/delete-by-path",
        json={"folder_path": str(conflict)},
    )

    assert response.status_code == 200
    assert not conflict.exists()
    assert test_db.sync_errors_list() == []


def test_delete_by_path_rejects_root_category_and_unregistered_folder(
    client, test_db, tmp_path, monkeypatch
):
    from app.routes import api_audit

    root = tmp_path / "recipes"
    category = root / "Hauptgericht" / "Pasta"
    unregistered = category / "Nicht_registriert"
    unregistered.mkdir(parents=True)
    monkeypatch.setattr(api_audit, "get_config", lambda: _RecipeRootConfig(root))

    assert client.post(
        "/api/audit/recipe/delete-by-path", json={"folder_path": str(root)}
    ).status_code == 400
    assert client.post(
        "/api/audit/recipe/delete-by-path", json={"folder_path": str(category)}
    ).status_code == 400
    assert client.post(
        "/api/audit/recipe/delete-by-path", json={"folder_path": str(unregistered)}
    ).status_code == 409
    assert unregistered.is_dir()


def test_category_finding_cannot_escape_recipe_root(
    client, test_db, tmp_path, monkeypatch
):
    from app.routes import api_audit

    root = tmp_path / "recipes"
    folder = root / "Hauptgericht" / "Pasta" / "Sicheres_Rezept"
    folder.mkdir(parents=True)
    recipe = _create_recipe(
        test_db,
        name="Sicheres Rezept",
        folder_path=str(folder.resolve()),
        type="Hauptgericht",
        category="Pasta",
    )
    test_db.audit_ai_finding_set(
        recipe["id"],
        "category_mismatch",
        "Hauptgericht/Pasta",
        "../Ausbruch",
        "adversarial test",
    )
    finding = test_db.audit_ai_findings_list("category_mismatch")[0]
    monkeypatch.setattr(api_audit, "get_config", lambda: _RecipeRootConfig(root))

    response = client.post(f"/api/audit/finding/{finding['id']}/apply")

    assert response.status_code == 400
    assert folder.is_dir()
    assert not (tmp_path / "Ausbruch").exists()


def test_name_finding_uses_versioned_consistent_metadata_update(
    client, test_db, tmp_path, monkeypatch
):
    from app.routes import api_audit
    from app.recipes import manage

    root = tmp_path / "recipes"
    folder = root / "Hauptgericht" / "Pasta" / "Alter_Name"
    folder.mkdir(parents=True)
    (folder / "info.json").write_text(
        json.dumps({"name": "Alter Name", "type": "Hauptgericht", "category": "Pasta"}),
        encoding="utf-8",
    )
    recipe = _create_recipe(
        test_db,
        name="Alter Name",
        folder_path=str(folder.resolve()),
        type="Hauptgericht",
        category="Pasta",
        description="Beschreibung",
    )
    test_db.audit_ai_finding_set(
        recipe["id"],
        "name_mismatch",
        "Alter Name",
        "Neuer Name",
        "Test",
    )
    finding = test_db.audit_ai_findings_list("name_mismatch")[0]
    monkeypatch.setattr(api_audit, "get_config", lambda: _RecipeRootConfig(root))
    monkeypatch.setattr(manage, "get_config", lambda: _RecipeRootConfig(root))

    response = client.post(f"/api/audit/finding/{finding['id']}/apply")

    assert response.status_code == 200, response.text
    target = root / "Hauptgericht" / "Pasta" / "Neuer_Name"
    assert target.is_dir()
    assert not folder.exists()
    updated = test_db.recipe_get(recipe["id"])
    assert updated["name"] == "Neuer Name"
    assert Path(updated["folder_path"]) == target.resolve()
    assert json.loads((target / "info.json").read_text(encoding="utf-8"))["name"] == "Neuer Name"
    versions = test_db.recipe_versions_list(recipe_id=recipe["id"])
    assert len(versions) == 1
    assert versions[0]["source"] == "audit"
