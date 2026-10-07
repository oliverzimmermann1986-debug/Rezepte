import Foundation

enum APIError: LocalizedError {
    case invalidServer
    case insecureServer
    case unauthenticated
    case sessionChanged
    case server(Int, String)
    case invalidResponse(String)

    var errorDescription: String? {
        switch self {
        case .invalidServer:
            return "Die Serveradresse ist ungültig."
        case .insecureServer:
            return "Bitte eine HTTPS-Adresse verwenden."
        case .unauthenticated:
            return "Die Sitzung ist abgelaufen. Bitte erneut anmelden."
        case .sessionChanged:
            return "Die Sitzung hat sich geändert. Bitte erneut laden."
        case let .server(_, message):
            return message
        case let .invalidResponse(endpoint):
            return "Die Serverantwort für \(endpoint) passt nicht zur App. Bitte App und Server auf denselben Stand aktualisieren."
        }
    }
}

actor APIClient {
    /// Seitengröße der Rezeptliste (entspricht dem Server-Default).
    static let pageSize = 60

    private var baseURL: URL?
    private var token: String?
    private var configurationID = UUID()
    private let session: URLSession
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder

    init(session: URLSession = .shared) {
        self.session = session
        decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
    }

    func configure(
        server: String,
        token: String?,
        sessionID: UUID? = nil
    ) throws {
        guard let url = Self.normalizedServerURL(server) else {
            throw APIError.invalidServer
        }
        // HTTPS in JEDER Konfiguration, auch im Debug-Build: über http würde
        // der Bearer-Token im Klartext über das Netz gehen. Die frühere
        // Debug-Ausnahme passte zu NSAllowsLocalNetworking in der Info.plist —
        // beide sind entfernt, damit Simulator und Release dieselbe Regel haben
        // (Entscheidung 30.07.2026).
        guard url.scheme == "https" else {
            throw APIError.insecureServer
        }
        guard !LegacyAccessMigration.isLegacyToken(token) else {
            throw APIError.unauthenticated
        }
        baseURL = url
        self.token = token
        configurationID = sessionID ?? UUID()
        URLCache.shared.removeAllCachedResponses()
    }

    func clearAuthentication(ifSessionID expected: UUID) {
        guard configurationID == expected else { return }
        token = nil
        configurationID = UUID()
        URLCache.shared.removeAllCachedResponses()
    }

    static func normalizedServerURL(_ value: String) -> URL? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard var components = URLComponents(string: trimmed),
              let scheme = components.scheme?.lowercased(),
              ["http", "https"].contains(scheme),
              components.host != nil else {
            return nil
        }
        let cleanPath = components.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        components.path = cleanPath.isEmpty ? "" : "/\(cleanPath)"
        components.query = nil
        components.fragment = nil
        return components.url
    }

    func endpoint(_ path: String, query: [URLQueryItem] = []) throws -> URL {
        guard let baseURL else { throw APIError.invalidServer }
        let cleanPath = path.hasPrefix("/") ? String(path.dropFirst()) : path
        guard var components = URLComponents(
            url: baseURL.appendingPathComponent(cleanPath),
            resolvingAgainstBaseURL: false
        ) else {
            throw APIError.invalidServer
        }
        components.queryItems = query.isEmpty ? nil : query
        guard let url = components.url else { throw APIError.invalidServer }
        return url
    }

    func imageRequest(
        recipeID: Int,
        width: Int = 900,
        cacheVersion: String? = nil,
        forceRefresh: Bool = false
    ) throws -> URLRequest {
        var query = [URLQueryItem(name: "w", value: String(width))]
        if let cacheVersion, !cacheVersion.isEmpty {
            query.append(URLQueryItem(name: "v", value: cacheVersion))
        }
        var request = URLRequest(url: try endpoint(
            "/api/recipes/\(recipeID)/thumb",
            query: query
        ))
        if forceRefresh {
            request.cachePolicy = .reloadIgnoringLocalCacheData
        }
        authorize(&request, includeBearer: true)
        return request
    }

    func imageBackupRequest(backupID: Int) throws -> URLRequest {
        var request = URLRequest(url: try endpoint(
            "/api/recipes/image-backups/\(backupID)/file"
        ))
        request.cachePolicy = .reloadIgnoringLocalCacheData
        authorize(&request, includeBearer: true)
        return request
    }

    func privacyURL() throws -> URL {
        try endpoint("/privacy")
    }

    func login(username: String, password: String) async throws -> LoginResponse {
        try await send(
            "/api/auth/login",
            method: "POST",
            body: ["username": username, "password": password],
            authenticated: false
        )
    }

    func guestLogin() async throws -> LoginResponse {
        try await send(
            "/api/auth/guest",
            method: "POST",
            body: EmptyBody(),
            authenticated: false
        )
    }

    func register(username: String, password: String, invitationToken: String = "") async throws -> LoginResponse {
        try await send("/api/auth/register", method: "POST", body: RegistrationPayload(
            username: username, password: password,
            invitationToken: HouseholdInvitationInput.token(from: invitationToken)
        ), authenticated: false)
    }

    func logout() async throws -> APIResult {
        try await send("/api/auth/logout", method: "POST", body: EmptyBody())
    }

    func logoutAll() async throws -> APIResult {
        try await send("/api/auth/logout-all", method: "POST", body: EmptyBody())
    }

    func accountProfile() async throws -> AccountProfile {
        try await send("/api/account/profile")
    }

    func changePassword(current: String, new: String) async throws -> APIResult {
        try await send("/api/account/password", method: "POST",
                       body: ["current_password": current, "new_password": new])
    }

    func deleteAccount(currentPassword: String) async throws -> APIResult {
        try await send("/api/account/profile", method: "DELETE", body: ["current_password": currentPassword])
    }

    func accountSessions() async throws -> AccountSessions {
        try await send("/api/account/sessions")
    }

    func revokeSession(id: String) async throws -> APIResult {
        // Session IDs are opaque. Reject separators instead of turning them into another route.
        guard !id.isEmpty, id.rangeOfCharacter(from: CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_").inverted) == nil else {
            throw APIError.invalidResponse("Sitzungskennung")
        }
        return try await send("/api/account/sessions/\(id)", method: "DELETE")
    }

    func users() async throws -> AdminUsers { try await send("/api/users") }

    func createUser(username: String, password: String, role: AccountRole) async throws -> APIResult {
        try await send("/api/users", method: "POST",
                       body: ["username": username, "password": password, "role": role.rawValue])
    }

    func updateUser(id: Int, patch: AdminUserPatch) async throws -> APIResult {
        try await send("/api/users/\(id)", method: "PATCH", body: patch)
    }

    func deleteUser(id: Int) async throws -> APIResult {
        try await send("/api/users/\(id)", method: "DELETE")
    }

    func revokeUserSessions(id: Int) async throws -> APIResult {
        try await send("/api/users/\(id)/revoke-sessions", method: "POST", body: EmptyBody())
    }

    func authProviders() async throws -> AuthProviders {
        try await send("/api/auth/providers", authenticated: false)
    }

    func startNativeAuth(provider: IdentityProvider, intent: NativeAuthIntent, challenge: String,
                         invitationToken: String? = nil, currentPassword: String? = nil) async throws -> NativeAuthStart {
        try await send("/api/auth/\(provider.rawValue)/start", method: "POST",
                       body: NativeAuthStartPayload(intent: intent, codeChallenge: challenge,
                           invitationToken: invitationToken.map { HouseholdInvitationInput.token(from: $0) }.flatMap { $0.isEmpty ? nil : $0 },
                           currentPassword: intent == .link ? currentPassword : nil),
                       authenticated: intent == .link)
    }

    func exchangeNativeAuth(code: String, verifier: String) async throws -> LoginResponse {
        try await send("/api/auth/exchange", method: "POST",
                       body: NativeAuthExchangePayload(code: code, codeVerifier: verifier), authenticated: false)
    }

    func accountIdentities() async throws -> AccountIdentities {
        try await send("/api/account/identities")
    }

    func unlinkIdentity(provider: IdentityProvider, currentPassword: String) async throws -> APIResult {
        try await send("/api/account/identities/\(provider.rawValue)", method: "DELETE",
                       body: ["current_password": currentPassword])
    }

    func account() async throws -> HouseholdAccount {
        try await send("/api/account")
    }

    func createInvitation() async throws -> CreatedHouseholdInvitation {
        try await send("/api/account/invitations", method: "POST", body: EmptyBody())
    }

    func revokeInvitation(id: Int) async throws -> APIResult {
        try await send("/api/account/invitations/\(id)", method: "DELETE")
    }

    func acceptInvitation(_ input: String) async throws -> APIResult {
        try await send("/api/account/invitations/accept", method: "POST",
                       body: ["token": HouseholdInvitationInput.token(from: input)])
    }

    func invitationURL(path: String) throws -> URL {
        guard let components = URLComponents(string: path), components.scheme == nil,
              components.host == nil, components.path == "/register" else {
            throw APIError.invalidResponse("Einladungslink")
        }
        return try endpoint(components.path, query: components.queryItems ?? [])
    }

    func saveRecipe(id: Int, saved: Bool) async throws -> APIResult {
        if saved {
            return try await send("/api/recipes/\(id)/save", method: "POST", body: EmptyBody())
        }
        return try await send("/api/recipes/\(id)/save", method: "DELETE")
    }

    func sessionInfo() async throws -> SessionResponse {
        try await send("/api/auth/session")
    }

    func systemInfo() async throws -> SystemInfo {
        try await send("/api/system/info", authenticated: false)
    }

    /// Rezepte seitenweise. `manualOnly` filtert serverseitig
    /// (`needs_manual_care`), damit `total` die echte Trefferzahl bleibt und
    /// nicht nur die der geladenen Seite.
    func recipes(
        search: String = "",
        manualOnly: Bool = false,
        filters: RecipeFilters = RecipeFilters(),
        limit: Int = APIClient.pageSize,
        offset: Int = 0
    ) async throws -> RecipeListResponse {
        var query = [
            URLQueryItem(name: "limit", value: String(limit)),
            URLQueryItem(name: "offset", value: String(offset)),
        ]
        query.append(contentsOf: recipeFilterQuery(
            search: search,
            filters: filters,
            forceManualOnly: manualOnly
        ))
        return try await send("/api/recipes", query: query)
    }

    func recipeFacets(
        search: String = "",
        filters: RecipeFilters = RecipeFilters()
    ) async throws -> RecipeFacets {
        try await send(
            "/api/recipes/facets",
            query: recipeFilterQuery(search: search, filters: filters)
        )
    }

    func recipeCount(
        search: String = "",
        filters: RecipeFilters = RecipeFilters()
    ) async throws -> Int {
        let response: RecipeCountResponse = try await send(
            "/api/recipes/count",
            query: recipeFilterQuery(search: search, filters: filters)
        )
        return response.total
    }

    func recipe(id: Int) async throws -> Recipe {
        try await send("/api/recipes/\(id)")
    }

    func updateRecipeMetadata(
        id: Int,
        name: String,
        type: String,
        category: String,
        description: String,
        servings: Int?,
        url: String?
    ) async throws -> APIResult {
        try await send(
            "/api/recipes/\(id)/metadata",
            method: "PUT",
            body: RecipeMetadataPayload(
                name: name,
                type: type,
                category: category,
                description: description,
                servings: servings,
                url: url
            )
        )
    }

    func updateRecipeTags(id: Int, tags: [String]) async throws -> APIResult {
        try await send(
            "/api/recipes/\(id)/tags",
            method: "PUT",
            body: RecipeTagsPayload(tags: tags)
        )
    }

    func setRecipeRating(id: Int, value: Int) async throws -> APIResult {
        try await send(
            "/api/recipes/\(id)/rating",
            method: "POST",
            query: [URLQueryItem(name: "value", value: String(value))],
            body: EmptyBody()
        )
    }

    func setRecipeVerified(id: Int, verified: Bool) async throws -> APIResult {
        try await send(
            "/api/recipes/\(id)/verify",
            method: "POST",
            query: [URLQueryItem(name: "verified", value: String(verified))],
            body: EmptyBody()
        )
    }

    func recipeSourceIntegrity(id: Int) async throws -> RecipeSourceIntegrity {
        try await send("/api/recipes/\(id)/source-integrity")
    }

    func checkRecipeSourceIntegrity(id: Int) async throws -> RecipeSourceIntegrity {
        try await send(
            "/api/recipes/\(id)/source-integrity/check",
            method: "POST",
            body: EmptyBody(),
            timeout: 30
        )
    }

    func acceptRecipeSourceIntegrity(
        id: Int,
        expectedSnapshotID: Int,
        expectedContentSHA256: String?
    ) async throws -> RecipeSourceIntegrity {
        try await send(
            "/api/recipes/\(id)/source-integrity/accept",
            method: "POST",
            body: SourceIntegrityAcceptRequest(
                expectedSnapshotId: expectedSnapshotID,
                expectedContentSha256: expectedContentSHA256
            )
        )
    }

    func recipeSubstitutions(id: Int) async throws -> SubstitutionLab {
        try await send("/api/recipes/\(id)/substitutions")
    }

    func applyRecipeSubstitution(
        id: Int,
        ingredientID: Int,
        candidateID: String,
        variantName: String
    ) async throws -> SubstitutionApplyResponse {
        try await send(
            "/api/recipes/\(id)/substitutions/apply",
            method: "POST",
            body: SubstitutionApplyPayload(
                ingredientId: ingredientID,
                candidateId: candidateID,
                variantName: variantName
            )
        )
    }

    func duplicateRecipe(id: Int, newName: String) async throws -> DuplicateRecipeResponse {
        try await send(
            "/api/recipes/\(id)/duplicate",
            method: "POST",
            body: DuplicateRecipePayload(newName: newName)
        )
    }

    func computeRecipeNutrition(id: Int) async throws -> APIResult {
        try await send(
            "/api/recipes/\(id)/nutrition",
            method: "POST",
            body: EmptyBody(),
            timeout: 120
        )
    }

    func reextractRecipeSource(id: Int, refreshMedia: Bool = true) async throws -> APIResult {
        try await send(
            "/api/recipes/\(id)/extract",
            method: "POST",
            query: [
                URLQueryItem(
                    name: "refresh_media",
                    value: refreshMedia ? "true" : "false"
                )
            ],
            body: EmptyBody(),
            timeout: 180
        )
    }

    func translateRecipeText(id: Int, language: String, text: String? = nil) async throws -> RecipeTranslationResponse {
        try await send(
            "/api/recipes/\(id)/translate",
            method: "POST",
            body: TranslationPayload(targetLanguage: language, text: text),
            timeout: 120
        )
    }

    func recipePDF(id: Int) async throws -> Data {
        try await download("/api/recipes/\(id)/pdf", accept: "application/pdf")
    }

    func deleteRecipe(id: Int) async throws -> APIResult {
        try await send(
            "/api/recipes/\(id)",
            method: "DELETE",
            query: [URLQueryItem(name: "delete_files", value: "true")]
        )
    }

    func toggleFavorite(id: Int) async throws -> APIResult {
        try await send("/api/recipes/\(id)/favorite", method: "POST", body: EmptyBody())
    }

    func updateIngredients(id: Int, ingredients: [IngredientDraft]) async throws -> APIResult {
        try await send(
            "/api/recipes/\(id)/ingredients",
            method: "PUT",
            body: IngredientsPayload(ingredients: ingredients)
        )
    }

    func updateSteps(id: Int, steps: [StepDraft]) async throws -> APIResult {
        try await send(
            "/api/recipes/\(id)/steps",
            method: "PUT",
            body: StepsPayload(steps: steps)
        )
    }

    func cookingProgress(id: Int) async throws -> CookingProgress {
        try await send("/api/recipes/\(id)/cooking-progress")
    }

    func updateCookingProgress(
        id: Int,
        completedSteps: [Int],
        activeStep: Int,
        servings: Int
    ) async throws -> CookingProgress {
        try await send(
            "/api/recipes/\(id)/cooking-progress",
            method: "PUT",
            body: CookingProgressPayload(
                completedSteps: completedSteps,
                activeStep: activeStep,
                servings: servings
            )
        )
    }

    func clearCookingProgress(id: Int) async throws -> APIResult {
        try await send("/api/recipes/\(id)/cooking-progress", method: "DELETE")
    }

    func completeCooking(
        id: Int,
        servings: Int,
        idempotencyKey: String
    ) async throws -> CookingCompletionResult {
        try await send(
            "/api/recipes/\(id)/cooking-complete",
            method: "POST",
            body: CookingCompletePayload(servings: servings),
            headers: ["Idempotency-Key": idempotencyKey]
        )
    }

    func addRecipeToCart(id: Int, servings: Int) async throws -> APIResult {
        try await send(
            "/api/cart/cook/\(id)",
            method: "POST",
            body: CookPayload(servings: servings)
        )
    }

    func cart() async throws -> CartResponse {
        try await send("/api/cart")
    }

    func addCartItem(
        name: String,
        amount: Double? = nil,
        unit: String? = nil,
        category: String? = nil
    ) async throws -> APIResult {
        try await send(
            "/api/cart/add",
            method: "POST",
            body: AddCartPayload(name: name, amount: amount, unit: unit, category: category)
        )
    }

    func shoppingSuggestions(query: String, limit: Int = 8) async throws -> ShoppingSuggestionsResponse {
        try await send(
            "/api/cart/suggestions",
            query: [
                URLQueryItem(name: "q", value: query),
                URLQueryItem(name: "limit", value: String(limit)),
            ]
        )
    }

    func shoppingCategories() async throws -> ShoppingCategoriesResponse {
        try await send("/api/cart/categories")
    }

    func shoppingOptimizationPreview() async throws -> ShoppingOptimizePreview {
        try await send(
            "/api/cart/optimize/preview",
            method: "POST",
            body: EmptyBody(),
            timeout: 120
        )
    }

    func applyShoppingOptimization(previewID: String) async throws -> ShoppingOptimizeApplyResponse {
        try await send(
            "/api/cart/optimize/apply",
            method: "POST",
            body: OptimizeApplyPayload(previewId: previewID)
        )
    }

    func shoppingExportText() async throws -> String {
        let data = try await download("/api/cart/export.txt", accept: "text/plain")
        guard let value = String(data: data, encoding: .utf8) else {
            throw APIError.invalidResponse("/api/cart/export.txt")
        }
        return value
    }

    func pushShoppingToEinkauf() async throws -> ShoppingPushResponse {
        try await send(
            "/api/cart/push-to-einkauf",
            method: "POST",
            body: ShoppingPushPayload(consolidate: true, onlyUnchecked: true, clearAfter: false),
            timeout: 120
        )
    }

    func setCartItem(id: Int, checked: Bool) async throws -> APIResult {
        try await send(
            "/api/cart/\(id)",
            method: "PATCH",
            body: CartUpdatePayload(checked: checked)
        )
    }

    func updateCartItemAmount(id: Int, amount: Double) async throws -> APIResult {
        guard amount.isFinite, amount > 0 else { throw CartAmountError.invalidAmount }
        return try await send(
            "/api/cart/\(id)",
            method: "PATCH",
            body: CartAmountUpdatePayload(amount: amount)
        )
    }

    func deleteCartItem(id: Int) async throws -> APIResult {
        try await send("/api/cart/\(id)", method: "DELETE")
    }

    func clearCart(onlyChecked: Bool) async throws -> APIResult {
        try await send(
            "/api/cart/clear",
            method: "POST",
            body: ClearCartPayload(onlyChecked: onlyChecked)
        )
    }

    func recurringCart() async throws -> RecurringCartResponse {
        try await send("/api/cart/recurring")
    }

    func createRecurringCartItem(
        name: String,
        amount: Double?,
        unit: String?,
        category: String?,
        intervalDays: Int,
        nextDueOn: String,
        active: Bool
    ) async throws -> APIResult {
        try await send(
            "/api/cart/recurring",
            method: "POST",
            body: RecurringCartPayload(
                name: name,
                amount: amount,
                defaultUnit: unit,
                category: category,
                intervalDays: intervalDays,
                nextDueOn: nextDueOn,
                active: active
            )
        )
    }

    func updateRecurringCartItem(
        id: Int,
        name: String,
        amount: Double?,
        unit: String?,
        category: String?,
        intervalDays: Int,
        nextDueOn: String,
        active: Bool
    ) async throws -> APIResult {
        try await send(
            "/api/cart/recurring/\(id)",
            method: "PATCH",
            body: RecurringCartPayload(
                name: name,
                amount: amount,
                defaultUnit: unit,
                category: category,
                intervalDays: intervalDays,
                nextDueOn: nextDueOn,
                active: active
            )
        )
    }

    func setRecurringCartItem(id: Int, active: Bool) async throws -> APIResult {
        try await send(
            "/api/cart/recurring/\(id)",
            method: "PATCH",
            body: RecurringActivePayload(active: active)
        )
    }

    func deleteRecurringCartItem(id: Int) async throws -> APIResult {
        try await send("/api/cart/recurring/\(id)", method: "DELETE")
    }

    func runRecurringCart() async throws -> RecurringRunResponse {
        try await send("/api/cart/recurring/run", method: "POST", body: EmptyBody())
    }

    func mealWeek(start: String? = nil) async throws -> MealWeek {
        let query = start.map { [URLQueryItem(name: "week_start", value: $0)] } ?? []
        return try await send("/api/meal-plan", query: query)
    }

    func addMeal(date: String, recipeID: Int, servings: Int) async throws -> APIResult {
        try await send(
            "/api/meal-plan/items",
            method: "POST",
            body: AddMealPayload(plannedFor: date, recipeId: recipeID, plannedServings: servings)
        )
    }

    func updateMeal(id: Int, servings: Int) async throws -> APIResult {
        try await send(
            "/api/meal-plan/items/\(id)",
            method: "PATCH",
            body: UpdateMealPayload(plannedServings: servings)
        )
    }

    func deleteMeal(id: Int) async throws -> APIResult {
        try await send("/api/meal-plan/items/\(id)", method: "DELETE")
    }

    func createWeekCart(start: String) async throws -> APIResult {
        try await send(
            "/api/meal-plan/cart",
            method: "POST",
            body: WeekCartPayload(weekStart: start)
        )
    }

    func mealPlanPDF(start: String) async throws -> Data {
        try await download(
            "/api/meal-plan/pdf",
            query: [URLQueryItem(name: "week_start", value: start)],
            accept: "application/pdf"
        )
    }

    func mealConductorPreview(
        date: String,
        serveAt: String,
        activeCooks: Int = 1,
        burners: Int,
        ovenSlots: Int,
        readOnly: Bool = false
    ) async throws -> MealConductorPlan {
        if readOnly {
            return try await send(
                "/api/meal-plan/conductor/preview",
                query: [
                    URLQueryItem(name: "planned_for", value: date),
                    URLQueryItem(name: "serve_at", value: serveAt),
                    URLQueryItem(name: "active_cooks", value: String(activeCooks)),
                    URLQueryItem(name: "burners", value: String(burners)),
                    URLQueryItem(name: "oven_slots", value: String(ovenSlots)),
                ]
            )
        }
        return try await send(
            "/api/meal-plan/conductor/preview",
            method: "POST",
            body: MealConductorPayload(
                plannedFor: date,
                serveAt: serveAt,
                activeCooks: activeCooks,
                burners: burners,
                ovenSlots: ovenSlots
            )
        )
    }

    func adminOverview() async throws -> AdminOverview {
        try await send("/api/admin/overview")
    }

    func adminConfiguration() async throws -> NativeAdminConfig {
        try await send("/api/config")
    }

    func updateAdminConfiguration(_ patch: NativeAdminConfigPatch) async throws -> APIResult {
        try await send(
            "/api/config",
            method: "PUT",
            body: patch
        )
    }

    func reloadAdminConfiguration() async throws -> APIResult {
        try await send("/api/config/reload", method: "POST", body: EmptyBody())
    }

    func adminSchedule() async throws -> NativeAdminScheduleStatus {
        try await send("/api/schedule")
    }

    func previewAdminSchedule(_ value: String) async throws -> NativeAdminSchedulePreview {
        try await send(
            "/api/schedule/preview",
            method: "POST",
            body: SchedulePayload(scraper: value)
        )
    }

    func updateAdminSchedule(_ value: String) async throws -> APIResult {
        try await send(
            "/api/schedule",
            method: "PUT",
            body: SchedulePayload(scraper: value),
            timeout: 120
        )
    }

    func testOpenAIConfiguration(apiKey: String?, model: String?) async throws -> NativeAdminTestResult {
        try await send(
            "/api/test/openai",
            method: "POST",
            body: OpenAITestPayload(apiKey: apiKey, model: model)
        )
    }

    func testMailConfiguration(account: String) async throws -> NativeAdminTestResult {
        try await send(
            "/api/test/mail",
            method: "POST",
            body: MailTestPayload(account: account),
            timeout: 120
        )
    }

    func adminLogStats() async throws -> NativeAdminLogStats {
        try await send("/api/config/logs/stats")
    }

    func cleanupAdminLogs(days: Int? = nil) async throws -> NativeAdminOperationResult {
        try await send(
            "/api/config/logs/cleanup",
            method: "POST",
            query: days.map { [URLQueryItem(name: "days", value: String($0))] } ?? [],
            body: EmptyBody(),
            timeout: 120
        )
    }

    func adminBackups() async throws -> NativeAdminBackupList {
        try await send("/api/config/backups/list")
    }

    func runAdminBackup() async throws -> NativeAdminOperationResult {
        try await send(
            "/api/config/backups/run-now",
            method: "POST",
            body: EmptyBody(),
            timeout: 300
        )
    }

    func trash() async throws -> TrashResponse {
        try await send("/api/recipes/trash/list")
    }

    func restoreTrashRecipe(id: Int) async throws -> APIResult {
        try await send("/api/recipes/\(id)/restore", method: "POST", body: EmptyBody())
    }

    func purgeTrashRecipe(id: Int) async throws -> APIResult {
        try await send(
            "/api/recipes/\(id)",
            method: "DELETE",
            query: [
                URLQueryItem(name: "delete_files", value: "true"),
                URLQueryItem(name: "hard", value: "true")
            ],
            body: EmptyBody()
        )
    }

    func emptyTrash() async throws -> APIResult {
        try await send(
            "/api/recipes/trash/empty",
            method: "DELETE",
            query: [URLQueryItem(name: "delete_files", value: "true")],
            body: EmptyBody()
        )
    }

    func recipeVersions(recipeID: Int? = nil) async throws -> RecipeVersionsResponse {
        let query = recipeID.map { [URLQueryItem(name: "recipe_id", value: String($0))] } ?? []
        return try await send("/api/admin/versions", query: query)
    }

    func restoreRecipeVersion(id: Int) async throws -> APIResult {
        try await send("/api/admin/versions/\(id)/restore", method: "POST", body: EmptyBody())
    }

    func auditFindings() async throws -> AuditFindingsResponse {
        try await send("/api/audit/ai-sanity/findings")
    }

    func startAudit() async throws -> APIResult {
        try await send("/api/audit/ai-sanity", method: "POST", body: EmptyBody())
    }

    func applyAuditFinding(id: Int) async throws -> APIResult {
        try await send("/api/audit/finding/\(id)/apply", method: "POST", body: EmptyBody())
    }

    func resolveAuditFinding(id: Int) async throws -> APIResult {
        try await send("/api/audit/finding/\(id)/resolve", method: "POST", body: EmptyBody())
    }

    func createRecipeShare(id: Int, expiresDays: Int) async throws -> ShareLinkResponse {
        try await send(
            "/api/recipes/\(id)/share",
            method: "POST",
            body: SharePayload(expiresDays: expiresDays)
        )
    }

    func recipeShares(id: Int) async throws -> ShareLinksResponse {
        try await send("/api/recipes/\(id)/shares")
    }

    func revokeRecipeShare(recipeID: Int, shareID: String) async throws -> APIResult {
        try await send(
            "/api/recipes/\(recipeID)/shares/\(shareID)",
            method: "DELETE",
            body: EmptyBody()
        )
    }

    func pending(visibility: String? = nil) async throws -> [PendingItem] {
        try await send("/api/pending", query: visibility.map { [URLQueryItem(name: "visibility", value: $0)] } ?? [])
    }

    func failedDownloads() async throws -> [FailedDownload] {
        try await send("/api/pending/failed")
    }

    func importURL(_ url: String, visibility: String = "private") async throws -> APIResult {
        try await send(
            "/api/pending/import-url",
            method: "POST",
            body: ImportPayload(url: url, type: "recipe", visibility: visibility)
        )
    }

    func importFile(data: Data, filename: String, mimeType: String, visibility: String = "private") async throws -> APIResult {
        let boundary = "RezepteBoundary-\(UUID().uuidString)"
        var body = Data()
        body.append("--\(boundary)\r\n")
        body.append("Content-Disposition: form-data; name=\"visibility\"\r\n\r\n\(visibility)\r\n")
        body.append("--\(boundary)\r\n")
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n")
        body.append("Content-Type: \(mimeType)\r\n\r\n")
        body.append(data)
        body.append("\r\n--\(boundary)--\r\n")

        var request = URLRequest(url: try endpoint("/api/pending/import-file"))
        request.httpMethod = "POST"
        request.timeoutInterval = 180
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.httpBody = body
        authorize(&request, includeBearer: true)
        return try await execute(request)
    }

    func scanPendingPhoto(
        url: String,
        data: Data,
        filename: String,
        mimeType: String
    ) async throws -> PendingAnalysisResult {
        let boundary = "RezepteBoundary-\(UUID().uuidString)"
        let safeFilename = filename
            .replacingOccurrences(of: "\"", with: "_")
            .replacingOccurrences(of: "\r", with: "_")
            .replacingOccurrences(of: "\n", with: "_")
        var body = Data()
        body.append("--\(boundary)\r\n")
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"\(safeFilename)\"\r\n")
        body.append("Content-Type: \(mimeType)\r\n\r\n")
        body.append(data)
        body.append("\r\n--\(boundary)--\r\n")

        var request = URLRequest(url: try endpoint(
            "/api/pending/scan-photo",
            query: [URLQueryItem(name: "url", value: url)]
        ))
        request.httpMethod = "POST"
        request.timeoutInterval = 180
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.httpBody = body
        authorize(&request, includeBearer: true)
        return try await execute(request)
    }

    func resolvePending(
        url: String,
        action: String,
        name: String? = nil,
        type: String? = nil,
        category: String? = nil,
        description: String? = nil,
        ingredients: [PendingIngredient]? = nil,
        steps: [PendingStep]? = nil,
        servings: Int? = nil,
        verified: Bool = false
    ) async throws -> APIResult {
        try await send(
            "/api/pending",
            method: "POST",
            body: ResolvePendingPayload(
                url: url,
                action: action,
                name: name,
                type: type,
                category: category,
                description: description,
                ingredients: ingredients,
                steps: steps,
                servings: servings,
                verified: verified
            )
        )
    }

    func reanalyzePending(url: String) async throws -> PendingAnalysisResult {
        try await send(
            "/api/pending/reanalyze",
            method: "POST",
            body: PendingURLPayload(url: url),
            timeout: 120
        )
    }

    func retryFailedDownload(url: String) async throws -> APIResult {
        try await send(
            "/api/pending/failed/retry",
            method: "POST",
            body: FailedDownloadPayload(url: url)
        )
    }

    func discardFailedDownload(url: String) async throws -> APIResult {
        try await send(
            "/api/pending/failed/discard",
            method: "POST",
            body: FailedDownloadPayload(url: url)
        )
    }

    func runScraper() async throws -> APIResult {
        try await send("/api/jobs/scraper/run", method: "POST", body: EmptyBody())
    }

    func generateRecipeImage(id: Int) async throws -> ImageGenerationStart {
        try await send(
            "/api/recipes/\(id)/generate-image",
            method: "POST",
            body: EmptyBody()
        )
    }

    func imageBackups(recipeID: Int) async throws -> ImageBackupResponse {
        try await send(
            "/api/recipes/images/backups",
            query: [URLQueryItem(name: "recipe_id", value: String(recipeID))]
        )
    }

    func restoreImageBackup(id: Int) async throws -> APIResult {
        try await send(
            "/api/recipes/image-backups/\(id)/restore",
            method: "POST",
            body: EmptyBody()
        )
    }

    func startImageBackfill() async throws -> ImageBackfillStart {
        try await send(
            "/api/recipes/images/backfill",
            method: "POST",
            body: EmptyBody()
        )
    }

    func imageBackfillStatus(runID: Int) async throws -> ImageBackfillRun {
        try await send("/api/recipes/images/backfill/\(runID)")
    }

    private func send<Response: Decodable>(
        _ path: String,
        method: String = "GET",
        query: [URLQueryItem] = [],
        authenticated: Bool = true
    ) async throws -> Response {
        var request = URLRequest(url: try endpoint(path, query: query))
        request.httpMethod = method
        request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        authorize(&request, includeBearer: authenticated)
        return try await execute(request, authenticated: authenticated)
    }

    private func send<Body: Encodable, Response: Decodable>(
        _ path: String,
        method: String,
        query: [URLQueryItem] = [],
        body: Body,
        authenticated: Bool = true,
        timeout: TimeInterval = 60,
        headers: [String: String] = [:]
    ) async throws -> Response {
        var request = URLRequest(url: try endpoint(path, query: query))
        request.httpMethod = method
        request.timeoutInterval = timeout
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try encoder.encode(body)
        for (field, value) in headers {
            request.setValue(value, forHTTPHeaderField: field)
        }
        authorize(&request, includeBearer: authenticated)
        return try await execute(request, authenticated: authenticated)
    }

    private func download(
        _ path: String,
        query: [URLQueryItem] = [],
        accept: String
    ) async throws -> Data {
        var request = URLRequest(url: try endpoint(path, query: query))
        request.httpMethod = "GET"
        request.timeoutInterval = 60
        request.setValue(accept, forHTTPHeaderField: "Accept")
        authorize(&request, includeBearer: true)
        let requestConfiguration = configurationID
        let (data, response) = try await session.data(for: request)
        guard configurationID == requestConfiguration else { throw APIError.sessionChanged }
        try Task.checkCancellation()
        guard let http = response as? HTTPURLResponse else {
            throw APIError.invalidResponse(path)
        }
        if http.statusCode == 401 { throw APIError.unauthenticated }
        guard (200..<300).contains(http.statusCode) else {
            let detail = (try? decoder.decode(ErrorResponse.self, from: data).detail)
                ?? HTTPURLResponse.localizedString(forStatusCode: http.statusCode)
            throw APIError.server(http.statusCode, detail)
        }
        guard !Self.isHTMLResponse(http, body: data) else {
            throw APIError.invalidResponse(path)
        }
        return data
    }

    private func authorize(_ request: inout URLRequest, includeBearer: Bool) {
        request.cachePolicy = .reloadIgnoringLocalCacheData
        if includeBearer, let token, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
    }

    private func recipeFilterQuery(
        search: String,
        filters: RecipeFilters,
        forceManualOnly: Bool = false
    ) -> [URLQueryItem] {
        var query: [URLQueryItem] = []
        if filters.library != .all {
            query.append(URLQueryItem(name: "library", value: filters.library.rawValue))
        }
        let cleanSearch = search.trimmingCharacters(in: .whitespacesAndNewlines)
        if !cleanSearch.isEmpty {
            query.append(URLQueryItem(name: "search", value: cleanSearch))
        }
        if !filters.type.isEmpty {
            query.append(URLQueryItem(name: "type", value: filters.type))
        }
        if !filters.category.isEmpty {
            query.append(URLQueryItem(name: "category", value: filters.category))
        }
        if filters.favoriteOnly {
            query.append(URLQueryItem(name: "favorite_only", value: "true"))
        }
        if filters.minRating > 0 {
            query.append(URLQueryItem(name: "min_rating", value: String(filters.minRating)))
        }
        if filters.manualOnly || forceManualOnly {
            query.append(URLQueryItem(name: "needs_manual_care", value: "true"))
        }
        for id in filters.tagIDs.union(filters.allergenTagIDs).sorted() {
            query.append(URLQueryItem(name: "tag_id", value: String(id)))
        }
        for ingredient in filters.includedIngredients.sorted() {
            query.append(URLQueryItem(name: "ingredient", value: ingredient))
        }
        for ingredient in filters.excludedIngredients.sorted() {
            query.append(URLQueryItem(name: "exclude_ingredient", value: ingredient))
        }
        return query
    }

    private func execute<Response: Decodable>(
        _ request: URLRequest,
        authenticated: Bool = true
    ) async throws -> Response {
        let requestConfiguration = configurationID
        let (data, response) = try await session.data(for: request)
        guard configurationID == requestConfiguration else { throw APIError.sessionChanged }
        try Task.checkCancellation()
        let endpoint = request.url?.path.nilIfEmpty ?? "diese Anfrage"
        guard let http = response as? HTTPURLResponse else {
            throw APIError.invalidResponse(endpoint)
        }
        if http.statusCode == 401 && authenticated { throw APIError.unauthenticated }
        guard (200..<300).contains(http.statusCode) else {
            let detail = (try? decoder.decode(ErrorResponse.self, from: data).detail)
                ?? HTTPURLResponse.localizedString(forStatusCode: http.statusCode)
            throw APIError.server(http.statusCode, detail)
        }
        guard !Self.isHTMLResponse(http, body: data) else {
            throw APIError.invalidResponse(endpoint)
        }
        do {
            return try decoder.decode(Response.self, from: data)
        } catch {
            // Kein Response-Body wird geloggt: Er könnte persönliche Rezept-
            // oder Kontodaten enthalten. Der konkrete Pfad reicht, um einen
            // App-/Server-Versionskonflikt gezielt zu erkennen.
            throw APIError.invalidResponse(endpoint)
        }
    }

    private static func isHTMLResponse(
        _ response: HTTPURLResponse,
        body: Data
    ) -> Bool {
        let contentType = response.value(forHTTPHeaderField: "Content-Type")?.lowercased() ?? ""
        if contentType.contains("text/html") || contentType.contains("application/xhtml+xml") {
            return true
        }
        // Auch falsch deklarierte Proxy-/Anmeldeseiten dürfen nicht als
        // Rezeptdaten oder PDF gespeichert werden.
        let snippet = String(decoding: body.prefix(512), as: UTF8.self)
            .trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return snippet.hasPrefix("<!doctype html") || snippet.hasPrefix("<html")
    }
}

