"""Exports reflect edited selections, authentic visibility and current rule evidence."""
from io import BytesIO
import time

import pdfplumber
import pytest

from app.db import RECIPE_VARIANT_PENDING_STATUS
from app.recipes import meal_suggestion_pdf
from tests.test_household_features import household_api, recipe


def payload(ids, **criteria):
    return {"criteria": {"count": len(ids), "vegetarian_count": 0, "week_start": "2026-10-12", **criteria},
            "items": [{"recipe_id": rid, "date": f"2026-10-{12 + index}", "servings": index + 2}
                      for index, rid in enumerate(ids)]}


def pdf_text(data):
    with pdfplumber.open(BytesIO(data)) as document:
        return "\n".join(page.extract_text() or "" for page in document.pages)


def test_pdf_exports_edited_dates_portions_literal_names_and_private_headers(household_api):
    client, db, users, login = household_api
    rid = recipe(db, 'Äpfel & Öl <b>literal</b>', users["anna"][1])
    with db.conn() as c:
        c.execute("UPDATE recipes SET description='Gesamtzeit: 20 Minuten' WHERE id=?", (rid,))
    db.recipe_tags_set(rid, ["vegetarisch"])
    login("anna")
    body = payload([rid], vegetarian_count=1, category_quotas=[{"category": "pasta", "count": 1}],
                   weekday_rules=[{"weekday": 5, "category": "pasta", "max_minutes": 30}])
    body["items"][0].update(date="2026-10-16", servings=6)
    response = client.post("/api/discovery/meal-plan/pdf", json=body)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="wochenvorschlaege-2026-10-12.pdf"'
    assert response.headers["cache-control"] == "private, no-store"
    assert "Authorization" in response.headers["vary"]
    assert response.headers["x-content-type-options"] == "nosniff"
    text = pdf_text(response.content)
    assert 'Äpfel & Öl <b>literal</b>' in text
    assert "Freitag, 16.10." in text and "6 Portionen" in text
    assert "20 Min. Gesamtzeit" in text and "vegetarisch markiert" in text
    assert "Freitag: Nudelgerichte, höchstens 30 Min." in text
    assert "erfüllt die überprüfbaren Vorgaben" in text


def test_export_never_reruns_solver_or_writes_plan(household_api, monkeypatch):
    from app.recipes import discovery
    client, db, _, login = household_api
    chosen, other = recipe(db, "Ausgewählt"), recipe(db, "Nicht ausgewählt")
    login("anna")
    monkeypatch.setattr(discovery, "meal_plan_suggestions", lambda *_a, **_kw: pytest.fail("Do not regenerate suggestions"))
    response = client.post("/api/discovery/meal-plan/pdf", json=payload([chosen]))
    assert response.status_code == 200
    text = pdf_text(response.content)
    assert "Ausgewählt" in text and "Nicht ausgewählt" not in text
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM meal_plan_entries").fetchone()[0] == 0


def test_pdf_uses_the_same_dish_types_for_quota_and_weekday_evidence(household_api):
    client, db, _, login = household_api
    salad = recipe(db, "Sommergericht")
    soup = recipe(db, "Wintergericht")
    with db.conn() as c:
        c.execute("UPDATE recipes SET category='Salat' WHERE id=?", (salad,))
        c.execute("UPDATE recipes SET type='Suppe' WHERE id=?", (soup,))
    login("anna")
    body = payload([salad, soup], category_quotas=[{"category": "salad", "count": 1}, {"category": "soup", "count": 1}],
                   weekday_rules=[{"weekday": 1, "category": "salad"}, {"weekday": 2, "category": "soup"}])
    response = client.post("/api/discovery/meal-plan/pdf", json=body)
    assert response.status_code == 200, response.text
    text = pdf_text(response.content)
    assert "Salate: exakt 1 gewünscht, 1 ausgewählt" in text
    assert "Suppen: exakt 1 gewünscht, 1 ausgewählt" in text
    assert "erfüllt die überprüfbaren Vorgaben" in text
    assert "erfüllt die Tagesregel nicht" not in text


def test_pdf_refuses_foreign_deleted_pending_and_unauthenticated_snapshots(household_api):
    client, db, users, login = household_api
    own = recipe(db, "Eigenes", users["anna"][1])
    hidden = recipe(db, "BertsGeheimnis", users["bert"][1])
    deleted = recipe(db, "Gelöscht")
    pending = recipe(db, "Unfertig")
    with db.conn() as c:
        c.execute("UPDATE recipes SET deleted_at=? WHERE id=?", (time.time(), deleted))
        c.execute("UPDATE recipes SET ingredients_status=? WHERE id=?", (RECIPE_VARIANT_PENDING_STATUS, pending))
    login("anna")
    for rid in (hidden, deleted, pending):
        response = client.post("/api/discovery/meal-plan/pdf", json=payload([own, rid]))
        assert response.status_code == 404
        assert "BertsGeheimnis" not in response.text
    login("guest")
    assert client.post("/api/discovery/meal-plan/pdf", json=payload([own])).status_code == 403
    client.headers.pop("Authorization")
    assert client.post("/api/discovery/meal-plan/pdf", json=payload([own])).status_code == 401


