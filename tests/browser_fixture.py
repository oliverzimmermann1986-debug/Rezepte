"""Synthetic browser API: all network requests stay inside a fresh test context.

No application imports, real recipe data, credentials or external services.
"""
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
ORIGIN = "http://rezepte.test"
NAMES = [
    ("Zitronen-Ricotta-Pasta", "zitronen-ricotta-pasta"),
    ("Ofengemüse mit Feta", "ofengemuese-feta"),
    ("Kürbissuppe", "kuerbissuppe"),
]


class WebFixture:
    def __init__(self):
        self.recipes = [{
            "id": index, "name": name, "type": "Hauptgericht", "category": "Demonstration",
            "description": "Demonstrationsrezept für die lokale GUI-Prüfung.",
            "is_favorite": False, "rating": 0, "verified": True, "needs_manual_care": False,
            "manual_care_reasons": [], "ingredients_status": "ok", "ingredients_count": 1,
            "thumb_filename": slug + ".png", "video_filename": None, "servings": 2,
            "url": f"https://example.invalid/{slug}", "tags": [], "source_added_at": 1791000000,
            "visibility": "global", "in_library": False,
            "ingredients": [{"id": 100 + index, "name": "Tomaten", "amount": 300, "unit": "g",
                             "canonical_name": "tomate", "raw": "300 g Tomaten"}],
            "steps": [{"id": 200 + index, "step_number": 1, "instruction": "Zutaten vorbereiten.",
                       "timer_seconds": None}, {"id": 300 + index, "step_number": 2,
                       "instruction": "Bei mittlerer Hitze garen.", "timer_seconds": 300}],
            "nutrition": {}, "image_backups": [],
        } for index, (name, slug) in enumerate(NAMES, 1)]
        self.cart = []
        self.planned = []
        self.requests = []
        self.defer_details = False
        self.pending_details = []
        self.role = "admin"
        self.account_invitations = []
        self.request_bodies = []

    def shopping_item(self):
        return {"id": 1, "name": "Tomaten", "canonical_name": "tomate", "amount": 300,
                "unit": "g", "checked": False, "category": "Obst & Gemüse", "source_recipe_ids": [2]}

    def meal_plan(self, query):
        start = date.today() - timedelta(days=date.today().weekday())
        if query.get("week_start"):
            start = date.fromisoformat(query["week_start"][0])
        days = [{"date": (start + timedelta(days=i)).isoformat(), "label": label,
                 "short_label": label[:2], "day_number": (start + timedelta(days=i)).day,
                 "is_today": start + timedelta(days=i) == date.today(),
                 "items": [item for item in self.planned if item["planned_for"] == (start + timedelta(days=i)).isoformat()]}
                for i, label in enumerate(["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"])]
        return {"week_start": start.isoformat(), "week_end": (start + timedelta(days=6)).isoformat(),
                "previous_week": (start - timedelta(days=7)).isoformat(), "next_week": (start + timedelta(days=7)).isoformat(),
                "is_current_week": True, "days": days, "shopping_preview": [],
                "summary": {"planned_meals": len(self.planned), "planned_days": len(self.planned), "shopping_items": len(self.planned)}}

    def handle(self, route):
        request = route.request
        parsed = urlsplit(request.url)
        path = parsed.path
        query = parse_qs(parsed.query)
        method = request.method
        self.requests.append((method, path, query))
        if method in ("POST", "PUT", "PATCH") and request.headers.get("content-type", "").startswith("application/json"):
            self.request_bodies.append((path, request.post_data_json))
        if path in ("/", "/account"):
            html = (STATIC / "index.html").read_text(encoding="utf-8").replace("{VERSION}", "browser-test")
            if path == "/account":
                html = html.replace('<body>', '<body data-initial-page="account">', 1)
            return route.fulfill(body=html, content_type="text/html; charset=utf-8")
        if path.startswith("/static/"):
            file = (STATIC / path.removeprefix("/static/")).resolve()
            if file.is_relative_to(STATIC.resolve()) and file.is_file():
                return route.fulfill(path=str(file))
        if path == "/api/session":
            return route.fulfill(json={"username": "Gast" if self.role == "guest" else "GUI-Demo", "role": self.role,
                                       "is_admin": self.role == "admin", "full_access": self.role == "admin"})
        if path == "/api/account":
            return route.fulfill(json={"is_guest": self.role == "guest", "is_owner": self.role != "guest",
                                       "members": [] if self.role == "guest" else [{"id": 1, "username": "GUI-Demo", "disabled": False}],
                                       "invitations": self.account_invitations, "max_members": 2,
                                       "data_scope": "global_read_only" if self.role == "guest" else "household"})
        if path == "/api/account/imports":
            return route.fulfill(json={"items": []})
        if path == "/api/account/profile":
            return route.fulfill(json={"id": 1, "username": "GUI-Demo", "role": self.role,
                                       "created_at": 1791000000, "last_login_at": 1791000000,
                                       "password_enabled": True})
        if path == "/api/account/sessions":
            return route.fulfill(json={"sessions": [{"id": "current", "created_at": 1791000000,
                "last_seen_at": 1791000000, "expires_at": 4102444800, "client_label": "Testbrowser", "is_current": True}]})
        if path == "/api/account/identities":
            return route.fulfill(json={"identities": [], "providers": []})
        if path == "/api/account/invitations" and method == "POST":
            invitation = {"id": 1, "created_at": 1791000000, "expires_at": 4102444800, "revoked_at": None, "accepted_at": None}
            self.account_invitations = [invitation]
            return route.fulfill(json={"ok": True, **invitation, "token": "demo-single-use-invitation-token",
                                       "invite_path": "/register?invite=demo-single-use-invitation-token"})
        if path == "/api/account/invitations/1" and method == "DELETE":
            self.account_invitations[0]["revoked_at"] = 1791000001
            return route.fulfill(json={"ok": True})
        if path == "/api/system/info":
            return route.fulfill(json={"version": "browser-test", "capabilities": ["weekly-meal-plan", "recipe-pdf-export"]})
        if path == "/api/recipes/facets":
            return route.fulfill(json={"types": ["Hauptgericht"], "categories": ["Demonstration"], "tags": [],
                                       "ingredients": [{"canonical_name": "tomate", "display_name": "Tomaten", "n": 3}]})
        if path == "/api/recipes":
            items = deepcopy(self.recipes)
            for item in items:
                item["can_edit"] = self.role == "admin" or (self.role == "user" and item["visibility"] == "private")
            if query.get("library") == ["mine"]:
                items = [item for item in items if item["in_library"] or item["visibility"] == "private"]
            if query.get("library") == ["global"]:
                items = [item for item in items if item["visibility"] == "global"]
            if query.get("search"):
                items = [item for item in items if query["search"][0].casefold() in item["name"].casefold()]
            if query.get("favorite_only") == ["true"]:
                items = [item for item in items if item["is_favorite"]]
            if query.get("exclude_ingredient") == ["tomate"]:
                items = []
            total = len(items)
            offset = int(query.get("offset", [0])[0])
            limit = int(query.get("limit", [60])[0])
            return route.fulfill(json={"items": items[offset:offset + limit], "total": total,
                                       "extraction_running": False, "sync": {"running": False}})
        if path == "/api/pending/import-url" and method == "POST":
            body = request.post_data_json
            recipe = next((item for item in self.recipes if item["url"] == body["url"]), None)
            if recipe:
                recipe["in_library"] = True
                return route.fulfill(json={"ok": True, "status": "linked_global", "recipe_id": recipe["id"],
                                           "visibility": "global", "in_library": True, "downloaded": False,
                                           "message": "Globales Rezept in deinem Haushalt gespeichert. Kein erneuter Download."})
            return route.fulfill(json={"ok": True, "status": "pending", "message": "Privater Import wartet auf Prüfung."})
        recipe_path = re.fullmatch(r"/api/recipes/(\d+)(?:/(thumb|favorite|save))?", path)
        if recipe_path:
            index = int(recipe_path[1]) - 1
            recipe = self.recipes[index]
            recipe["can_edit"] = self.role == "admin" or (self.role == "user" and recipe["visibility"] == "private")
            if recipe_path[2] == "thumb":
                return route.fulfill(path=str(ROOT / "review-demo" / "assets" / (NAMES[index][1] + ".png")))
            if recipe_path[2] == "favorite" and method == "POST":
                recipe["is_favorite"] = not recipe["is_favorite"]
                recipe["in_library"] = True
                return route.fulfill(json={"ok": True, "is_favorite": recipe["is_favorite"]})
            if recipe_path[2] == "save" and method in ("POST", "DELETE"):
                recipe["in_library"] = method == "POST"
                return route.fulfill(json={"ok": True, "in_library": recipe["in_library"]})
            if self.defer_details:
                self.pending_details.append(route)
                return
            return route.fulfill(json=recipe)
        if path == "/api/einkauf/status":
            return route.fulfill(json={"configured": False})
        if path == "/api/cart":
            return route.fulfill(json={"items": self.cart})
        if path.startswith("/api/cart/cook/") and method == "POST":
            self.cart = [self.shopping_item()]
            return route.fulfill(json={"ok": True, "added": 1, "merged": 0})
        if path == "/api/meal-plan":
            return route.fulfill(json=self.meal_plan(query))
        if path == "/api/meal-plan/items" and method == "POST":
            body = request.post_data_json
            recipe = self.recipes[int(body["recipe_id"]) - 1]
            self.planned.append({**body, "id": 1, "recipe_name": recipe["name"],
                                 "recipe_servings": recipe["servings"], "thumb_url": f'/api/recipes/{recipe["id"]}/thumb',
                                 "ingredients_count": recipe["ingredients_count"]})
            return route.fulfill(json={"ok": True})
        if path == "/api/meal-plan/cart" and method == "POST":
            self.cart = [self.shopping_item()]
            return route.fulfill(json={"ok": True, "added": 1, "merged": 0, "planned_meals": 1})
        if path == "/manifest.json":
            return route.fulfill(path=str(STATIC / "manifest.json"))
        return route.fulfill(status=404, json={"detail": "Unconfigured browser fixture endpoint"})
