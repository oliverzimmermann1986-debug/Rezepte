"""API-Endpoint-Tests via FastAPI TestClient.
Auth ist überschrieben (siehe conftest.py), get_db() returnt Test-DB.
"""
from tests.conftest import _create_recipe


# ─── Liste + Filter ─────────────────────────────────────────────────────────

def test_list_empty(client, test_db):
    r = client.get("/api/recipes")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []


def test_list_returns_inserted_recipes(client, test_db):
    _create_recipe(test_db, name="Spargel", folder_path="/tmp/sp")
    _create_recipe(test_db, name="Tomate", folder_path="/tmp/to")
    r = client.get("/api/recipes")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    names = sorted(it["name"] for it in body["items"])
    assert names == ["Spargel", "Tomate"]


def test_list_filter_by_type(client, test_db):
    _create_recipe(test_db, name="Suppe", folder_path="/tmp/su", type="Vorspeise")
    _create_recipe(test_db, name="Pasta", folder_path="/tmp/pa", type="Hauptgericht")
    r = client.get("/api/recipes?type=Vorspeise")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Suppe"


def test_list_search_in_name(client, test_db):
    _create_recipe(test_db, name="Spargelsalat", folder_path="/tmp/x1")
    _create_recipe(test_db, name="Tomatenpasta", folder_path="/tmp/x2")
    r = client.get("/api/recipes?search=Spargel")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Spargelsalat"


def test_list_search_in_description(client, test_db):
    _create_recipe(test_db, name="Geheim", folder_path="/tmp/g",
                   description="mit viel Knoblauch und Olivenöl")
    r = client.get("/api/recipes?search=Knoblauch")
    body = r.json()
    assert body["total"] == 1


def test_list_pagination(client, test_db):
    for i in range(10):
        _create_recipe(test_db, name=f"R{i:02d}", folder_path=f"/tmp/r{i}")
    r = client.get("/api/recipes?limit=3&offset=0")
    body = r.json()
    assert body["total"] == 10
    assert len(body["items"]) == 3

    r2 = client.get("/api/recipes?limit=3&offset=3")
    body2 = r2.json()
    assert len(body2["items"]) == 3
    # Keine Überlappung
    ids_1 = {it["id"] for it in body["items"]}
    ids_2 = {it["id"] for it in body2["items"]}
    assert ids_1.isdisjoint(ids_2)


# ─── Detail-Endpoint ────────────────────────────────────────────────────────

