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
