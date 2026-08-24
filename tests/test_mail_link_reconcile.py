from tools.mail_link_reconcile import _apply_matches, _path_key, match_source_to_recipes


def _recipe(recipe_id, name, folder, description=""):
    return {
        "id": recipe_id,
        "name": name,
        "folder_path": folder,
        "description": description,
    }


def test_path_key_ignores_changed_recipe_root():
    assert _path_key("/mnt/alt/rezepte/Hauptgericht/Pasta/Lasagne") == _path_key(
        "/opt/scrapper/files/rezepte/Hauptgericht/Pasta/Lasagne"
    )


def test_exact_relative_path_is_safe_match():
    source = {
        "url": "https://www.tiktok.com/@koch/video/1",
        "history_target": "/mnt/alt/rezepte/Hauptgericht/Pasta/Lasagne",
    }
    recipes = [
        _recipe(1, "Lasagne", "/opt/scrapper/files/rezepte/Hauptgericht/Pasta/Lasagne"),
        _recipe(2, "Andere", "/opt/scrapper/files/rezepte/Hauptgericht/Pasta/Andere"),
    ]

    result = match_source_to_recipes(source, recipes, include_media=False)

    assert result["safe"] is True
    assert result["candidate_id"] == 1
    assert result["evidence"][0]["method"] == "path-exact"


def test_name_only_match_stays_manual():
    source = {
        "url": "https://www.tiktok.com/@koch/video/2",
        "history_name": "Lasagne",
    }
    result = match_source_to_recipes(
        source,
        [_recipe(1, "Lasagne", "/recipes/Lasagne")],
        include_media=False,
    )

    assert result["candidate_id"] == 1
    assert result["safe"] is False


def test_exact_description_is_safe_and_conflicting_evidence_is_not():
    description = (
        "Zwiebeln klein schneiden und mit Knoblauch anbraten. "
        "Tomaten und Nudeln hinzufügen und zehn Minuten köcheln lassen."
    )
    recipes = [
        _recipe(1, "Pasta", "/recipes/Pasta", description),
        _recipe(2, "Suppe", "/recipes/Suppe", "Kartoffeln weich kochen und pürieren."),
    ]
    source = {
        "url": "https://www.tiktok.com/@koch/video/3",
        "pending_description": description,
    }

    result = match_source_to_recipes(source, recipes, include_media=False)

    assert result["safe"] is True
    assert result["candidate_id"] == 1
    assert result["evidence"][0]["method"] == "description-exact"

    source["history_target"] = "/recipes/Suppe"
    conflict = match_source_to_recipes(source, recipes, include_media=False)
    assert conflict["safe"] is False
    assert conflict["conflict"] is True


def test_apply_without_safe_matches_does_not_create_backup(monkeypatch):
    def fail_if_called(_db):
        raise AssertionError("dry selection must not create a backup")

    monkeypatch.setattr("tools.mail_link_reconcile._backup_database", fail_if_called)
    backup, results = _apply_matches(object(), [{"safe": False}])

    assert backup is None
    assert results == []
