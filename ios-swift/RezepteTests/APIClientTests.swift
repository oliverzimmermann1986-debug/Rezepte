import Foundation
import XCTest
@testable import Rezepte

final class APIClientTests: XCTestCase {
    func testNormalizesServerURL() {
        XCTAssertEqual(
            APIClient.normalizedServerURL(" https://rezepte.example.de/ ")?.absoluteString,
            "https://rezepte.example.de"
        )
    }

    func testKeepsServerBasePath() {
        XCTAssertEqual(
            APIClient.normalizedServerURL("https://example.de/rezepte/")?.absoluteString,
            "https://example.de/rezepte"
        )
    }

    func testRejectsUnsupportedSchemes() {
        XCTAssertNil(APIClient.normalizedServerURL("ftp://example.de"))
        XCTAssertNil(APIClient.normalizedServerURL("example.de"))
    }

    func testEndpointPreservesServerBasePath() async throws {
        let client = APIClient()
        try await client.configure(server: "https://example.de/rezepte", token: "token")
        let url = try await client.endpoint(
            "/api/recipes",
            query: [URLQueryItem(name: "limit", value: "20")]
        )
        XCTAssertEqual(
            url.absoluteString,
            "https://example.de/rezepte/api/recipes?limit=20"
        )
    }

    func testRecipeListDecodesManualCareState() throws {
        let json = """
        {
          "total": 1,
          "items": [{
            "id": 42,
            "name": "Pasta",
            "type": "recipe",
            "category": null,
            "url": "https://www.tiktok.com/example",
            "is_favorite": false,
            "rating": 0,
            "servings": 2,
            "ingredients_count": 3,
            "steps_count": 0,
            "needs_manual_care": true,
            "description": ""
          }]
        }
        """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let response = try decoder.decode(RecipeListResponse.self, from: Data(json.utf8))

        XCTAssertEqual(response.items.first?.stepsCount, 0)
        XCTAssertEqual(response.items.first?.needsManualCare, true)
        XCTAssertEqual(response.items.first?.url, "https://www.tiktok.com/example")
    }

    func testRejectsPlainHTTPServer() async {
        let client = APIClient()
        do {
            try await client.configure(server: "http://192.168.1.20:8000", token: nil)
            XCTFail("http darf in keiner Build-Konfiguration akzeptiert werden")
        } catch let error as APIError {
            guard case .insecureServer = error else {
                return XCTFail("Erwartet insecureServer, war \(error)")
            }
        } catch {
            XCTFail("Unerwarteter Fehler: \(error)")
        }
    }

    func testCloudflareCredentialsRequireBothValues() throws {
        XCTAssertNil(try CloudflareAccessCredentials(clientID: "", clientSecret: ""))
        XCTAssertThrowsError(
            try CloudflareAccessCredentials(clientID: "client-id", clientSecret: "")
        ) { error in
            guard let apiError = error as? APIError,
                  case .incompleteCloudflareCredentials = apiError else {
                return XCTFail("Erwartet incompleteCloudflareCredentials, war \(error)")
            }
        }
    }

    func testCloudflareHeadersAreSentOnNativeLogin() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        let cloudflare = try XCTUnwrap(
            CloudflareAccessCredentials(clientID: "device.access", clientSecret: "device-secret")
        )
        try await client.configure(
            server: "https://example.de",
            token: nil,
            cloudflareCredentials: cloudflare
        )
        MockURLProtocol.respond(json: """
        {"token":"api-token","username":"oliver","expires_in":1209600}
        """)

        let response = try await client.login(username: "oliver", password: "password")

