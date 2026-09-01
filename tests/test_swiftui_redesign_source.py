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
    assert 'ASC_ASSIGN_INTERNAL_GROUP: "false"' in workflow
    assert 'ASC_ASSIGN_INTERNAL_GROUP: "true"' not in workflow


def test_swiftui_theme_is_user_selectable_and_persisted():
    theme = _read(SWIFT / "Design" / "Theme.swift")
    settings = _read(SWIFT / "Views" / "Settings" / "SettingsView.swift")

    for choice in ("butter", "sage", "tomato", "plum"):
        assert f"case {choice}" in theme
    assert "appearance-theme-v1" in theme
    assert "appearance-mode-v1" in theme
    assert "?? .plum" in theme
    assert "ThemeChoice.plum.theme" in theme
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


def test_swiftui_recipe_passport_exposes_stable_identity_and_original_source():
    detail = _read(SWIFT / "Views" / "Recipes" / "RecipeDetailView.swift")

    assert 'LabeledContent("Rezept-ID")' in detail
    assert 'Text("#\\(recipe.id)")' in detail
    assert 'Text("Originalquelle")' in detail
    assert "sourceURL.absoluteString" in detail
    assert "UIPasteboard.general.string" in detail
    assert "ShareLink(item: sourceURL)" in detail
    assert "Originalquelle ergänzen" in detail
    assert "sourceAddedAt" in detail


def test_swiftui_source_watcher_exposes_diff_quality_and_safe_review_flow():
    detail = _read(SWIFT / "Views" / "Recipes" / "RecipeDetailView.swift")
    watcher = _read(SWIFT / "Views" / "Recipes" / "RecipeSourceIntegrityView.swift")
    api = _read(SWIFT / "Networking" / "APIClient.swift")
    main = _read(ROOT / "app" / "main.py")

    assert 'session.supports("source-integrity-v2")' in detail
    assert "Quellenwächter & Rezept-TÜV" in detail
    for endpoint in (
        "/source-integrity",
        "/source-integrity/check",
        "/source-integrity/accept",
    ):
        assert endpoint in api
    assert "Quelle hat sich geändert" in watcher
    assert "Quellprüfungen überschreiben niemals Rezeptdaten" in watcher
    assert "Als neuen Quellstand bestätigen" in watcher
    assert '"source-integrity-v1"' in main


def test_swiftui_selected_differentiators_are_native_and_capability_gated():
    detail = _read(SWIFT / "Views" / "Recipes" / "RecipeDetailView.swift")
    meal_plan = _read(SWIFT / "Views" / "MealPlan" / "MealPlanView.swift")
    conductor = _read(SWIFT / "Views" / "MealPlan" / "MealConductorView.swift")
    watcher = _read(SWIFT / "Views" / "Recipes" / "RecipeSourceIntegrityView.swift")
    substitutions = _read(SWIFT / "Views" / "Recipes" / "SubstitutionLabView.swift")
    api = _read(SWIFT / "Networking" / "APIClient.swift")
    main = _read(ROOT / "app" / "main.py")

    assert 'session.supports("meal-conductor-v1")' in meal_plan
    assert "MealConductorView(day: day)" in meal_plan
    assert "Gemeinsamer Ablauf" in conductor
    assert "/api/meal-plan/conductor/preview" in api

    assert 'session.supports("substitution-lab-v1")' in detail
    assert "SubstitutionLabView" in detail
    assert "/substitutions/apply" in api
    assert "Das Original bleibt unverändert" in substitutions

    assert "Änderungswirkung" in watcher
    assert "keine medizinische Sicherheitsfreigabe" in watcher
    assert "Keine medizinische Sicherheitsfreigabe" in substitutions
    for capability in (
        "meal-conductor-v1",
        "source-integrity-v2",
        "substitution-lab-v1",
    ):
        assert f'"{capability}"' in main


