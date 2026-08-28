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
    assert "categoryIcon" in cart
    assert "openCategoryNames" in cart
    assert '"/api/cart/suggestions"' in api
    assert '"/api/cart/categories"' in api
    assert "category: category" in api


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
    assert "CookingTimerView" in cooking
    assert "completionRequestID" in cooking
    assert '"Idempotency-Key": idempotencyKey' in api
    assert '"/api/recipes/\\(id)/cooking-complete"' in api


def test_swiftui_allergen_information_is_a_separate_multi_select_filter():
    filters = _read(SWIFT / "Views" / "Recipes" / "RecipeFiltersView.swift")
    models = _read(SWIFT / "Models" / "Models.swift")
    api = _read(SWIFT / "Networking" / "APIClient.swift")

    assert 'Label("Allergiker-Infos", systemImage: "checkmark.shield")' in filters
    assert "allergenBinding" in filters
    assert "draft.allergenTagIDs" in filters
    assert "alle ausgewählten Frei-von-Tags" in filters
    assert "ersetzen keine medizinische Prüfung" in filters
    for value in ("glutenfrei", "laktosefrei", "eifrei", "nussfrei"):
        assert value in models
    assert "var allergenTagIDs: Set<Int>" in models
    assert "filters.tagIDs.union(filters.allergenTagIDs)" in api


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


def test_swiftui_recipe_comments_follow_selected_language_and_keep_original():
    detail = _read(SWIFT / "Views" / "Recipes" / "RecipeDetailView.swift")
    settings = _read(SWIFT / "Views" / "Settings" / "SettingsView.swift")
    theme = _read(SWIFT / "Design" / "Theme.swift")
    api = _read(SWIFT / "Networking" / "APIClient.swift")
    models = _read(SWIFT / "Models" / "Models.swift")

    assert "enum CommentLanguage" in theme
    assert 'commentLanguageKey = "comment-language-v1"' in theme
    assert "Kommentare anzeigen auf" in settings
    assert "themeStore.commentLanguage" in settings
    assert "Gemeinsame Kochnotizen" in detail
    assert "Automatisch übersetzt aus" in detail
    assert "comment.originalText" in detail
    assert "if session.readOnly" in detail
    assert '"/api/recipes/\\(id)/comments"' in api
    assert 'URLQueryItem(name: "language", value: language.rawValue)' in api
    assert "struct RecipeComment" in models
