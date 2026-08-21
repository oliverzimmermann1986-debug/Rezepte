import Foundation

enum APIError: LocalizedError {
    case invalidServer
    case insecureServer
    case incompleteCloudflareCredentials
    case cloudflareAccessRequired
    case unauthenticated
    case server(Int, String)
    case invalidResponse

    var errorDescription: String? {
        switch self {
        case .invalidServer:
            return "Die Serveradresse ist ungültig."
        case .insecureServer:
            return "Bitte eine HTTPS-Adresse verwenden."
        case .incompleteCloudflareCredentials:
            return "Für Cloudflare Access werden Client-ID und Client-Secret benötigt."
        case .cloudflareAccessRequired:
            return "Cloudflare Access hat den Gerätezugang abgelehnt. Bitte Client-ID und Client-Secret prüfen."
        case .unauthenticated:
            return "Die Sitzung ist abgelaufen. Bitte erneut anmelden."
        case let .server(_, message):
            return message
        case .invalidResponse:
            return "Der Server hat eine unerwartete Antwort gesendet."
        }
    }
}

struct CloudflareAccessCredentials: Equatable {
    let clientID: String
    let clientSecret: String

    init?(clientID: String, clientSecret: String) throws {
        let cleanID = clientID.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanSecret = clientSecret.trimmingCharacters(in: .whitespacesAndNewlines)
        if cleanID.isEmpty && cleanSecret.isEmpty { return nil }
        guard !cleanID.isEmpty, !cleanSecret.isEmpty else {
            throw APIError.incompleteCloudflareCredentials
        }
        self.clientID = cleanID
        self.clientSecret = cleanSecret
    }
}

actor APIClient {
    /// Seitengröße der Rezeptliste (entspricht dem Server-Default).
    static let pageSize = 60

    private var baseURL: URL?
    private var token: String?
    private var cloudflareCredentials: CloudflareAccessCredentials?
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
        cloudflareCredentials: CloudflareAccessCredentials? = nil
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
        baseURL = url
        self.token = token
        self.cloudflareCredentials = cloudflareCredentials
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

    func imageRequest(recipeID: Int, width: Int = 900) throws -> URLRequest {
        var request = URLRequest(url: try endpoint(
            "/api/recipes/\(recipeID)/thumb",
            query: [URLQueryItem(name: "w", value: String(width))]
        ))
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

    func sessionInfo() async throws -> SessionResponse {
        try await send("/api/auth/session")
    }

    /// Rezepte seitenweise. `manualOnly` filtert serverseitig
    /// (`needs_manual_care`), damit `total` die echte Trefferzahl bleibt und
    /// nicht nur die der geladenen Seite.
    func recipes(
        search: String = "",
        manualOnly: Bool = false,
        limit: Int = APIClient.pageSize,
        offset: Int = 0
    ) async throws -> RecipeListResponse {
        var query = [
            URLQueryItem(name: "limit", value: String(limit)),
            URLQueryItem(name: "offset", value: String(offset)),
        ]
        if !search.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            query.append(URLQueryItem(name: "search", value: search))
        }
        if manualOnly {
            query.append(URLQueryItem(name: "needs_manual_care", value: "true"))
        }
        return try await send("/api/recipes", query: query)
    }

    func recipe(id: Int) async throws -> Recipe {
        try await send("/api/recipes/\(id)")
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

    func addRecipeToCart(id: Int, multiplier: Double = 1) async throws -> APIResult {
        try await send(
            "/api/cart/cook/\(id)",
            method: "POST",
            body: CookPayload(multiplier: multiplier)
        )
    }

    func cart() async throws -> CartResponse {
        try await send("/api/cart")
    }

    func addCartItem(name: String, amount: Double? = nil, unit: String? = nil) async throws -> APIResult {
        try await send(
            "/api/cart/add",
            method: "POST",
            body: AddCartPayload(name: name, amount: amount, unit: unit)
        )
    }

    func setCartItem(id: Int, checked: Bool) async throws -> APIResult {
        try await send(
            "/api/cart/\(id)",
            method: "PATCH",
            body: CartUpdatePayload(checked: checked)
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

    func adminOverview() async throws -> AdminOverview {
        try await send("/api/admin/overview")
    }

    func pending() async throws -> [PendingItem] {
        try await send("/api/pending")
    }

    func importURL(_ url: String) async throws -> APIResult {
        try await send(
            "/api/pending/import-url",
            method: "POST",
            body: ImportPayload(url: url, type: "recipe")
        )
    }

    func runScraper() async throws -> APIResult {
        try await send("/api/jobs/scraper/run", method: "POST", body: EmptyBody())
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
        return try await execute(request)
    }

    private func send<Body: Encodable, Response: Decodable>(
        _ path: String,
        method: String,
        body: Body,
        authenticated: Bool = true
    ) async throws -> Response {
        var request = URLRequest(url: try endpoint(path))
        request.httpMethod = method
        request.timeoutInterval = 60
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try encoder.encode(body)
        authorize(&request, includeBearer: authenticated)
        return try await execute(request)
    }

    private func authorize(_ request: inout URLRequest, includeBearer: Bool) {
        if let cloudflareCredentials {
            request.setValue(
                cloudflareCredentials.clientID,
                forHTTPHeaderField: "CF-Access-Client-Id"
            )
            request.setValue(
                cloudflareCredentials.clientSecret,
                forHTTPHeaderField: "CF-Access-Client-Secret"
            )
        }
        if includeBearer, let token, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
    }

    private func execute<Response: Decodable>(_ request: URLRequest) async throws -> Response {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }
        if Self.isCloudflareAccessResponse(http) {
            throw APIError.cloudflareAccessRequired
        }
        if http.statusCode == 401 { throw APIError.unauthenticated }
        guard (200..<300).contains(http.statusCode) else {
            let detail = (try? decoder.decode(ErrorResponse.self, from: data).detail)
                ?? HTTPURLResponse.localizedString(forStatusCode: http.statusCode)
            throw APIError.server(http.statusCode, detail)
        }
        do {
            return try decoder.decode(Response.self, from: data)
        } catch {
            throw APIError.invalidResponse
        }
    }

    private static func isCloudflareAccessResponse(_ response: HTTPURLResponse) -> Bool {
        let responseHost = response.url?.host?.lowercased() ?? ""
        if responseHost == "cloudflareaccess.com"
            || responseHost.hasSuffix(".cloudflareaccess.com") {
            return true
        }

        let authenticate = response.value(forHTTPHeaderField: "WWW-Authenticate")?.lowercased() ?? ""
        if authenticate.contains("cloudflare-access") { return true }

        let location = response.value(forHTTPHeaderField: "Location")?.lowercased() ?? ""
        if location.contains("cloudflareaccess.com/cdn-cgi/access/login") { return true }

        return response.value(forHTTPHeaderField: "cf-mitigated")?.lowercased() == "challenge"
    }
}

private struct ErrorResponse: Codable { let detail: String }
private struct EmptyBody: Codable {}
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
private struct CookPayload: Codable { let multiplier: Double }
private struct AddCartPayload: Codable { let name: String; let amount: Double?; let unit: String? }
private struct CartUpdatePayload: Codable { let checked: Bool }
private struct ClearCartPayload: Codable { let onlyChecked: Bool }
private struct AddMealPayload: Codable { let plannedFor: String; let recipeId: Int; let plannedServings: Int }
private struct UpdateMealPayload: Codable { let plannedServings: Int }
private struct WeekCartPayload: Codable { let weekStart: String }
private struct ImportPayload: Codable { let url: String; let type: String }