def test_swiftui_differentiator_safety_states_follow_the_hardened_contracts():
    models = _read(SWIFT / "Models" / "Models.swift")
    api = _read(SWIFT / "Networking" / "APIClient.swift")
    detail = _read(SWIFT / "Views" / "Recipes" / "RecipeDetailView.swift")
    watcher = _read(SWIFT / "Views" / "Recipes" / "RecipeSourceIntegrityView.swift")
    substitutions = _read(SWIFT / "Views" / "Recipes" / "SubstitutionLabView.swift")
    conductor = _read(SWIFT / "Views" / "MealPlan" / "MealConductorView.swift")

    for contract_field in (
        "variantProvenance",
        "variantReviewNotice",
        "resultIngredient",
        "blockedAutoTags",
        "activeCooks",
        "counterAdjustments",
        "startsPreviousDay",
    ):
        assert contract_field in models

    assert ".interactiveDismissDisabled(isApplying)" in substitutions
    assert '.disabled(isApplying)' in substitutions
    assert "applyTask?.cancel()" not in substitutions
    assert "Variante wird sicher angelegt" in substitutions
    assert 'LabeledContent("Vorher"' in substitutions
    assert 'LabeledContent("Nachher"' in substitutions
    assert "candidate.blockedAutoTags" in substitutions
    assert "recipe.variantReviewNotice" in detail
    assert "variantProvenanceSection" in detail

    assert 'quality.status == "verified"' in watcher
    assert "Struktur vollständig – manuelle Prüfung bleibt offen" in watcher
    assert "keine Lebensmittel- oder Allergensicherheit" in watcher
    assert "expectedSnapshotID: latest.id" in watcher
    assert "status == 409" in watcher
    assert "reloadAfterAcceptConflict" in watcher
    assert "expectedSnapshotId" in api and "expectedContentSha256" in api

    assert "Aktive Köch:innen" in conductor
    assert "plan = nil" in conductor
    assert "readOnly: session.readOnly" in conductor
    assert 'URLQueryItem(name: "active_cooks"' in api
    assert 'method: "POST"' in api


def test_swiftui_admin_settings_use_safe_partial_config_contract():
    api = _read(SWIFT / "Networking" / "APIClient.swift")
    models = _read(SWIFT / "Models" / "AdminConfigModels.swift")
    settings = _read(SWIFT / "Views" / "Admin" / "AdminSettingsView.swift")
    admin = _read(SWIFT / "Views" / "Admin" / "AdminView.swift")
    main = _read(ROOT / "app" / "main.py")

    for endpoint in (
        "/api/config",
        "/api/config/reload",
        "/api/test/openai",
        "/api/test/mail",
        "/api/schedule/preview",
        "/api/config/logs/stats",
        "/api/config/logs/cleanup",
        "/api/config/backups/list",
        "/api/config/backups/run-now",
    ):
        assert endpoint in api
    assert "NativeAdminConfigPatch" in models
    patch_models = models.split("struct NativeAdminConfigPatch", 1)[1]
    assert "let baseUrl" not in patch_models
    assert "let apiUrl" not in patch_models
    assert "let paths" not in patch_models
    assert "Leere Geheimnisfelder behalten den gespeicherten Wert" in settings
    assert "Ziel-URLs und Serverpfade sind ausschließlich auf dem Server änderbar" in settings
    assert "Ungespeicherte Änderungen" in settings
    assert "Datenbank jetzt sichern" in settings
    assert "Alte Logs bereinigen" in settings
    assert "Gemäß Aufbewahrungsfrist löschen" in settings
    assert "changedSections" in settings
    assert 'session.supports("native-admin-config-v1")' in admin
    assert '"native-admin-config-v1"' in main


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
    authenticated_image = _read(SWIFT / "Views" / "Common" / "AuthenticatedImage.swift")
    admin = _read(SWIFT / "Views" / "Admin" / "AdminView.swift")
    api = _read(SWIFT / "Networking" / "APIClient.swift")

    assert "Gesicherte Originale" in history
    assert "restoreImageBackup" in history
    assert "Original und generierte Fassung vergleichen" in history
    assert "refreshToken: refreshToken" in history
    assert "refreshToken?.uuidString" in authenticated_image
    assert ".reloadIgnoringLocalCacheData" in api
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
    assert '"Für wie viele Portionen kochst du?"' in cooking
    assert '"Die Portionszahl fehlt. Du kannst trotzdem kochen' in cooking
    assert "private var canScale" in cooking
    assert '"Kochen starten"' in cooking
    assert "hasStartedCooking = progress.exists" in cooking
    assert "startCooking()" in cooking
    assert "CookingTimerView" in cooking
    assert "completionRequestID" in cooking
    assert '"Idempotency-Key": idempotencyKey' in api
    assert '"/api/recipes/\\(id)/cooking-complete"' in api
    assert '"Für heute einplanen"' in detail
    assert '"Anderen Tag wählen"' in detail
    assert "PlanRecipeSheet" in detail
    assert "session.api.addMeal" in detail
    assert "reextractRecipeSource" in detail
    assert 'name: "refresh_media"' in api


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
    assert '"Küchengrundlagen anzeigen"' in filters
    assert "DisclosureGroup" in filters
    assert "ingredientGroups" in filters
    assert "ingredient.isPantryBasic" in filters
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