private struct ErrorResponse: Codable { let detail: String }
private struct EmptyBody: Codable {}
private struct RecipeMetadataPayload: Codable {
    let name: String
    let type: String
    let category: String
    let description: String
    let servings: Int?
    let url: String?
}
private struct RecipeTagsPayload: Codable { let tags: [String] }
private struct DuplicateRecipePayload: Codable { let newName: String }
private struct SubstitutionApplyPayload: Codable {
    let ingredientId: Int
    let candidateId: String
    let variantName: String
}
private struct OptimizeApplyPayload: Codable { let previewId: String }
private struct ShoppingPushPayload: Codable {
    let consolidate: Bool
    let onlyUnchecked: Bool
    let clearAfter: Bool
}
private struct SharePayload: Codable { let expiresDays: Int }
private struct TranslationPayload: Codable { let targetLanguage: String; let text: String? }
private struct OpenAITestPayload: Codable { let apiKey: String?; let model: String? }
private struct MailTestPayload: Codable { let account: String }
private struct SchedulePayload: Codable { let scraper: String }
struct IngredientDraft: Codable, Hashable {
    let name: String
    let amount: Double?
    let unit: String?
}
private struct IngredientsPayload: Codable { let ingredients: [IngredientDraft] }
struct StepDraft: Codable, Hashable {
    let instruction: String
    let timerSeconds: Int?
}
private struct StepsPayload: Codable { let steps: [StepDraft] }
private struct CookingProgressPayload: Codable {
    let completedSteps: [Int]
    let activeStep: Int
    let servings: Int
}
private struct CookingCompletePayload: Codable { let servings: Int }
private struct CookPayload: Codable { let servings: Int }
private struct AddCartPayload: Codable {
    let name: String
    let amount: Double?
    let unit: String?
    let category: String?
}
private struct CartUpdatePayload: Codable { let checked: Bool }
private struct CartAmountUpdatePayload: Codable { let amount: Double }
private struct ClearCartPayload: Codable { let onlyChecked: Bool }
private struct RecurringCartPayload: Codable {
    let name: String
    let amount: Double?
    let defaultUnit: String?
    let category: String?
    let intervalDays: Int
    let nextDueOn: String
    let active: Bool
}
private struct RecurringActivePayload: Codable { let active: Bool }
private struct AddMealPayload: Codable { let plannedFor: String; let recipeId: Int; let plannedServings: Int }
private struct UpdateMealPayload: Codable { let plannedServings: Int }
private struct WeekCartPayload: Codable { let weekStart: String }
private struct MealConductorPayload: Codable {
    let plannedFor: String
    let serveAt: String
    let activeCooks: Int
    let burners: Int
    let ovenSlots: Int
}
private struct ImportPayload: Codable { let url: String; let type: String; let visibility: String }
private struct RegistrationPayload: Encodable {
    let username: String
    let password: String
    let invitationToken: String
}
private struct ResolvePendingPayload: Codable {
    let url: String
    let action: String
    let name: String?
    let type: String?
    let category: String?
    let description: String?
    let ingredients: [PendingIngredient]?
    let steps: [PendingStep]?
    let servings: Int?
    let verified: Bool
}
private struct PendingURLPayload: Codable { let url: String }
private struct FailedDownloadPayload: Codable { let url: String }

private extension Data {
    mutating func append(_ string: String) {
        append(Data(string.utf8))
    }
}
