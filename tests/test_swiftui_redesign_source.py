from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SWIFT = ROOT / "ios-swift" / "Rezepte"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_swiftui_is_the_primary_native_path_with_source_first_navigation():
    tabs = _read(SWIFT / "Views" / "MainTabView.swift")
    readme = _read(ROOT / "README.md")
    workflow = _read(ROOT / ".github" / "workflows" / "ios-swift.yml")

    assert "InboxView()" in tabs
    assert 'Label("Eingang"' in tabs
    assert "SettingsView()" in tabs
    assert "ios-swift/" in readme and "iPhone-Hauptpfad" in readme
    assert "xcodegen generate" in workflow
    assert "xcodebuild" in workflow
    assert "upload_testflight:" in workflow
    assert "IOS_SHARE_PROFILE_BASE64" in workflow
    assert "ASC_ASSIGN_INTERNAL_GROUP" not in workflow


def test_swiftui_theme_is_user_selectable_and_persisted():
    theme = _read(SWIFT / "Design" / "Theme.swift")
    settings = _read(SWIFT / "Views" / "Settings" / "SettingsView.swift")

    for choice in ("butter", "sage", "tomato", "plum"):
        assert f"case {choice}" in theme
    assert "appearance-theme-v1" in theme
    assert "appearance-mode-v1" in theme
    assert "themeStore.selection" in settings
    assert "preferredColorScheme" in settings


def test_swiftui_import_accepts_open_web_sources_and_share_extension_matches():
    inbox = _read(SWIFT / "Views" / "Inbox" / "InboxView.swift")
    share = _read(ROOT / "ios-swift" / "RezepteShare" / "ShareViewController.swift")

    for source in ("Webseite", "Pinterest", "YouTube", "TikTok", "Instagram"):
        assert source in inbox
    assert '.contains(url.scheme?.lowercased())' in inbox
    assert '["https", "http"].contains' in share
    assert "Zu Quellenküche" in share


def test_swiftui_cart_uses_catalog_suggestions_icons_and_categories():
    cart = _read(SWIFT / "Views" / "Cart" / "CartView.swift")
    api = _read(SWIFT / "Networking" / "APIClient.swift")

    assert "shoppingSuggestions" in cart
    assert "query.isEmpty ? 12 : 8" in cart
    assert "categoryIcon" in cart
    assert "openCategoryNames" in cart
    assert 'TextField("Menge", text: $newAmount)' in cart
    assert 'Text(newUnit.nilIfEmpty ?? "Einheit")' in cart
    assert "amount: amount" in cart
    assert "unit: unit" in cart
    assert '"/api/cart/suggestions"' in api
    assert '"/api/cart/categories"' in api
    assert "category: category" in api


def test_swiftui_cart_restores_recurring_purchase_management():
    cart = _read(SWIFT / "Views" / "Cart" / "CartView.swift")
    api = _read(SWIFT / "Networking" / "APIClient.swift")
    models = _read(SWIFT / "Models" / "Models.swift")

    assert 'case recurring = "Wiederkehrend"' in cart
    assert "RecurringEditorView" in cart
    assert 'TextField("Menge", text: $draft.amount)' in cart
    assert 'Picker("Supermarkt-Kategorie", selection: $draft.category)' in cart
    assert 'DatePicker(' in cart and '"Nächster Einkauf"' in cart
    assert "runRecurringCart" in cart
    assert "setRecurringCartItem" in cart
    assert "deleteRecurringCartItem" in cart
    assert '"/api/cart/recurring"' in api
    assert '"/api/cart/recurring/run"' in api
    assert "struct RecurringCartItem" in models
    assert "@FlexibleBool var active" in models


def test_swiftui_exposes_backend_library_and_shopping_tools():
    api = _read(SWIFT / "Networking" / "APIClient.swift")
    admin = _read(SWIFT / "Views" / "Admin" / "AdminLibraryToolsView.swift")
    shopping = _read(SWIFT / "Views" / "Cart" / "ShoppingToolsView.swift")
    detail = _read(SWIFT / "Views" / "Recipes" / "RecipeDetailView.swift")

    for endpoint in (
        "/api/system/info",
        "/api/recipes/trash/list",
        "/api/admin/versions",
        "/api/audit/ai-sanity/findings",
        "/api/cart/optimize/preview",
        "/api/cart/export.txt",
        "/api/meal-plan/pdf",
    ):
        assert endpoint in api
    assert "Wiederherstellen" in admin and "KI-Prüfung" in admin
    assert "Vorschau übernehmen" in shopping and "Einkaufsliste teilen" in shopping
    assert "RecipeMetadataEditorView" in detail
    assert "RecipeShareLinksView" in detail
    assert "computeRecipeNutrition" in detail


def test_swiftui_checks_capabilities_and_persists_content_language():
    session = _read(SWIFT / "Session" / "SessionStore.swift")
    settings = _read(SWIFT / "Views" / "Settings" / "SettingsView.swift")
    detail = _read(SWIFT / "Views" / "Recipes" / "RecipeDetailView.swift")

    assert "refreshSystemInfo" in session
    assert "serverCapabilities" in session
    assert "compatibilityWarning" in session
    assert 'content-language-v1' in settings
    assert 'Section("Inhaltssprache")' in settings
    assert "translateRecipeText" in detail


