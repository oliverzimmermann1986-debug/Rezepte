from urllib.parse import urlsplit

from itsdangerous import URLSafeTimedSerializer

from tests.conftest import _create_recipe


def test_public_recipe_share_can_be_listed_and_revoked(client, test_db, monkeypatch):
    import app.routes.sharing as sharing

    serializer = URLSafeTimedSerializer("s" * 32, salt=sharing.SHARE_SALT)
    monkeypatch.setattr(sharing, "_serializer", lambda: serializer)
    recipe = _create_recipe(
        test_db,
        name="Freigabe",
        folder_path="/missing/share",
        description="Ein öffentlich freigegebenes Testrezept.",
    )
    test_db.recipe_set_extraction_result(
        recipe["id"],
        "ok",
        [{"name": "Mehl", "canonical_name": "mehl", "amount": 200, "unit": "g"}],
    )
    test_db.recipe_steps_set(recipe["id"], [{"instruction": "Verrühren."}])

    created = client.post(
        f"/api/recipes/{recipe['id']}/share",
        json={"expires_days": 7},
    )

    assert created.status_code == 200
    payload = created.json()
    parsed = urlsplit(payload["url"])
    assert parsed.path == "/share"
    token = parsed.fragment
    token_data = serializer.loads(token)
    assert token_data["sid"] == payload["share_id"]
    assert "by" not in token_data
    resolved = client.post(
        "/share/resolve", json={"token": token}, headers={"Origin": "http://testserver"},
    )
    assert resolved.status_code == 200
    public_response = client.get("/share/view")
    assert public_response.status_code == 200
    assert public_response.headers["cache-control"] == "private, no-store"

    listed = client.get(f"/api/recipes/{recipe['id']}/shares")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["active"] == 1

    revoked = client.delete(
        f"/api/recipes/{recipe['id']}/shares/{payload['share_id']}"
    )
    assert revoked.status_code == 200
    assert client.get("/share/view").status_code == 410
    assert client.get(f"/api/recipes/{recipe['id']}/shares").json()["items"][0]["active"] == 0


def test_invalid_public_url_does_not_create_orphan_share(client, test_db, monkeypatch):
    import app.routes.sharing as sharing

    class _InvalidPublicConfig:
        @staticmethod
        def get(*keys, default=None):
            if keys == ("web", "public_url"):
                return "http://unsicher.example/path"
            return default

    monkeypatch.setattr(sharing, "get_config", lambda: _InvalidPublicConfig())
    recipe = _create_recipe(
        test_db,
        name="Keine verwaiste Freigabe",
        folder_path="/missing/orphan-share",
    )

    response = client.post(
        f"/api/recipes/{recipe['id']}/share",
        json={"expires_days": 7},
    )

    assert response.status_code == 503
    assert test_db.recipe_share_links_list(recipe["id"]) == []


def test_print_metadata_separates_category_and_servings():
    import app.routes.sharing as sharing

    body = sharing._render_print_html({
        "name": "Metadaten",
        "type": "Hauptgericht",
        "category": "Pasta",
        "servings": 4,
    })

    assert '<span class="meta">Hauptgericht · Pasta</span>' in body
    assert '<span class="meta">4 Portionen</span>' in body