        XCTAssertEqual(response.token, "api-token")
        XCTAssertEqual(MockURLProtocol.lastHeader("CF-Access-Client-Id"), "device.access")
        XCTAssertEqual(MockURLProtocol.lastHeader("CF-Access-Client-Secret"), "device-secret")
        XCTAssertNil(MockURLProtocol.lastHeader("Authorization"))
    }

    func testCloudflareAndBearerHeadersAreSentTogether() async throws {
        let client = APIClient()
        let cloudflare = try XCTUnwrap(
            CloudflareAccessCredentials(clientID: "device.access", clientSecret: "device-secret")
        )
        try await client.configure(
            server: "https://example.de",
            token: "api-token",
            cloudflareCredentials: cloudflare
        )

        let request = try await client.imageRequest(recipeID: 42)

        XCTAssertEqual(request.value(forHTTPHeaderField: "CF-Access-Client-Id"), "device.access")
        XCTAssertEqual(request.value(forHTTPHeaderField: "CF-Access-Client-Secret"), "device-secret")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer api-token")
    }

    func testCloudflareLoginPageGetsSpecificError() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: nil)
        MockURLProtocol.respond(
            body: "<html>Cloudflare Access</html>",
            headers: ["Content-Type": "text/html"],
            responseURL: URL(string: "https://team.cloudflareaccess.com/cdn-cgi/access/login/example.de")
        )

        do {
            _ = try await client.login(username: "oliver", password: "password") as LoginResponse
            XCTFail("Cloudflare-Loginseite darf nicht als API-Antwort gelten")
        } catch let error as APIError {
            guard case .cloudflareAccessRequired = error else {
                return XCTFail("Erwartet cloudflareAccessRequired, war \(error)")
            }
        }
    }

    func testRecipesFiltersManualCareOnServerAndPagesByOffset() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "token")

        MockURLProtocol.respond(json: """
        {"total": 130, "items": []}
        """)

        let first = try await client.recipes(manualOnly: true)
        let firstQuery = MockURLProtocol.lastQueryItems()

        // total kommt unverändert vom Server — nicht aus der Seitenlänge
        XCTAssertEqual(first.total, 130)
        XCTAssertEqual(firstQuery["needs_manual_care"], "true")
        XCTAssertEqual(firstQuery["limit"], String(APIClient.pageSize))
        XCTAssertEqual(firstQuery["offset"], "0")

        _ = try await client.recipes(search: "pasta", manualOnly: false, offset: 60)
        let secondQuery = MockURLProtocol.lastQueryItems()

        XCTAssertEqual(secondQuery["offset"], "60")
        XCTAssertEqual(secondQuery["search"], "pasta")
        XCTAssertNil(secondQuery["needs_manual_care"])
    }

    func testRecipesSendsNativeFilterSelection() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "token")
        MockURLProtocol.respond(json: """
        {"total": 0, "items": []}
        """)

        var filters = RecipeFilters()
        filters.type = "Hauptgericht"
        filters.category = "Pasta"
        filters.tagIDs = [8, 2]
        filters.includedIngredients = ["knoblauch"]
        filters.excludedIngredients = ["zwiebel"]
        filters.favoriteOnly = true
        filters.minRating = 4
        filters.manualOnly = true

        _ = try await client.recipes(filters: filters)
        let query = MockURLProtocol.lastQueryItems()

        XCTAssertEqual(query["type"], "Hauptgericht")
        XCTAssertEqual(query["category"], "Pasta")
        XCTAssertEqual(query["ingredient"], "knoblauch")
        XCTAssertEqual(query["exclude_ingredient"], "zwiebel")
        XCTAssertEqual(query["favorite_only"], "true")
        XCTAssertEqual(query["min_rating"], "4")
        XCTAssertEqual(query["needs_manual_care"], "true")
    }

    func testRecipeFacetsDecodeServerResponse() throws {
        let json = """
        {
          "types": ["Hauptgericht"],
          "categories": ["Pasta"],
          "tags": [{"id": 8, "name": "nussfrei", "n": 12}],
          "ingredients": [{"canonical_name": "salz", "display_name": "Salz", "n": 9}]
        }
        """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase

        let facets = try decoder.decode(RecipeFacets.self, from: Data(json.utf8))

        XCTAssertEqual(facets.tags.first?.name, "nussfrei")
        XCTAssertEqual(facets.ingredients.first?.canonicalName, "salz")
    }

    func testFileImportUsesAuthenticatedMultipartRequest() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "api-token")
        MockURLProtocol.respond(json: """
        {"ok":true,"message":"Datei wurde importiert."}
        """)

        let result = try await client.importFile(
            data: Data("jpeg-data".utf8), filename: "rezept.jpg", mimeType: "image/jpeg"
        )

        XCTAssertEqual(result.ok, true)
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        XCTAssertEqual(MockURLProtocol.lastHeader("Authorization"), "Bearer api-token")
        XCTAssertTrue(MockURLProtocol.lastHeader("Content-Type")?.contains("multipart/form-data") == true)
        let body = String(data: MockURLProtocol.lastBody(), encoding: .utf8) ?? ""
        XCTAssertTrue(body.contains("filename=\"rezept.jpg\""))
        XCTAssertTrue(body.contains("jpeg-data"))
    }
}