def test_swiftui_image_generation_exposes_backup_compare_restore_flow():
    history = _read(SWIFT / "Views" / "Recipes" / "RecipeImageHistoryView.swift")
    admin = _read(SWIFT / "Views" / "Admin" / "AdminView.swift")
    api = _read(SWIFT / "Networking" / "APIClient.swift")

    assert "Gesicherte Originale" in history
    assert "restoreImageBackup" in history
    assert "Original und generierte Fassung vergleichen" in history
    assert "Sicherungsbarriere" in admin
    assert "Altbilder sichern & neu generieren" in admin
    assert '"/api/recipes/images/backfill"' in api
    assert '"/api/recipes/image-backups/\\(id)/restore"' in api


def test_swiftui_pending_review_edits_complete_recipe_and_reanalyzes():
    editor = _read(SWIFT / "Views" / "Admin" / "PendingEditorView.swift")
    api = _read(SWIFT / "Networking" / "APIClient.swift")
    models = _read(SWIFT / "Models" / "Models.swift")

    for field in ("description:", "ingredients:", "steps:", "servings:", "verified:"):
        assert field in api
    assert "Nochmals mit KI prüfen" in editor
    assert "Zutaten geprüft" in editor
    assert "parsedIngredients" in editor
    assert "parsedSteps" in editor
    assert '"/api/pending/reanalyze"' in api
    assert "struct PendingIngredient" in models
    assert "struct PendingStep" in models


def test_swiftui_cooking_mode_persists_progress_scales_and_completes_idempotently():
    cooking = _read(SWIFT / "Views" / "Recipes" / "CookingModeView.swift")
    detail = _read(SWIFT / "Views" / "Recipes" / "RecipeDetailView.swift")
    api = _read(SWIFT / "Networking" / "APIClient.swift")

    assert "CookingModeView(recipe: recipe)" in detail
    assert "updateCookingProgress" in cooking
    assert "completedSteps" in cooking
    assert "multiplier" in cooking
    assert 'Text("Für wie viele Portionen kochst du?")' in cooking
    assert '"Kochen starten"' in cooking
    assert "hasStartedCooking = progress.exists" in cooking
    assert "startCooking()" in cooking
    assert "CookingTimerView" in cooking
    assert "completionRequestID" in cooking
    assert '"Idempotency-Key": idempotencyKey' in api
    assert '"/api/recipes/\\(id)/cooking-complete"' in api


def test_swiftui_shopping_asks_for_servings_and_sends_exact_selection():
    detail = _read(SWIFT / "Views" / "Recipes" / "RecipeDetailView.swift")
    picker = _read(SWIFT / "Views" / "Common" / "ServingPicker.swift")
    api = _read(SWIFT / "Networking" / "APIClient.swift")

    assert 'Text("Für wie viele Portionen einkaufen?")' in detail
    assert "ShoppingServingsSheet" in detail
    assert "shoppingServings = recipe.servings ?? 1" in detail
    assert "addRecipeToCart(id: recipeID, servings: servings)" in detail
    assert "struct ServingPicker" in picker
    assert "original: originalServings" in detail
    assert "body: CookPayload(servings: servings)" in api


def test_swiftui_allergen_information_is_a_separate_multi_select_filter():
    filters = _read(SWIFT / "Views" / "Recipes" / "RecipeFiltersView.swift")
    models = _read(SWIFT / "Models" / "Models.swift")
    api = _read(SWIFT / "Networking" / "APIClient.swift")

    assert 'title: "Allergiker-Infos"' in filters
    assert "allergenBinding" in filters
    assert "draft.allergenTagIDs" in filters
    assert "alle ausgewählten Frei-von-Tags" in filters
    assert "ersetzen keine medizinische Prüfung" in filters
    for value in ("glutenfrei", "laktosefrei", "eifrei", "nussfrei"):
        assert value in models
    assert "var allergenTagIDs: Set<Int>" in models
    assert "filters.tagIDs.union(filters.allergenTagIDs)" in api


def test_swiftui_recipe_filter_has_live_count_and_direct_ingredient_choices():
    filters = _read(SWIFT / "Views" / "Recipes" / "RecipeFiltersView.swift")
    recipes = _read(SWIFT / "Views" / "Recipes" / "RecipesView.swift")
    api = _read(SWIFT / "Networking" / "APIClient.swift")

    assert "loadMatchCount" in filters
    assert ".task(id: draft)" in filters
    assert "applyButtonTitle" in filters
    assert "checkmark.square.fill" in filters
    assert '"Mit"' in filters and '"Ohne"' in filters
    assert ".cardSurface()" in filters
    assert "initialMatchCount: total" in recipes
    assert '"/api/recipes/count"' in api


def test_swiftui_guest_login_is_read_only_across_navigation_and_recipe_actions():
    login = _read(SWIFT / "Views" / "LoginView.swift")
    session = _read(SWIFT / "Session" / "SessionStore.swift")
    tabs = _read(SWIFT / "Views" / "MainTabView.swift")
    detail = _read(SWIFT / "Views" / "Recipes" / "RecipeDetailView.swift")
    settings = _read(SWIFT / "Views" / "Settings" / "SettingsView.swift")
    api = _read(SWIFT / "Networking" / "APIClient.swift")

    assert "Als Gast ansehen" in login
    assert "signInAsGuest" in login and "signInAsGuest" in session
    assert '"/api/auth/guest"' in api
    assert "@Published private(set) var readOnly" in session
    assert "case .signedIn = state, !readOnly" in session
    assert tabs.count("if !session.readOnly") >= 2
    assert "Gastzugang · Rezept nur ansehen" in detail
    assert detail.count("if !session.readOnly") >= 4
    assert 'Section("Gastzugang")' in settings
    assert 'value: session.readOnly ? "Nur lesen" : "Bearbeiten"' in settings
