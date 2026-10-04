"""Browser workflows with local scripts and a synthetic, isolated household."""
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.browser_fixture import ORIGIN, WebFixture

playwright = pytest.importorskip("playwright.sync_api")
expect = playwright.expect


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def web(browser, request):
    width = getattr(request, "param", 390)
    context = browser.new_context(viewport={"width": width, "height": 1000 if width > 768 else 844}, reduced_motion="reduce")
    fixture = WebFixture()
    context.route("**/*", fixture.handle)
    page = context.new_page()
    page.set_default_timeout(5000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield page, fixture, width, errors
    context.close()


def capture(page, name):
    target = os.environ.get("REZEPTE_BROWSER_ARTIFACT_DIR")
    if target:
        directory = Path(target)
        directory.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(directory / (name + ".png")),
                        full_page=not page.locator(".recipe-detail-backdrop").is_visible())


@pytest.mark.parametrize("web", [1440], indirect=True)
def test_upload_reloads_hidden_cover_and_library_images(web):
    page, fixture, _, errors = web
    failed = False

    def thumbnail(route):
        nonlocal failed
        if not urlsplit(route.request.url).query and not failed:
            failed = True
            route.fulfill(status=404)
        else:
            fixture.handle(route)

    def upload(route):
        fixture.recipes[1]["thumb_filename"] = "thumb.jpg"
        route.fulfill(json={"ok": True, "thumbnail": "thumb.jpg", "size_bytes": 1024})

    page.route(ORIGIN + "/api/recipes/2/thumb*", thumbnail)
    page.route(ORIGIN + "/api/recipes/2/upload-thumbnail", upload)
    open_library(page)
    page.get_by_role("button", name="Rezept Ofengemüse mit Feta öffnen").click()
    image = page.locator(".recipe-detail-video > img")
    expect(image).to_be_hidden()
    page.locator(".recipe-actions summary").click()
    page.locator('.recipe-actions input[type="file"]').set_input_files({
        "name": "cover.png", "mimeType": "image/png",
        "buffer": (Path(__file__).resolve().parents[1] / "review-demo/assets/ofengemuese-feta.png").read_bytes(),
    })
    expect(image).to_have_attribute("src", re.compile(r"/api/recipes/2/thumb\?v="))
    expect(image).to_be_visible()
    assert image.evaluate("image => image.naturalWidth > 0")
    page.get_by_role("button", name="Rezeptdetails schließen").click()
    card_image = page.locator('.recipe-card').filter(has_text="Ofengemüse mit Feta").locator('.recipe-thumb > img')
    expect(card_image).to_have_attribute("src", re.compile(r"/api/recipes/2/thumb\?w=400&v="))
    expect(card_image).to_have_attribute("srcset", re.compile(r"w=800&v="))
    assert errors == []


def test_image_backup_restore_refreshes_the_actual_image_url(web):
    page, fixture, _, errors = web
    fixture.recipes[1]["image_backups"] = [{"id": 55, "recipe_id": 2, "created_at": 1791000000}]
    page.route(ORIGIN + "/api/recipes/image-backups/55/restore", lambda route: route.fulfill(json={"ok": True}))
    open_library(page)
    page.get_by_role("button", name="Rezept Ofengemüse mit Feta öffnen").click()
    image = page.locator(".recipe-detail-video > img")
    previous = image.get_attribute("src")
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator('.recipe-image-backups button').click()
    expect(image).to_have_attribute("src", re.compile(r"/api/recipes/2/thumb\?v="))
    expect(image).to_be_visible()
    assert image.get_attribute("src") != previous
    assert errors == []


def test_unregistered_cover_stays_hidden_after_a_successful_image_load(web):
    page, fixture, _, errors = web
    fixture.recipes[1]["thumb_filename"] = None
    open_library(page)
    page.get_by_role("button", name="Rezept Ofengemüse mit Feta öffnen").click()
    image = page.locator(".recipe-detail-video > img")
    page.wait_for_function("document.querySelector('.recipe-detail-video > img')?.complete")
    assert image.evaluate("image => image.naturalWidth > 0")
    expect(image).to_be_hidden()
    expect(page.get_by_text("Kein Rezeptbild vorhanden.", exact=True)).to_be_visible()
    assert errors == []


