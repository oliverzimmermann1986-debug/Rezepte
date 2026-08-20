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
}

/// Fängt Requests des injizierten URLSession ab, damit der Query-Aufbau
/// prüfbar ist, ohne einen Server zu brauchen.
final class MockURLProtocol: URLProtocol {
    nonisolated(unsafe) private static var body = Data("{}".utf8)
    nonisolated(unsafe) private static var lastRequestURL: URL?

    static func makeSession() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MockURLProtocol.self]
        return URLSession(configuration: configuration)
    }

    static func respond(json: String) {
        body = Data(json.utf8)
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

    override class func canInit(with request: URLRequest) -> Bool { true }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.lastRequestURL = request.url
        guard let response = HTTPURLResponse(
            url: request.url ?? URL(fileURLWithPath: "/"),
            statusCode: 200,
            httpVersion: nil,
            headerFields: ["Content-Type": "application/json"]
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