@pytest.mark.parametrize("mutate", [
    lambda body: body["items"][0].update(date="2026-10-12T00:00:00Z"),
    lambda body: body["items"][0].update(date="2026-10-19"),
    lambda body: body["items"][0].update(date="2026-02-30"),
    lambda body: body["items"][0].update(servings=0),
    lambda body: body["items"][0].update(servings=25),
    lambda body: body["items"][0].update(recipe_id=True),
    lambda body: body["items"].append(body["items"][0]),
    lambda body: body.update(items=[]),
    lambda body: body.update(items=body["items"] * 8),
    lambda body: body["criteria"].update(week_start="2026-10-13"),
    lambda body: body["criteria"].update(week_start=None),
    lambda body: body.update(warnings=["All constraints met"]),
    lambda body: body["items"][0].update(name="Invented title"),
])
def test_malformed_or_untrusted_snapshots_are_rejected(household_api, mutate):
    client, db, _, login = household_api
    login("anna")
    body = payload([recipe(db)])
    mutate(body)
    assert client.post("/api/discovery/meal-plan/pdf", json=body).status_code == 422


def test_pdf_rechecks_quota_overflow_missing_days_exclusions_and_time(household_api):
    client, db, _, login = household_api
    rid = recipe(db, "PastaChampignons")
    db.recipe_set_extraction_result(rid, status="ok", ingredients=[{"name": "Penne"}, {"name": "Champignons"}])
    login("anna")
    body = payload([rid], count=2, vegetarian_count=1, max_minutes=20,
                   category_quotas=[{"category": "pasta", "count": 0}], excluded_ingredients=["Pilze"],
                   weekday_rules=[{"weekday": 5, "category": "potato"}])
    response = client.post("/api/discovery/meal-plan/pdf", json=body)
    assert response.status_code == 200
    text = pdf_text(response.content)
    for expected in ("Aktuell 1 von 2", "Aktuell 0 von mindestens 1", "aktuell 1, gewünscht exakt 0",
                     "Zeitgrenze ist nicht nachgewiesen", "enthält eine ausgeschlossene Zutat",
                     "Freitag: Für die Tagesregel ist noch kein Gericht"):
        assert expected in text
    assert "erfüllt die überprüfbaren Vorgaben" not in text


def test_current_database_changes_and_each_fixed_day_dish_are_rechecked(household_api):
    client, db, users, login = household_api
    pasta, rice = recipe(db, "Pasta"), recipe(db, "Reis")
    db.recipe_set_extraction_result(rice, status="ok", ingredients=[{"name": "Reis"}])
    with db.conn() as c:
        c.execute("INSERT INTO meal_plan_entries(account_id,planned_for,recipe_id,planned_servings,sort_order,created_at,updated_at) "
                  "VALUES(?,?,?,?,?,?,?)", (users["anna"][1], "2026-10-10", pasta, 2, 0, time.time(), time.time()))
    login("anna")
    body = payload([pasta, rice], avoid_recent_days=7, weekday_rules=[{"weekday": 1, "category": "pasta"}])
    body["items"][1]["date"] = "2026-10-12"
    response = client.post("/api/discovery/meal-plan/pdf", json=body)
    assert response.status_code == 200
    text = pdf_text(response.content)
    assert "wurde im gewählten Rückblick bereits geplant" in text
    assert "erfüllt die Tagesregel nicht" in text
    # Free dates can contain several dishes. No automatic solver-slot rule is invented.
    body["criteria"]["weekday_rules"] = []
    response = client.post("/api/discovery/meal-plan/pdf", json=body)
    assert response.status_code == 200
    assert "erfüllt die Tagesregel nicht" not in pdf_text(response.content)
    body["criteria"]["weekday_rules"] = [{"weekday": 1, "category": "pasta"}]
    body["items"][1]["date"] = "2026-10-13"
    response = client.post("/api/discovery/meal-plan/pdf", json=body)
    assert "erfüllt die Tagesregel nicht" not in pdf_text(response.content)
    db.recipe_set_extraction_result(pasta, status="ok", ingredients=[{"name": "Karotte"}])
    response = client.post("/api/discovery/meal-plan/pdf", json=body)
    assert "erfüllt die Tagesregel nicht" in pdf_text(response.content)


def test_export_bounds_and_render_slots_release_after_failure(household_api, monkeypatch):
    from app.routes.api_meal_plan_pdf import _pdf_slots
    client, db, _, login = household_api
    login("anna")
    body = payload([recipe(db)])
    monkeypatch.setattr(meal_suggestion_pdf, "MAX_TEXT_CHARACTERS", 1)
    assert client.post("/api/discovery/meal-plan/pdf", json=body).status_code == 422
    monkeypatch.setattr(meal_suggestion_pdf, "MAX_TEXT_CHARACTERS", 40_000)
    _pdf_slots.acquire()
    _pdf_slots.acquire()
    try:
        response = client.post("/api/discovery/meal-plan/pdf", json=body)
        assert response.status_code == 429 and response.headers["retry-after"] == "5"
    finally:
        _pdf_slots.release()
        _pdf_slots.release()
    assert client.post("/api/discovery/meal-plan/pdf", json=body).status_code == 200
    monkeypatch.setattr(meal_suggestion_pdf, "MAX_PDF_BYTES", 1)
    assert client.post("/api/discovery/meal-plan/pdf", json=body).status_code == 422


def test_long_literal_recipe_title_splits_without_loss(household_api):
    client, db, _, login = household_api
    rid = recipe(db)
    name = "\n".join(f"Zeile {index:03d}: Äpfel & Öl <kein-link>" for index in range(90))
    with db.conn() as c:
        c.execute("UPDATE recipes SET name=? WHERE id=?", (name, rid))
    login("anna")
    response = client.post("/api/discovery/meal-plan/pdf", json=payload([rid]))
    assert response.status_code == 200, response.text
    text = pdf_text(response.content)
    assert "Zeile 000" in text and "Zeile 089" in text and "<kein-link>" in text
