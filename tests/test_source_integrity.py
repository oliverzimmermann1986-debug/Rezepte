import pytest

from tests.conftest import _create_recipe

from app.recipes.source_integrity import (
    normalize_source_text,
    source_change_impact,
    source_diff,
    source_fingerprint,
)


def _complete_recipe(db, recipe_id: int) -> None:
    db.recipe_set_servings(recipe_id, 2)
    db.recipe_set_extraction_result(
        recipe_id,
        "ok",
        [{
            "name": "Tomaten",
            "canonical_name": "tomate",
            "amount": 4,
            "unit": "Stück",
            "raw": "4 Tomaten",
        }],
    )
    db.recipe_steps_set(recipe_id, [{"instruction": "Tomaten schneiden und servieren."}])


def _record_source_snapshot(
    db,
    recipe_id: int,
    source_url: str,
    text: str,
    *,
    state: str,
    force_baseline: bool = False,
) -> int:
    return db.recipe_source_snapshot_create(
        recipe_id,
        source_url=source_url,
        content_sha256=source_fingerprint(text),
        content_text=text,
        state=state,
        force_baseline=force_baseline,
    )


def _changed_source_recipe(test_db, *, suffix: str):
    source_url = f"https://example.test/source-cas-{suffix}"
    recipe = _create_recipe(
        test_db,
        name=f"CAS {suffix}",
        folder_path=f"/missing/source-cas-{suffix}",
        url=source_url,
        description="1 l Wasser",
    )
    baseline_text = "Zutaten:\n- 1 l Wasser"
    changed_text = "Zutaten:\n- 1 l Wasser\n- 2 Eier"
    baseline_id = _record_source_snapshot(
        test_db,
        recipe["id"],
        source_url,
        baseline_text,
        state="baseline",
        force_baseline=True,
    )
    latest_id = _record_source_snapshot(
        test_db,
        recipe["id"],
        source_url,
        changed_text,
        state="changed",
    )
    return {
        "recipe": recipe,
        "source_url": source_url,
        "baseline_id": baseline_id,
        "baseline_sha256": source_fingerprint(baseline_text),
        "latest_id": latest_id,
        "latest_sha256": source_fingerprint(changed_text),
    }


def test_source_text_normalization_and_diff_are_stable():
    assert normalize_source_text("  Pasta\r\n\r\n  200   g Mehl  \n") == "Pasta\n\n200 g Mehl"
    comparison = source_diff("Pasta\n200 g Mehl", "Pasta\n250 g Mehl")
    assert comparison["changed"] is True
    assert comparison["added_lines"] == 1
    assert comparison["removed_lines"] == 1
    assert any(line == "+250 g Mehl" for line in comparison["lines"])


@pytest.mark.parametrize(
    ("ingredient_line", "expected_allergens", "forbidden_allergens"),
    [
        ("200 ml Mandelmilch", {"nuts"}, {"lactose"}),
        ("100 g Mandelmehl", {"nuts"}, {"gluten"}),
        ("2 EL Mandelmus", {"nuts"}, set()),
        ("50 g Haselnusscreme", {"nuts"}, set()),
        ("3 EL Cashewmus", {"nuts"}, set()),
        ("nussfreie Dekoration", set(), {"nuts"}),
    ],
)
def test_source_impact_matches_nut_compounds_without_naive_substrings(
    ingredient_line, expected_allergens, forbidden_allergens
):
    comparison = source_diff(
        "Zutaten:\n- 1 l Wasser",
        f"Zutaten:\n- 1 l Wasser\n- {ingredient_line}",
    )

    impact = source_change_impact(comparison, [])
    detected = {
        item["allergen"]
        for item in impact["possible_allergen_changes"]
        if item["direction"] == "added"
    }

    assert expected_allergens <= detected
    assert not (forbidden_allergens & detected)
    assert impact["automatic_safety_claim"] is False


def test_truncated_impact_requires_full_source_texts():
    baseline_text = "\n".join(f"Alt {index}" for index in range(100))
    current_text = "\n".join(f"Neu {index}" for index in range(100))
    comparison = source_diff(baseline_text, current_text)

    assert comparison["truncated"] is True
    with pytest.raises(ValueError, match="vollständigen Quelltexte"):
        source_change_impact(comparison, [])


@pytest.mark.parametrize(
    ("ingredient_line", "expected_allergen"),
    [
        ("200 ml Milch", "lactose"),
        ("100 g Mehl", "gluten"),
        ("100 g Weizen", "gluten"),
        ("2 Eier", "egg"),
    ],
)
def test_source_impact_keeps_true_allergen_positive_cases(
    ingredient_line, expected_allergen
):
    comparison = source_diff(
        "Zutaten:\n- 1 l Wasser",
        f"Zutaten:\n- 1 l Wasser\n- {ingredient_line}",
    )

    impact = source_change_impact(comparison, [])

    assert any(
        item["allergen"] == expected_allergen and item["direction"] == "added"
        for item in impact["possible_allergen_changes"]
    )
    assert impact["review_required"] is True