/// Fängt Requests des injizierten URLSession ab, damit der Query-Aufbau
/// prüfbar ist, ohne einen Server zu brauchen.
final class MockURLProtocol: URLProtocol {
    nonisolated(unsafe) private static var body = Data("{}".utf8)
    nonisolated(unsafe) private static var lastRequestURL: URL?
    nonisolated(unsafe) private static var lastRequestHeaders: [String: String] = [:]
    nonisolated(unsafe) private static var statusCode = 200
    nonisolated(unsafe) private static var responseHeaders = ["Content-Type": "application/json"]
    nonisolated(unsafe) private static var responseURL: URL?
    nonisolated(unsafe) private static var requestBody = Data()
    nonisolated(unsafe) private static var requestMethod = ""

    static func makeSession() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MockURLProtocol.self]
        return URLSession(configuration: configuration)
    }

    static func respond(json: String) {
        respond(body: json)
    }

    static func respond(
        body responseBody: String,
        statusCode responseStatusCode: Int = 200,
        headers: [String: String] = ["Content-Type": "application/json"],
        responseURL: URL? = nil
    ) {
        body = Data(responseBody.utf8)
        statusCode = responseStatusCode
        responseHeaders = headers
        self.responseURL = responseURL
    }

    static func lastQueryItems() -> [String: String] {
        guard let url = lastRequestURL,
              let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            return [:]
        }
        return (components.queryItems ?? []).reduce(into: [:]) { result, item in
            result[item.name] = item.value
        }
    }

    static func lastHeader(_ name: String) -> String? {
        lastRequestHeaders.first { $0.key.caseInsensitiveCompare(name) == .orderedSame }?.value
    }

    static func lastBody() -> Data { requestBody }
    static func lastMethod() -> String { requestMethod }

    override class func canInit(with request: URLRequest) -> Bool { true }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.lastRequestURL = request.url
        Self.lastRequestHeaders = request.allHTTPHeaderFields ?? [:]
        if let body = request.httpBody {
            Self.requestBody = body
        } else if let stream = request.httpBodyStream {
            stream.open()
            defer { stream.close() }
            var body = Data()
            var buffer = [UInt8](repeating: 0, count: 4096)
            while stream.hasBytesAvailable {
                let count = stream.read(&buffer, maxLength: buffer.count)
                guard count > 0 else { break }
                body.append(buffer, count: count)
            }
            Self.requestBody = body
        } else {
            Self.requestBody = Data()
        }
        Self.requestMethod = request.httpMethod ?? ""
        guard let response = HTTPURLResponse(
            url: Self.responseURL ?? request.url ?? URL(fileURLWithPath: "/"),
            statusCode: Self.statusCode,
            httpVersion: nil,
            headerFields: Self.responseHeaders
        ) else {
            client?.urlProtocolDidFinishLoading(self)
            return
        }
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Self.body)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
