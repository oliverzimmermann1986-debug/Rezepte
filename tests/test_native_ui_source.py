from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "native-ios" / "src"


def test_native_ingredient_editors_use_one_unit_picker():
    picker = (NATIVE / "components" / "unit-picker.tsx").read_text(encoding="utf-8")
    recipe_editor = (NATIVE / "components" / "recipe-editor.tsx").read_text(encoding="utf-8")
    pending_editor = (NATIVE / "components" / "pending-editor.tsx").read_text(encoding="utf-8")
    cart = (NATIVE / "app" / "(tabs)" / "cart.tsx").read_text(encoding="utf-8")

    assert "ActionSheetIOS.showActionSheetWithOptions" in picker
    assert "Bisherige Einheit" in picker
    assert "<UnitPicker" in recipe_editor
    assert "<UnitPicker" in pending_editor
    assert "<UnitPicker" in cart
    assert 'placeholder="Einheit"' not in recipe_editor
    assert 'placeholder="Einheit"' not in pending_editor
    assert '<Field label="Einheit"' not in cart


def test_native_dynamic_editor_rows_have_stable_keys():
    recipe_editor = (NATIVE / "components" / "recipe-editor.tsx").read_text(encoding="utf-8")
    pending_editor = (NATIVE / "components" / "pending-editor.tsx").read_text(encoding="utf-8")

    assert "key={index}" not in recipe_editor
    assert "key={index}" not in pending_editor
    assert "key={item.clientKey}" in recipe_editor
    assert "key={ingredient.clientKey}" in pending_editor
    assert "key={step.clientKey}" in pending_editor


def test_recurring_date_uses_local_calendar_and_strict_validation():
    cart = (NATIVE / "app" / "(tabs)" / "cart.tsx").read_text(encoding="utf-8")
    date_input = (NATIVE / "lib" / "date-input.ts").read_text(encoding="utf-8")

    assert "nextDueOn: localDateInput()" in cart
    assert "isValidDateInput(editor.nextDueOn)" in cart
    assert "toISOString().slice(0, 10)" not in cart
    assert "value.getFullYear()" in date_input
    assert "new Date(year, month, 0).getDate()" in date_input


def test_external_links_are_validated_and_fail_visibly():
    helper = (NATIVE / "lib" / "external-links.ts").read_text(encoding="utf-8")
    sources = [
        NATIVE / "app" / "recipe" / "[id].tsx",
        NATIVE / "components" / "pending-editor.tsx",
        NATIVE / "app" / "(tabs)" / "admin.tsx",
        NATIVE / "app" / "login.tsx",
    ]

    assert "parsed.protocol !== 'https:'" in helper
    assert "parsed.username || parsed.password" in helper
    assert "await Linking.openURL(normalized)" in helper
    assert "host.endsWith('.instagram.com')" in helper
    assert "host.endsWith('.tiktok.com')" in helper
    for source in sources:
        code = source.read_text(encoding="utf-8")
        assert "Linking.openURL" not in code
        assert "openExternalUrl" in code


def test_favorite_toggle_reports_network_errors():
    detail = (NATIVE / "app" / "recipe" / "[id].tsx").read_text(encoding="utf-8")

    favorite = detail[detail.index("async function toggleFavorite"):detail.index("async function addToCart")]
    assert "catch (reason)" in favorite
    assert "Favorit nicht geändert" in favorite


def test_bulk_editor_shows_the_recipe_currently_being_processed():
    bulk_editor = (NATIVE / "components" / "admin-bulk-editor.tsx").read_text(encoding="utf-8")

    assert "setProgress({" in bulk_editor
    assert "recipe_ids: [recipe.id]" in bulk_editor
    assert "Rezept {progress.current} von {progress.total}" in bulk_editor
    assert "{progress.recipeName}" in bulk_editor
    assert "Wird gerade bearbeitet" in bulk_editor
    assert 'accessibilityRole="progressbar"' in bulk_editor


def test_recipe_info_shows_selectable_archive_id():
    detail = (NATIVE / "app" / "recipe" / "[id].tsx").read_text(encoding="utf-8")

    assert "Rezept-ID" in detail
    assert "Videoarchiv: {recipe.id}.mp4" in detail
    assert "accessibilityLabel={`Rezept-ID ${recipe.id}`}" in detail
    assert "selectable" in detail