def open_library(page):
    page.goto(ORIGIN)
    expect(page.locator(".recipe-featured-copy h2")).to_have_text("Zitronen-Ricotta-Pasta")


@pytest.mark.parametrize("web", [1440, 390, 320], indirect=True)
def test_member_saves_and_unlinks_global_recipe_in_own_collection(web):
    page, fixture, width, errors = web
    fixture.role = "user"
    open_library(page)
    page.get_by_role("button", name="Rezept Ofengemüse mit Feta öffnen").click()
    expect(page.locator(".recipe-rating-row .pill")).to_have_text("Global für alle")
    page.get_by_role("button", name="In meiner Sammlung speichern", exact=True).click()
    expect(page.get_by_role("button", name="Aus meiner Sammlung entfernen", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    capture(page, f"global-saved-{width}")
    page.get_by_role("button", name="Rezeptdetails schließen").click()
    page.locator("#recipe-library").select_option("mine")
    expect(page.locator(".recipe-featured-copy h2")).to_have_text("Ofengemüse mit Feta")
    page.locator(".recipe-featured-action").click()
    page.get_by_role("button", name="Aus meiner Sammlung entfernen", exact=True).click()
    expect(page.get_by_role("button", name="In meiner Sammlung speichern", exact=True)).to_be_visible()
    assert len(fixture.recipes) == 3 and fixture.recipes[1]["in_library"] is False
    assert errors == []


@pytest.mark.parametrize("web", [1440, 390, 320], indirect=True)
def test_member_private_link_import_shows_global_reuse(web):
    page, fixture, width, errors = web
    fixture.role = "user"
    open_library(page)
    page.get_by_role("button", name="Rezept hinzufügen", exact=True).filter(visible=True).click()
    page.locator("#household-import-url").fill(fixture.recipes[1]["url"])
    expect(page.locator("#household-import-visibility")).to_be_hidden()
    page.get_by_role("button", name="In Sammlung übernehmen", exact=True).click()
    expect(page.locator(".household-import [role='status']")).to_contain_text("Kein erneuter Download")
    assert fixture.request_bodies[-1][1]["visibility"] == "private"
    assert fixture.recipes[1]["in_library"] is True and len(fixture.recipes) == 3
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    capture(page, f"global-link-import-{width}")
    assert errors == []


@pytest.mark.parametrize("web", [1440, 390, 320], indirect=True)
def test_guest_reads_household_without_active_write_controls(web):
    page, fixture, width, errors = web
    fixture.role = "guest"
    fixture.cart = [fixture.shopping_item()]
    start = fixture.meal_plan({})["week_start"]
    fixture.planned = [{"id": 1, "recipe_id": 2, "recipe_name": fixture.recipes[1]["name"],
                        "planned_for": start, "planned_servings": 2, "recipe_servings": 2,
                        "thumb_url": "/api/recipes/2/thumb", "ingredients_count": 1}]
    open_library(page)
    expect(page.locator(".guest-notice")).to_be_visible()
    expect(page.locator('.sidebar button[title="Administration"]')).to_be_hidden()
    page.get_by_role("button", name="Rezept Ofengemüse mit Feta öffnen").click()
    page.locator(".recipe-actions summary").click()
    expect(page.get_by_role("button", name="Als Favorit speichern", exact=True)).to_be_disabled()
    expect(page.get_by_role("button", name="Rezeptlink kopieren", exact=True)).to_be_disabled()
    expect(page.get_by_role("button", name="PDF herunterladen", exact=True)).to_be_enabled()
    page.locator(".recipe-actions summary").click()
    page.locator(".detail-tab").filter(has_text="Zutaten").click()
    for button in page.get_by_role("button", name="Zutaten auf Einkaufsliste", exact=True).all():
        expect(button).to_be_disabled()
    page.get_by_role("button", name="Rezeptdetails schließen").click()
    page.locator('.sidebar button[title="Wochenplan"]').click()
    expect(page.locator('.meal-plan-recipe')).to_contain_text("Ofengemüse mit Feta")
    expect(page.locator('.meal-plan-inline-servings input')).to_be_disabled()
    for button in page.locator('button').filter(has_text="Rezept hinzufügen").all():
        expect(button).to_be_disabled()
    page.locator('.sidebar button[title="Einkaufsliste"]').click()
    expect(page.locator(".cart-item-name")).to_contain_text("Tomaten")
    for checkbox in page.locator('.cart-page input[type="checkbox"]').all():
        expect(checkbox).to_be_disabled()
    page.locator('.sidebar button[title="Mein Konto"]').click()
    expect(page.locator(".account-page h1")).to_have_text("Mein Konto")
    expect(page.locator('.account-page a[href="/register"]')).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    capture(page, f"guest-account-{width}")
    assert all(method in ("GET", "HEAD", "OPTIONS") for method, path, _ in fixture.requests if path.startswith('/api/'))
    assert errors == []


@pytest.mark.parametrize("web", [1440, 390, 320], indirect=True)
def test_account_invitation_can_be_created_and_revoked(web):
    page, fixture, width, errors = web
    fixture.role = "user"
    page.goto(ORIGIN + "/account")
    expect(page.locator(".account-page h1")).to_be_visible()
    page.get_by_role("button", name="Zweite Person einladen", exact=True).click()
    link = page.locator('.account-invitation input')
    expect(link).to_be_visible()
    expect(link).to_have_value(ORIGIN + "/register?invite=demo-single-use-invitation-token")
    assert link.get_attribute("readonly") is not None
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    capture(page, f"account-invitation-{width}")
    page.get_by_role("button", name="Widerrufen", exact=True).click()
    expect(link).to_be_hidden()
    assert fixture.account_invitations[0]["revoked_at"] is not None
    assert errors == []


@pytest.mark.parametrize("web", [1440, 390, 320], indirect=True)
def test_keyboard_recipe_mobile_actions_cooking_and_shopping(web):
    page, fixture, width, errors = web
    open_library(page)
    expect(page.locator(".recipe-featured-media > img")).to_be_visible()
    assert page.locator(".recipe-featured-media > img").evaluate("image => image.naturalWidth") > 0
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    capture(page, f"library-{width}")

    opener = page.get_by_role("button", name="Rezept Ofengemüse mit Feta öffnen")
    opener.focus()
    opener.press("Enter")
    expect(page.locator("#recipe-detail-title")).to_have_text("Ofengemüse mit Feta")
    page.locator(".recipe-actions summary").click()
    for label in ["PDF herunterladen", "PDF teilen", "Rezeptlink kopieren", "Eigenes Bild hochladen"]:
        expect(page.get_by_role("button", name=label, exact=True)).to_be_visible()
    capture(page, f"detail-menu-{width}")
    page.get_by_role("button", name="Als Favorit speichern", exact=True).click()
    expect(page.get_by_role("button", name="Favorit entfernen", exact=True)).to_be_visible()
    assert fixture.recipes[1]["is_favorite"] is True
    page.locator(".recipe-actions summary").focus()
    page.keyboard.press("Escape")
    expect(page.locator(".recipe-actions")).not_to_have_attribute("open", "")
    expect(page.locator(".recipe-detail-modal")).to_be_visible()
    page.get_by_role("button", name="Kochmodus starten", exact=True).click()
    expect(page.get_by_role("button", name="Kochmodus beenden", exact=True)).to_be_visible()
    expect(page.locator(".step-text").first).to_contain_text("Zutaten vorbereiten")
    capture(page, f"cooking-{width}")
    page.keyboard.press("Escape")
    expect(page.locator(".recipe-detail-backdrop")).to_be_hidden()
    expect(opener).to_be_focused()

    opener.press("Enter")
    expect(page.locator("#recipe-detail-title")).to_have_text("Ofengemüse mit Feta")
    page.locator(".detail-tab").filter(has_text="Zutaten").click()
    page.get_by_role("button", name="Zutaten auf Einkaufsliste", exact=True).filter(visible=True).click()
    expect(page.get_by_role("status").filter(has_text="neu")).to_be_visible()
    assert fixture.cart[0]["name"] == "Tomaten"
    page.get_by_role("button", name="Rezeptdetails schließen").click()
    page.locator(".nav-item, .mobile-nav-item").filter(has_text="Einkauf").filter(visible=True).first.click()
    expect(page.locator(".cart-item-name")).to_contain_text("Tomaten")
    assert errors == []


def test_detail_loading_error_retry_and_close(web):
    page, fixture, _, errors = web
    fixture.defer_details = True
    open_library(page)
    opener = page.get_by_role("button", name="Rezept Ofengemüse mit Feta öffnen")
    opener.focus()
    with page.expect_request(ORIGIN + "/api/recipes/2"):
        opener.press("Enter")
    expect(page.locator(".recipe-detail-state[role=status]")).to_be_visible()
    expect(page.get_by_role("button", name="Rezeptdetails schließen")).to_be_visible()
    assert fixture.pending_details
    fixture.defer_details = False
    fixture.pending_details.pop().fulfill(status=503, json={"detail": "Server vorübergehend nicht erreichbar"})
    expect(page.locator(".recipe-detail-state[role=alert]")).to_contain_text("Server vorübergehend nicht erreichbar")
    capture(page, "detail-error")
    page.get_by_role("button", name="Erneut versuchen", exact=True).click()
    expect(page.locator("#recipe-detail-title")).to_have_text("Ofengemüse mit Feta")
    expect(page.locator(".recipe-detail-state[role=alert]")).to_be_hidden()
    page.keyboard.press("Escape")
    expect(page.locator(".recipe-detail-backdrop")).to_be_hidden()
    assert errors == []


def test_explicit_ingredient_filters_and_honest_featured_status(web):
    page, fixture, _, errors = web
    fixture.recipes[0]["ingredients_status"] = "error"
    fixture.recipes[0]["ingredients_count"] = 0
    open_library(page)
    expect(page.locator(".recipe-status-label")).to_have_text("Zutaten konnten nicht erkannt werden")
    page.locator('[aria-controls="recipe-filter-panel"]').filter(visible=True).click()
    group = page.get_by_role("group", name="Tomaten filtern")
    group.get_by_role("button", name="Mit", exact=True).click()
    expect(group.get_by_role("button", name="Mit", exact=True)).to_have_attribute("aria-pressed", "true")
    group.get_by_role("button", name="Ohne", exact=True).click()
    expect(group.get_by_role("button", name="Ohne", exact=True)).to_have_attribute("aria-pressed", "true")
    expect(group.get_by_role("button", name="Mit", exact=True)).to_have_attribute("aria-pressed", "false")
    expect(page.locator(".recipe-card-open")).to_have_count(0)
    assert any(query.get("exclude_ingredient") == ["tomate"] for _, path, query in fixture.requests if path == "/api/recipes")
    group.get_by_role("button", name="Ohne", exact=True).click()
    expect(page.locator(".recipe-card-open")).to_have_count(2)
    assert errors == []


def test_weekly_plan_add_and_shopping_after_module_split(web):
    page, fixture, _, errors = web
    open_library(page)
    page.locator(".nav-item, .mobile-nav-item").filter(has_text="Wochenplan").filter(visible=True).first.click()
    expect(page.locator(".meal-plan-day")).to_have_count(7)
    page.locator(".meal-plan-day").first.get_by_role("button", name="Rezept hinzufügen").click()
    page.locator(".meal-plan-add-panel select").select_option("2")
    page.get_by_role("button", name="Einplanen", exact=True).click()
    expect(page.locator(".meal-plan-recipe-name")).to_have_text("Ofengemüse mit Feta")
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Woche einkaufen", exact=False).click()
    expect(page.locator(".cart-item-name")).to_contain_text("Tomaten")
    assert len(fixture.planned) == 1
    assert errors == []


@pytest.mark.parametrize("web", [1440, 320], indirect=True)
def test_library_connection_error_and_retry_are_visible(web):
    page, fixture, width, errors = web
    fail = True

    def recipes(route):
        if fail:
            route.fulfill(status=503, json={"detail": "Testverbindung unterbrochen"})
        else:
            fixture.handle(route)

    page.route(ORIGIN + "/api/recipes?*", recipes)
    page.goto(ORIGIN)
    alert = page.locator(".recipes-load-error")
    expect(alert).to_contain_text("Testverbindung unterbrochen")
    expect(page.locator(".recipes-grid .empty-state")).to_be_hidden()
    retry = alert.get_by_role("button", name="Erneut laden", exact=True)
    expect(retry).to_be_enabled()
    assert retry.bounding_box()["height"] >= 44
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    capture(page, f"library-error-{width}")
    fail = False
    retry.click()
    expect(page.locator(".recipe-featured-copy h2")).to_have_text("Zitronen-Ricotta-Pasta")
    expect(alert).to_be_hidden()
    assert errors == []


def test_cart_load_error_preserves_items_and_retry_recovers(web):
    page, fixture, _, errors = web
    fixture.cart = [fixture.shopping_item()]
    fail = False

    def cart(route):
        if fail:
            route.fulfill(status=503, json={"detail": "Testverbindung unterbrochen"})
        else:
            fixture.handle(route)

    page.route(ORIGIN + "/api/cart", cart)
    open_library(page)
    nav = page.locator(".nav-item, .mobile-nav-item").filter(visible=True)
    nav.filter(has_text="Einkauf").first.click()
    expect(page.locator(".cart-item-name")).to_contain_text("Tomaten")
    fail = True
    nav.filter(has_text="Rezepte").first.click()
    nav.filter(has_text="Einkauf").first.click()
    alert = page.locator(".cart-connection-error").filter(has_text="Einkaufsliste nicht erreichbar")
    expect(alert).to_contain_text("Testverbindung unterbrochen")
    expect(page.locator(".cart-item-name")).to_contain_text("Tomaten")
    capture(page, "cart-error-390")
    fail = False
    alert.get_by_role("button", name="Erneut laden", exact=True).click()
    expect(alert).to_be_hidden()
    expect(page.get_by_role("checkbox", name="Als erledigt markieren: Tomaten")).to_be_visible()
    assert errors == []


def test_week_retry_uses_the_failed_week_instead_of_the_previous_display(web):
    page, fixture, _, errors = web
    fail = False

    def plan(route):
        if fail:
            route.fulfill(status=503, json={"detail": "Testverbindung unterbrochen"})
        else:
            fixture.handle(route)

    page.route(ORIGIN + "/api/meal-plan*", plan)
    open_library(page)
    page.locator(".nav-item, .mobile-nav-item").filter(has_text="Wochenplan").filter(visible=True).first.click()
    expect(page.locator(".meal-plan-day")).to_have_count(7)
    next_week = fixture.meal_plan({})["next_week"]
    fail = True
    page.get_by_role("button", name="Nächste Woche", exact=True).click()
    alert = page.locator(".meal-plan-alert-error")
    expect(alert).to_contain_text("Testverbindung unterbrochen")
    fail = False
    with page.expect_request(ORIGIN + "/api/meal-plan?week_start=" + next_week):
        alert.get_by_role("button", name="Erneut laden", exact=True).click()
    expect(alert).to_be_hidden()
    expect(page.locator(".meal-plan-day")).to_have_count(7)
    assert errors == []


def test_failed_next_recipe_page_retains_cards_and_retries(web):
    page, fixture, _, errors = web
    fail = True

    def recipes(route):
        query = parse_qs(urlsplit(route.request.url).query)
        offset = int(query.get("offset", [0])[0])
        if not offset:
            route.fulfill(json={"items": fixture.recipes[:2], "total": 3})
        elif fail:
            route.fulfill(status=503, json={"detail": "Testverbindung unterbrochen"})
        else:
            route.fulfill(json={"items": fixture.recipes[2:], "total": 3})

    page.route(ORIGIN + "/api/recipes?*", recipes)
    open_library(page)
    page.locator('[x-ref="scrollSentinel"]').scroll_into_view_if_needed()
    alert = page.locator(".recipes-more-error")
    expect(alert).to_be_visible()
    expect(alert).to_contain_text("Testverbindung unterbrochen")
    expect(page.locator(".recipe-card-open")).to_have_count(1)
    fail = False
    alert.get_by_role("button", name="Weitere Rezepte laden", exact=True).click()
    expect(page.locator(".recipe-card-open")).to_have_count(2)
    expect(alert).to_be_hidden()
    assert errors == []