def test_source_impact_uses_full_diff_while_public_preview_stays_truncated(
    client, test_db
):
    recipe = _create_recipe(
        test_db,
        name="Langer Quellvergleich",
        folder_path="/missing/long-source-diff",
        url="https://example.test/long-source-diff",
    )
    baseline_text = "\n".join(f"{index} g Zutat {index}" for index in range(120))
    current_text = "\n".join(
        [f"{index + 1} g Neue Zutat {index}" for index in range(120)]
        + ["2 Eier"]
    )
    _record_source_snapshot(
        test_db,
        recipe["id"],
        recipe["url"],
        baseline_text,
        state="baseline",
        force_baseline=True,
    )
    _record_source_snapshot(
        test_db,
        recipe["id"],
        recipe["url"],
        current_text,
        state="changed",
    )

    response = client.get(f"/api/recipes/{recipe['id']}/source-integrity")

    assert response.status_code == 200
    body = response.json()
    assert body["diff"]["truncated"] is True
    assert len(body["diff"]["lines"]) == 80
    assert body["impact"]["review_required"] is True
    assert any(
        item["allergen"] == "egg" and item["direction"] == "added"
        for item in body["impact"]["possible_allergen_changes"]
    )


def test_source_watcher_detects_change_without_overwriting_recipe(
    client, test_db, monkeypatch
):
    import app.routes.api_recipes as api_recipes

    recipe = _create_recipe(
        test_db,
        name="Quellenpasta",
        folder_path="/missing/quellenpasta",
        url="https://example.test/quellenpasta",
        description="Quellenpasta\n\nZutaten:\n- 200 g Mehl",
    )
    _complete_recipe(test_db, recipe["id"])
    payload = {
        "canonical_url": recipe["url"],
        "description_text": recipe["description"],
        "description_source": "recipe-json-ld",
        "page_title": "Quellenpasta",
    }
    monkeypatch.setattr(
        api_recipes,
        "extract_recipe_web_metadata",
        lambda _url, include_thumbnail=False: dict(payload),
    )

    initial = client.get(f"/api/recipes/{recipe['id']}/source-integrity")
    assert initial.status_code == 200
    assert initial.json()["status"] == "unchecked"

    checked = client.post(f"/api/recipes/{recipe['id']}/source-integrity/check")
    assert checked.status_code == 200, checked.text
    assert checked.json()["status"] == "current"
    assert checked.json()["baseline"]["is_baseline"] is True
    assert checked.json()["automatic_overwrite"] is False
    current_snapshot = checked.json()["latest"]
    assert client.post(
        f"/api/recipes/{recipe['id']}/source-integrity/accept",
        json={"expected_snapshot_id": current_snapshot["id"]},
    ).status_code == 409

    payload["description_text"] = "Quellenpasta\n\nZutaten:\n- 250 g Mehl\n- 2 Eier"
    changed = client.post(f"/api/recipes/{recipe['id']}/source-integrity/check")
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["status"] == "changed"
    assert body["diff"]["changed"] is True
    assert body["diff"]["added_lines"] == 2
    assert body["impact"]["review_required"] is True
    assert body["impact"]["automatic_safety_claim"] is False
    assert any(
        change["label"] == "Ei" and change["direction"] == "added"
        for change in body["impact"]["possible_allergen_changes"]
    )
    assert any(
        change["text"] == "2 Eier"
        for change in body["impact"]["ingredient_changes"]
    )
    assert any(issue["id"] == "source-changed" for issue in body["quality"]["issues"])
    assert test_db.recipe_get(recipe["id"])["description"] == recipe["description"]

    accepted = client.post(
        f"/api/recipes/{recipe['id']}/source-integrity/accept",
        json={
            "expected_snapshot_id": body["latest"]["id"],
            "expected_content_sha256": body["latest"]["content_sha256"],
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "current"
    assert accepted.json()["baseline"]["accepted_by"] == "unknown"
    assert test_db.recipe_get(recipe["id"])["description"] == recipe["description"]


def test_source_accept_requires_snapshot_cas_token(client, test_db):
    source = _changed_source_recipe(test_db, suffix="missing-token")

    response = client.post(
        f"/api/recipes/{source['recipe']['id']}/source-integrity/accept",
        json={},
    )

    assert response.status_code == 422
    state = test_db.recipe_source_snapshot_state(
        source["recipe"]["id"], source["source_url"]
    )
    assert state["baseline"]["id"] == source["baseline_id"]
    assert state["latest"]["id"] == source["latest_id"]


def test_source_accept_rejects_stale_or_inconsistent_cas_token(client, test_db):
    source = _changed_source_recipe(test_db, suffix="stale-token")
    endpoint = f"/api/recipes/{source['recipe']['id']}/source-integrity/accept"

    stale_id = client.post(
        endpoint,
        json={"expected_snapshot_id": source["baseline_id"]},
    )
    inconsistent_pair = client.post(
        endpoint,
        json={
            "expected_snapshot_id": source["latest_id"],
            "expected_content_sha256": source["baseline_sha256"],
        },
    )

    assert stale_id.status_code == 409
    assert "zwischenzeitlich aktualisiert" in stale_id.json()["detail"]
    assert inconsistent_pair.status_code == 409
    assert "zwischenzeitlich aktualisiert" in inconsistent_pair.json()["detail"]
    state = test_db.recipe_source_snapshot_state(
        source["recipe"]["id"], source["source_url"]
    )
    assert state["baseline"]["id"] == source["baseline_id"]

    accepted = client.post(
        endpoint,
        json={"expected_content_sha256": source["latest_sha256"]},
    )
    assert accepted.status_code == 200
    assert accepted.json()["baseline"]["id"] == source["latest_id"]


def test_source_accept_rejects_snapshot_created_after_review(
    client, test_db, monkeypatch
):
    source = _changed_source_recipe(test_db, suffix="race")
    original_accept = test_db.recipe_source_snapshot_accept_latest
    raced_text = "Zutaten:\n- 1 l Wasser\n- 2 Eier"
    raced_snapshot_id = None

    def insert_racing_snapshot(recipe_id, source_url, *, accepted_by, **expected):
        nonlocal raced_snapshot_id
        raced_snapshot_id = _record_source_snapshot(
            test_db,
            recipe_id,
            source_url,
            raced_text,
            state="changed",
        )
        return original_accept(
            recipe_id,
            source_url,
            accepted_by=accepted_by,
            **expected,
        )

    monkeypatch.setattr(
        test_db,
        "recipe_source_snapshot_accept_latest",
        insert_racing_snapshot,
    )

    response = client.post(
        f"/api/recipes/{source['recipe']['id']}/source-integrity/accept",
        json={
            "expected_snapshot_id": source["latest_id"],
            "expected_content_sha256": source["latest_sha256"],
        },
    )

    assert response.status_code == 409
    assert "zwischenzeitlich aktualisiert" in response.json()["detail"]
    state = test_db.recipe_source_snapshot_state(
        source["recipe"]["id"], source["source_url"]
    )
    assert state["baseline"]["id"] == source["baseline_id"]
    assert state["latest"]["id"] == raced_snapshot_id


def test_source_accept_rejects_ambiguous_fingerprint_after_equivalent_snapshot(
    client, test_db
):
    source = _changed_source_recipe(test_db, suffix="fingerprint-race")
    repeated_snapshot_id = _record_source_snapshot(
        test_db,
        source["recipe"]["id"],
        source["source_url"],
        "Zutaten:\n- 1 l Wasser\n- 2 Eier",
        state="changed",
    )

    response = client.post(
        f"/api/recipes/{source['recipe']['id']}/source-integrity/accept",
        json={"expected_content_sha256": source["latest_sha256"]},
    )

    assert response.status_code == 409
    assert "nicht mehr eindeutig" in response.json()["detail"]
    state = test_db.recipe_source_snapshot_state(
        source["recipe"]["id"], source["source_url"]
    )
    assert state["baseline"]["id"] == source["baseline_id"]
    assert state["latest"]["id"] == repeated_snapshot_id


def test_source_accept_requires_admin_for_authenticated_user(
    client, test_db, monkeypatch
):
    from app import auth
    from app.auth import require_admin
    from app.main import app

    source = _changed_source_recipe(test_db, suffix="rbac-user")
    test_db.user_create("source-reader", auth.hash_password("password"), role="user")
    monkeypatch.setattr(auth, "auth_disabled", lambda: False)
    monkeypatch.setattr(auth, "request_user", lambda _request: "source-reader")
    app.dependency_overrides.pop(require_admin, None)
    try:
        response = client.post(
            f"/api/recipes/{source['recipe']['id']}/source-integrity/accept",
            json={"expected_snapshot_id": source["latest_id"]},
            headers={"Origin": "http://testserver"},
        )
    finally:
        app.dependency_overrides[require_admin] = lambda: {
            "username": "test-admin",
            "role": "admin",
            "full_access": True,
        }

    assert response.status_code == 403
    assert test_db.recipe_source_snapshot_state(
        source["recipe"]["id"], source["source_url"]
    )["baseline"]["id"] == source["baseline_id"]


def test_source_accept_requires_authentication(client, test_db, monkeypatch):
    from app import auth
    from app.auth import require_admin, require_auth
    from app.main import app

    source = _changed_source_recipe(test_db, suffix="rbac-guest")
    monkeypatch.setattr(auth, "auth_disabled", lambda: False)
    app.dependency_overrides.pop(require_auth, None)
    app.dependency_overrides.pop(require_admin, None)
    try:
        response = client.post(
            f"/api/recipes/{source['recipe']['id']}/source-integrity/accept",
            json={"expected_snapshot_id": source["latest_id"]},
        )
    finally:
        app.dependency_overrides[require_auth] = lambda: None
        app.dependency_overrides[require_admin] = lambda: {
            "username": "test-admin",
            "role": "admin",
            "full_access": True,
        }

    assert response.status_code == 401
    assert test_db.recipe_source_snapshot_state(
        source["recipe"]["id"], source["source_url"]
    )["baseline"]["id"] == source["baseline_id"]


def test_source_accept_rejects_signed_guest_session(client, test_db, monkeypatch):
    from app import auth
    from app.auth import require_admin, require_auth
    from app.main import app

    source = _changed_source_recipe(test_db, suffix="rbac-signed-guest")
    guest_token = auth.create_guest_session()
    monkeypatch.setattr(auth, "auth_disabled", lambda: False)
    app.dependency_overrides.pop(require_auth, None)
    app.dependency_overrides.pop(require_admin, None)
    client.cookies.set(auth.SESSION_COOKIE, guest_token)
    try:
        response = client.post(
            f"/api/recipes/{source['recipe']['id']}/source-integrity/accept",
            json={"expected_snapshot_id": source["latest_id"]},
            headers={"Origin": "http://testserver"},
        )
    finally:
        client.cookies.delete(auth.SESSION_COOKIE)
        app.dependency_overrides[require_auth] = lambda: None
        app.dependency_overrides[require_admin] = lambda: {
            "username": "test-admin",
            "role": "admin",
            "full_access": True,
        }

    assert response.status_code == 403
    assert "schreibgeschützt" in response.json()["detail"]
    assert test_db.recipe_source_snapshot_state(
        source["recipe"]["id"], source["source_url"]
    )["baseline"]["id"] == source["baseline_id"]


def test_source_watcher_records_unavailable_source(client, test_db, monkeypatch):
    import app.routes.api_recipes as api_recipes

    recipe = _create_recipe(
        test_db,
        name="Offlinequelle",
        folder_path="/missing/offlinequelle",
        url="https://example.test/offlinequelle",
        description="Gespeicherter Inhalt",
    )

    def fail(_url, *, include_thumbnail=False):
        raise ValueError("HTTP 503")

    monkeypatch.setattr(api_recipes, "extract_recipe_web_metadata", fail)
    response = client.post(f"/api/recipes/{recipe['id']}/source-integrity/check")

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["latest"]["error"] == "HTTP 503"
    assert test_db.recipe_get(recipe["id"])["description"] == "Gespeicherter Inhalt"


def test_recipe_quality_report_flags_duplicates_and_incomplete_sections(client, test_db):
    recipe = _create_recipe(
        test_db,
        name="Prüffall",
        folder_path="/missing/prueffall",
        url="attachment://scan/prueffall",
    )
    with test_db.conn() as connection:
        connection.executemany(
            "INSERT INTO recipe_ingredients "
            "(recipe_id, name, canonical_name, amount, unit, sort_order) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (recipe["id"], "Tomate", "tomate", None, None, 0),
                (recipe["id"], "Tomaten", "tomate", 2, "Stück", 1),
            ],
        )

    response = client.get(f"/api/recipes/{recipe['id']}/source-integrity")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "local"
    issue_ids = {issue["id"] for issue in body["quality"]["issues"]}
    assert {"steps-missing", "servings-missing", "ingredients-duplicate", "amounts-missing"} <= issue_ids
    assert "source-missing" not in issue_ids


def test_complete_but_unverified_recipe_has_explicit_non_green_quality(client, test_db):
    recipe = _create_recipe(
        test_db,
        name="Vollständig, ungeprüft",
        folder_path="/missing/complete-unverified",
        url="attachment://scan/complete-unverified",
    )
    _complete_recipe(test_db, recipe["id"])

    response = client.get(f"/api/recipes/{recipe['id']}/source-integrity")

    assert response.status_code == 200
    quality = response.json()["quality"]
    assert quality["status"] == "review"
    assert quality["score"] < 85
    assert any(
        issue["id"] == "manual-verification-required"
        and issue["severity"] == "warning"
        for issue in quality["issues"]
    )

    test_db.recipe_set_verified(recipe["id"], True, "quality-reviewer")
    verified = client.get(f"/api/recipes/{recipe['id']}/source-integrity").json()
    assert verified["verified"] is True
    assert verified["quality"]["status"] == "verified"
    assert verified["quality"]["score"] == 100
    assert verified["quality"]["issues"] == []
