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


def test_recipe_info_shows_only_a_neutral_selectable_recipe_id():
    detail = (NATIVE / "app" / "recipe" / "[id].tsx").read_text(encoding="utf-8")

    assert "Rezept-ID" in detail
    assert "Videoarchiv" not in detail
    assert ".mp4" not in detail
    assert "accessibilityLabel={`Rezept-ID ${recipe.id}`}" in detail
    assert "selectable" in detail


def test_native_cooking_completion_retries_keep_one_persistent_request_id():
    cooking = (NATIVE / "app" / "cook" / "[id].tsx").read_text(encoding="utf-8")

    persist = cooking[cooking.index("function persistProgress"):cooking.index("function toggleCurrentStep")]
    finish = cooking[cooking.index("async function finishCooking"):]
    assert "cooking-completion-request:" in cooking
    assert "readApiCache<string>(completionStorageKey)" in cooking
    assert "putApiCache(completionStorageKey, nextRequestId)" in cooking
    assert "rotateCompletionRequestId" not in persist
    assert "'Idempotency-Key': completionRequestId.current" in finish
    assert finish.index("await api(") < finish.index("rotateCompletionRequestId();")


def test_native_authenticated_downloads_are_cancelled_on_session_change():
    api_source = (NATIVE / "lib" / "api.ts").read_text(encoding="utf-8")
    share_sources = [
        NATIVE / "app" / "recipe" / "[id].tsx",
        NATIVE / "app" / "(tabs)" / "plan.tsx",
        NATIVE / "components" / "pending-editor.tsx",
    ]

    assert "const activeDownloads = new Map" in api_source
    assert "cancelDownloadsFromPreviousSessions();" in api_source
    assert "download.task.cancelAsync()" in api_source
    assert "assertApiSessionEpochCurrent(requestEpoch);" in api_source
    assert "const DOWNLOAD_TIMEOUT_MS = 90_000" in api_source
    assert "Promise.race([task.downloadAsync(), timeout])" in api_source
    assert "Der Dateidownload dauert zu lange" in api_source
    for source in share_sources:
        code = source.read_text(encoding="utf-8")
        share_flow = code[code.index("downloadFileToCache("):code.index("Sharing.shareAsync")]
        assert share_flow.count("assertApiSessionEpochCurrent(downloadEpoch)") >= 2


def test_native_cache_is_best_effort_and_follows_server_mutations():
    cache = (NATIVE / "lib" / "cache.ts").read_text(encoding="utf-8")
    metadata = (NATIVE / "components" / "recipe-metadata-editor.tsx").read_text(encoding="utf-8")

    clear_cache = cache[cache.index("export async function clearApiCache"):]
    assert "try {" in clear_cache
    assert "} catch {" in clear_cache
    assert metadata.index("/metadata`") < metadata.index("/tags`")
    assert metadata.index("/tags`") < metadata.index("if (serverChanged)")


def test_native_admin_role_refresh_remounts_the_native_tabs():
    auth = (NATIVE / "lib" / "auth-context.tsx").read_text(encoding="utf-8")
    tabs = (NATIVE / "app" / "(tabs)" / "_layout.tsx").read_text(encoding="utf-8")

    assert "if (state === 'active') void refreshSession();" in auth
    assert "state === 'active' && sessionWarning" not in auth
    assert "[ready, refreshSession, token]" in auth
    assert "key={isAdmin ? 'admin-tabs' : 'user-tabs'}" in tabs
    assert '<NativeTabs.Trigger name="admin" hidden={!isAdmin}>' in tabs
    assert "{isAdmin && (" not in tabs


def test_pending_editor_can_reanalyze_with_ai_using_long_request_timeout():
    pending_editor = (NATIVE / "components" / "pending-editor.tsx").read_text(encoding="utf-8")
    api_source = (NATIVE / "lib" / "api.ts").read_text(encoding="utf-8")

    assert "Nochmals mit KI prüfen" in pending_editor
    assert "'/api/pending/reanalyze'" in pending_editor
    assert "120_000" in pending_editor
    assert "setIngredients(suggestion.ingredients.map(createIngredientRow))" in pending_editor
    assert "setSteps(suggestion.steps.map(createStepRow))" in pending_editor
    assert "timeoutMs = REQUEST_TIMEOUT_MS" in api_source
    assert "signal,\n    timeoutMs," in api_source


def test_native_cart_groups_categories_and_emphasizes_amounts():
    cart = (NATIVE / "app" / "(tabs)" / "cart.tsx").read_text(encoding="utf-8")

    assert "<SectionList" in cart
    assert "sections={cartSections}" in cart
    assert "renderSectionHeader" in cart
    assert "SHOPPING_CATEGORIES" in cart
    assert "item.category?.trim() || 'Sonstiges'" in cart
    assert "new Intl.NumberFormat('de-DE'" in cart
    assert "formatCartAmount(item) || '—'" in cart
    assert "fontVariant: ['tabular-nums']" in cart
    assert "cartItemAccessibilityLabel(item)" in cart


def test_native_recipe_filters_follow_a_clear_progressive_order():
    recipes = (NATIVE / "app" / "(tabs)" / "index.tsx").read_text(encoding="utf-8")

    headings = [
        "Gericht</Text>",
        "Status & Bewertung</Text>",
        "Tags</Text>",
        "Zutaten</Text>",
    ]
    positions = [recipes.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert "Grenze erst die Rezeptart ein" in recipes
    assert "filterSection:" in recipes


def test_native_admin_duplicate_finder_is_read_only_and_opens_candidates():
    admin = (NATIVE / "app" / "(tabs)" / "admin.tsx").read_text(encoding="utf-8")
    duplicates = (NATIVE / "components" / "admin-duplicates.tsx").read_text(encoding="utf-8")

    assert 'label="Dubletten finden"' in admin
    assert "<AdminDuplicates" in admin
    assert "router.push(`/recipe/${recipeId}`)" in admin
    assert "'/api/audit" not in duplicates
    assert "`/api/audit?${params}`" in duplicates
    assert "exact_duplicates" in duplicates
    assert "url_duplicates" in duplicates
    assert "similar_clusters" in duplicates
    assert "Es wird nichts automatisch gelöscht" in duplicates
    assert "method: 'DELETE'" not in duplicates