def test_get_detail(client, test_db):
    rec = _create_recipe(test_db, name="Detail-Test", folder_path="/tmp/d")
    r = client.get(f"/api/recipes/{rec['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Detail-Test"
    assert body["folder_path"] == "/tmp/d"


def test_get_detail_404(client, test_db):
    r = client.get("/api/recipes/999999")
    assert r.status_code == 404


# ─── Favorite + Rating ──────────────────────────────────────────────────────

def test_toggle_favorite_flow(client, test_db):
    rec = _create_recipe(test_db, name="X", folder_path="/tmp/x")
    rid = rec["id"]

    # Initial: nicht Favorit
    r = client.get(f"/api/recipes/{rid}")
    assert r.json().get("is_favorite") in (0, False, None)

    # Toggle ON
    r = client.post(f"/api/recipes/{rid}/favorite")
    assert r.status_code == 200
    assert r.json()["is_favorite"] is True

    # Toggle OFF
    r = client.post(f"/api/recipes/{rid}/favorite")
    assert r.json()["is_favorite"] is False


def test_set_rating(client, test_db):
    rec = _create_recipe(test_db, name="X", folder_path="/tmp/x")
    rid = rec["id"]

    r = client.post(f"/api/recipes/{rid}/rating?value=4")
    assert r.status_code == 200
    assert r.json()["rating"] == 4

    # Liste enthält das rating
    r = client.get("/api/recipes")
    assert r.json()["items"][0]["rating"] == 4


def test_set_rating_out_of_range(client, test_db):
    rec = _create_recipe(test_db, name="X", folder_path="/tmp/x")
    r = client.post(f"/api/recipes/{rec['id']}/rating?value=99")
    assert r.status_code == 422  # FastAPI Validation


def test_min_rating_filter_via_api(client, test_db):
    r1 = _create_recipe(test_db, name="Eins", folder_path="/tmp/1")
    r2 = _create_recipe(test_db, name="Fuenf", folder_path="/tmp/5")
    client.post(f"/api/recipes/{r1['id']}/rating?value=1")
    client.post(f"/api/recipes/{r2['id']}/rating?value=5")

    r = client.get("/api/recipes?min_rating=3")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Fuenf"


def test_favorite_only_filter_via_api(client, test_db):
    r1 = _create_recipe(test_db, name="A", folder_path="/tmp/a")
    _create_recipe(test_db, name="B", folder_path="/tmp/b")
    client.post(f"/api/recipes/{r1['id']}/favorite")

    r = client.get("/api/recipes?favorite_only=true")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "A"


# ─── Soft-Delete + Trash ────────────────────────────────────────────────────

def test_soft_delete_default(client, test_db):
    rec = _create_recipe(test_db, name="X", folder_path="/tmp/x")
    rid = rec["id"]

    # DELETE ohne hard=true → Soft
    r = client.delete(f"/api/recipes/{rid}")
    assert r.status_code == 200
    assert r.json()["soft"] is True

    # Aus Liste verschwunden
    assert client.get("/api/recipes").json()["total"] == 0
    # Im Trash sichtbar
    trash = client.get("/api/recipes/trash/list").json()
    assert trash["total"] == 1
    assert trash["items"][0]["name"] == "X"
    assert trash["items"][0]["days_until_purge"] > 25  # frisch gelöscht


def test_restore_from_trash(client, test_db):
    rec = _create_recipe(test_db, name="R", folder_path="/tmp/r")
    rid = rec["id"]
    client.delete(f"/api/recipes/{rid}")

    r = client.post(f"/api/recipes/{rid}/restore")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # Wieder in der Liste
    assert client.get("/api/recipes").json()["total"] == 1
    assert client.get("/api/recipes/trash/list").json()["total"] == 0


def test_hard_delete(client, test_db):
    rec = _create_recipe(test_db, name="X", folder_path="/tmp/x")
    rid = rec["id"]

    r = client.delete(f"/api/recipes/{rid}?hard=true")
    assert r.status_code == 200
    assert r.json()["soft"] is False

    # Auch nicht im Trash mehr
    assert client.get("/api/recipes/trash/list").json()["total"] == 0
    # Detail-404
    assert client.get(f"/api/recipes/{rid}").status_code == 404


def test_empty_trash(client, test_db):
    for i in range(3):
        rec = _create_recipe(test_db, name=f"R{i}", folder_path=f"/tmp/r{i}")
        client.delete(f"/api/recipes/{rec['id']}")
    assert client.get("/api/recipes/trash/list").json()["total"] == 3

    r = client.delete("/api/recipes/trash/empty?delete_files=false")
    assert r.json()["purged"] == 3
    assert client.get("/api/recipes/trash/list").json()["total"] == 0


# ─── Filter-Kombinationen ───────────────────────────────────────────────────

def test_combined_filter_search_and_favorite(client, test_db):
    r1 = _create_recipe(test_db, name="Spargel-Favorit", folder_path="/tmp/sf")
    _create_recipe(test_db, name="Spargel-Normal", folder_path="/tmp/sn")
    _create_recipe(test_db, name="Pasta", folder_path="/tmp/pa")
    client.post(f"/api/recipes/{r1['id']}/favorite")

    # Search + favorite_only → nur Spargel-Favorit
    r = client.get("/api/recipes?search=Spargel&favorite_only=true")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Spargel-Favorit"
