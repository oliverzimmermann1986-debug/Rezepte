import Foundation
import XCTest
@testable import Rezepte

final class RecipeTrashTests: XCTestCase {
    private func client() async throws -> APIClient {
        let client = APIClient(session: MockURLProtocol.makeSession())
        try await client.configure(server: "https://example.de/rezepte", token: "trash-test-token")
        return client
    }

    func testTrashUsesHouseholdEndpointAndDecodesServerPermissions() async throws {
        let client = try await client()
        MockURLProtocol.respond(json: #"{"total":2,"items":[{"id":42,"name":"Eigene Suppe","can_edit":true,"visibility":"private","days_until_purge":29},{"id":43,"name":"Globales Rezept","can_edit":false,"visibility":"global"}]}"#)
        let response = try await client.trash()
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/recipes/trash/list")
        XCTAssertEqual(MockURLProtocol.lastMethod(), "GET")
        XCTAssertEqual(MockURLProtocol.lastHeader("Authorization"), "Bearer trash-test-token")
        XCTAssertEqual(response.total, 2)
        XCTAssertEqual(response.items[0].visibility, "private")
        XCTAssertEqual(response.items[0].daysUntilPurge, 29)
        XCTAssertTrue(response.items[0].canRestore(isAdmin: false))
        XCTAssertFalse(response.items[1].canRestore(isAdmin: false))
        XCTAssertFalse(response.items[1].canRestore(isAdmin: true), "An explicit server denial overrides the legacy admin fallback")
    }

    func testLegacyTrashOnlyAllowsAdminFallback() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let recipe = try decoder.decode(TrashRecipe.self, from: Data(#"{"id":42,"name":"Altes Rezept"}"#.utf8))
        XCTAssertNil(recipe.canEdit)
        XCTAssertNil(recipe.visibility)
        XCTAssertFalse(recipe.canRestore(isAdmin: false))
        XCTAssertTrue(recipe.canRestore(isAdmin: true))
    }

    func testRestoreUsesOnlyRestoreEndpointAndKeepsAcknowledgementExplicit() async throws {
        let client = try await client()
        for (json, expected) in [(#"{"ok":true}"#, true), (#"{"ok":false}"#, false), (#"{}"#, false)] {
            MockURLProtocol.respond(json: json)
            let result = try await client.restoreTrashRecipe(id: 42)
            XCTAssertEqual(result.ok == true, expected)
            XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/recipes/42/restore")
            XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
            XCTAssertTrue(MockURLProtocol.lastQueryItems().isEmpty)
        }
    }
}
