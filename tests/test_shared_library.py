"""Public recipe content, independent membership and private personal state."""
import time

from app.db import Database, RECIPE_VARIANT_PENDING_STATUS
from app.tenant_db import HouseholdDatabase
from app.tenancy import HouseholdScope
from tests.test_household_features import household_api  # noqa: F401
from tests.test_meal_suggestion_pdf import payload


def imported(db, account, name="Familienpasta", *, status="pending"):
    own = HouseholdDatabase(db, HouseholdScope(account))
    rid = own.recipe_upsert(url=f"https://shared.example/{name}", name=name, type="Hauptgericht", category="Pasta",
                            folder_path=f"/shared-tests/{name}", description="Gesamtzeit: 20 Minuten",
                            thumb_filename=None, video_filename=None, source_added_at=time.time(),
                            initial_ingredients_status=status)
    if status != RECIPE_VARIANT_PENDING_STATUS:
        db.recipe_set_extraction_result(rid, status="ok", ingredients=[{"name": "Nudeln", "amount": 200, "unit": "g"}])
        db.recipe_steps_set(rid, [{"instruction": "Kochen."}])
    return rid


def test_completed_import_is_global_but_membership_and_editing_are_scoped(household_api):
    client, db, users, login = household_api
    rid = imported(db, users["anna"][1])
    login("anna")
    own = client.get(f"/api/recipes/{rid}").json()
    assert own["visibility"] == "global" and own["in_library"] and own["can_edit"]
    login("bert")
    other = client.get(f"/api/recipes/{rid}").json()
    assert other["visibility"] == "global" and not other["in_library"] and not other["can_edit"]
    assert client.get('/api/recipes?library=global').json()["total"] == 1
    assert client.get('/api/recipes?library=mine').json()["total"] == 0
    assert client.put(f"/api/recipes/{rid}/steps", json={"steps": [{"instruction": "Fremd ändern"}]}).status_code == 403
    login("guest")
    assert client.get(f"/api/recipes/{rid}").status_code == 200


def test_household_flag_is_independent_of_favorites_and_ratings(household_api):
    client, db, users, login = household_api
    rid = imported(db, users["anna"][1])
    login("anna")
    client.post(f"/api/recipes/{rid}/favorite")
    client.post(f"/api/recipes/{rid}/rating?value=5")
    assert client.delete(f"/api/recipes/{rid}/save").status_code == 200
    removed = client.get(f"/api/recipes/{rid}").json()
    assert not removed["in_library"] and removed["is_favorite"] and removed["rating"] == 5
    assert client.get('/api/recipes?library=mine').json()["total"] == 0
    # Changing a personal rating must not silently restore the flag.
    client.post(f"/api/recipes/{rid}/rating?value=4")
    assert not client.get(f"/api/recipes/{rid}").json()["in_library"]
    assert client.post(f"/api/recipes/{rid}/save").status_code == 200
    assert client.get(f"/api/recipes/{rid}").json()["in_library"]
    login("bert")
    assert client.get(f"/api/recipes/{rid}").json()["rating"] == 0


def test_suggestions_default_global_override_archive_filter_and_only_household_has_no_fallback(household_api):
    client, db, users, login = household_api
    rid = imported(db, users["anna"][1])
    login("bert")
    normal = client.post('/api/discovery/meal-plan?library=mine', json={"count": 1})
    assert [item["recipe_id"] for item in normal.json()["items"]] == [rid]
    limited = client.post('/api/discovery/meal-plan?library=global', json={"count": 1, "household_only": True})
    assert limited.json()["items"] == [] and not limited.json()["constraints_satisfied"]
    client.post(f'/api/recipes/{rid}/save')
    limited = client.post('/api/discovery/meal-plan', json={"count": 1, "household_only": True})
    assert [item["recipe_id"] for item in limited.json()["items"]] == [rid]
    client.delete(f'/api/recipes/{rid}/save')
    assert client.post('/api/discovery/meal-plan', json={"count": 1, "household_only": True}).json()["items"] == []


def test_pdf_rechecks_current_membership_for_household_only(household_api):
    client, db, users, login = household_api
    rid = imported(db, users["anna"][1])
    login("bert")
    assert client.post('/api/discovery/meal-plan/pdf', json=payload([rid])).status_code == 200
    body = payload([rid], household_only=True)
    assert client.post('/api/discovery/meal-plan/pdf', json=body).status_code == 409
    client.post(f'/api/recipes/{rid}/save')
    assert client.post('/api/discovery/meal-plan/pdf', json=body).status_code == 200
    client.delete(f'/api/recipes/{rid}/save')
    assert client.post('/api/discovery/meal-plan/pdf', json=body).status_code == 409


def test_global_recipe_keeps_its_name_in_another_households_shopping_provenance(household_api):
    from app.recipes.cart_logic import add_recipe_to_cart, cart_for_display
    _, db, users, _ = household_api
    rid = imported(db, users['anna'][1])
    other = HouseholdDatabase(db, HouseholdScope(users['bert'][1]))
    add_recipe_to_cart(other, rid)
    assert cart_for_display(other)[0]['source_contributions'][0]['recipe_name'] == 'Familienpasta'


def test_pending_variants_are_not_published_until_finalized(household_api):
    client, db, users, login = household_api
    rid = imported(db, users['anna'][1], status=RECIPE_VARIANT_PENDING_STATUS)
    login('bert')
    assert client.get(f'/api/recipes/{rid}').status_code == 404
    db.recipe_variant_finalize(rid, status='ok')
    assert client.get(f'/api/recipes/{rid}').status_code == 200


def test_upgrade_preserves_author_ids_and_personal_values_and_does_not_publish_drafts(household_api):
    _, db, users, _ = household_api
    account = users['anna'][1]
    rid = imported(db, account)
    draft = imported(db, account, 'Entwurf', status=RECIPE_VARIANT_PENDING_STATUS)
    own = HouseholdDatabase(db, HouseholdScope(account))
    own.save_recipe(rid, field='rating', value=5)
    with db.conn() as c:
        c.execute('DELETE FROM schema_migrations WHERE version=273')
        c.execute('DROP INDEX idx_household_saved_recipes')
        c.execute('ALTER TABLE recipes DROP COLUMN is_global')
        c.execute('ALTER TABLE account_recipe_state DROP COLUMN is_saved')
    upgraded = Database(db.path)
    assert upgraded.recipe_get(rid)['is_global'] == 1
    assert upgraded.recipe_get(rid)['owner_account_id'] == account
    assert upgraded.recipe_get(draft, include_pending=True)['is_global'] == 0
    assert HouseholdDatabase(upgraded, HouseholdScope(account)).recipe_get(rid)['rating'] == 5
    assert HouseholdDatabase(upgraded, HouseholdScope(account)).recipe_get(rid)['in_library']
    assert Database(db.path).recipe_count() == upgraded.recipe_count()
