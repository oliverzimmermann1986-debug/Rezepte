from tests.conftest import _create_recipe


def test_similarity_search_returns_partial_warning_at_deadline(monkeypatch):
    from app.recipes import audit

    recipes = [
        {"id": index, "name": f"Kartoffelsuppe Variante {index}"}
        for index in range(80)
    ]
    ticks = iter([0.0, 10.0])
    monkeypatch.setattr(audit.time, "monotonic", lambda: next(ticks, 10.0))

    clusters = audit.find_similar_names(recipes, timeout_seconds=0.1)

    warning = next(cluster[0]["_warning"] for cluster in clusters if cluster[0].get("_warning"))
    assert "beendet" in warning
    assert "Exakte Dubletten sind vollständig" in warning


def test_audit_cache_is_reused_and_invalidated_by_database_writes(client, test_db):
    from app.routes import api_audit

    with api_audit._audit_cache_lock:
        api_audit._audit_cache.clear()
    first = client.get("/api/audit", params={"refresh": "true"})
    second = client.get("/api/audit")

    assert first.status_code == 200
    assert first.json()["audit_meta"]["cached"] is False
    assert second.json()["audit_meta"]["cached"] is True

    _create_recipe(test_db, name="Neu", folder_path="/tmp/audit-cache-new")
    changed = client.get("/api/audit")

    assert changed.status_code == 200
    assert changed.json()["audit_meta"]["cached"] is False
    assert changed.json()["total_recipes"] == 1


def test_deleted_recipes_are_not_reported_as_active_audit_gaps(client, test_db):
    recipe = _create_recipe(
        test_db,
        name="Gelöscht",
        folder_path="/tmp/audit-deleted",
        description=None,
    )
    test_db.recipe_soft_delete(recipe["id"])

    response = client.get("/api/audit", params={"refresh": "true"})

    assert response.status_code == 200
    body = response.json()
    assert body["total_recipes"] == 0
    assert body["summary"]["no_description_count"] == 0
