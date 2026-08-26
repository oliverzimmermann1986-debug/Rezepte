from app.recipes.naming import normalize_recipe_name


def test_normalize_recipe_name_replaces_underscores_and_collapses_whitespace():
    assert normalize_recipe_name("  Omas__ Kuchen_ Deluxe  ") == "Omas Kuchen Deluxe"


def test_normalize_recipe_name_handles_compatible_unicode_underscores():
    assert normalize_recipe_name("Sushi＿Bowl") == "Sushi Bowl"


def test_normalize_recipe_name_keeps_empty_values_empty_for_caller_validation():
    assert normalize_recipe_name(None) == ""
    assert normalize_recipe_name("___") == ""
