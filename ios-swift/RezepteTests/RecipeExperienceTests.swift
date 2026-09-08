import Foundation
import XCTest
@testable import Rezepte

final class RecipeExperienceTests: XCTestCase {
    private let memoryJSON = """
    {"items":[{"id":11,"client_entry_id":"stable-1","recipe_id":42,"created_at":1725000000,
    "note":"Gut geworden","adjustments":"Weniger Salz","next_time":"Nudelwasser behalten",
    "servings":3,"step_number":2,"step_instruction":"Nudeln abgießen.","step_is_current":true}],"total":1}
    """

    private let reviewJSON = """
    {"recipe_id":42,"revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "can_apply":false,"source":{"url":"https://example.de/pasta","description":"Originaltext"},
    "ingredients":[{"name":"Nudeln","amount":200,"unit":"g","raw":"200 g Nudeln"}],
    "steps":[{"step_number":1,"instruction":"Kochen.","timer_seconds":600}],"servings":2,
    "quality":{"status":"needs-review","score":90,"checked_rules":9,
    "issues":[{"id":"manual-verification-required","title":"Prüfung offen","detail":"Prüfen",
    "severity":"warning","section":"verification"}]},
    "corrections":[{"id":3,"status":"pending","reason":"Menge geprüft","username":"anna","created_at":1725000000,
    "before":{"ingredients":[{"name":"Nudeln","amount":200,"unit":"g","raw":"200 g Nudeln"}],
    "steps":[{"instruction":"Kochen.","timer_seconds":600}],"servings":2},
    "proposed":{"ingredients":[{"name":"Nudeln","amount":300,"unit":"g","raw":null}],
    "steps":[{"instruction":"Kochen.","timer_seconds":600}],"servings":3}}]}
    """

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(type, from: Data(json.utf8))
    }

    func testMemoryContractAndExactStepAnchoring() throws {
        let entry = try XCTUnwrap(decode(CookingMemoryResponse.self, memoryJSON).items.first)
        XCTAssertEqual(entry.clientEntryId, "stable-1")
        XCTAssertTrue(entry.matches(stepNumber: 2, instruction: "Nudeln abgießen."))
        XCTAssertFalse(entry.matches(stepNumber: 1, instruction: "Nudeln abgießen."))
        XCTAssertFalse(entry.matches(stepNumber: 2, instruction: "Nudeln und Nudelwasser abgießen."))
        let stale = try decode(CookingMemoryResponse.self, memoryJSON.replacingOccurrences(of: "\"step_is_current\":true", with: "\"step_is_current\":false"))
        XCTAssertFalse(try XCTUnwrap(stale.items.first).matches(stepNumber: 2, instruction: "Nudeln abgießen."))
    }

    func testMemoryRequiresContentAndHonorsFieldLimit() {
        var draft = CookingMemoryRequest()
        XCTAssertFalse(draft.isValid)
        draft.note = " \n "
        XCTAssertFalse(draft.isValid)
        draft.nextTime = "Nudelwasser behalten"
        XCTAssertTrue(draft.isValid)
        let identity = draft.clientEntryId
        draft.adjustments = String(repeating: "x", count: 2001)
        XCTAssertFalse(draft.isValid)
        XCTAssertEqual(draft.clientEntryId, identity)
    }

    func testAmountParsingNeverInventsUnknownQuantity() {
        XCTAssertNil(ImportReviewNumber.parse(""))
        XCTAssertNil(ImportReviewNumber.parse("nach Geschmack"))
        XCTAssertNil(ImportReviewNumber.parse("nan"))
        XCTAssertNil(ImportReviewNumber.parse("inf"))
        XCTAssertNil(ImportReviewNumber.parse("-2"))
        XCTAssertNil(ImportReviewNumber.parse("1000001"))
        XCTAssertEqual(ImportReviewNumber.parse(" 1,5 "), 1.5)
        XCTAssertEqual(ImportReviewNumber.parse("0"), 0)
        let exact = 0.123456789
        XCTAssertEqual(ImportReviewNumber.parse(ImportReviewNumber.format(exact)), exact)
    }

    @MainActor
    func testPermanentFailureDoesNotBlockLaterNotesAndStaleGETCannotOverwriteChanges() throws {
        XCTAssertTrue(CookingMemoryStorage.canContinueSync(after: APIError.server(409, "Schritt geändert")))
        XCTAssertTrue(CookingMemoryStorage.canContinueSync(after: APIError.server(410, "Gelöscht")))
        XCTAssertFalse(CookingMemoryStorage.canContinueSync(after: APIError.server(503, "Offline")))
        XCTAssertFalse(CookingMemoryStorage.canContinueSync(after: APIError.server(429, "Zu viele Anfragen")))
        let entries = try decode(CookingMemoryResponse.self, memoryJSON).items
        var archive = CookingMemoryArchive()
        let readBeganAt = archive.generation
        archive.entries["42"] = entries
        archive.generation += 1
        XCTAssertFalse(archive.applySnapshot([], recipeID: 42, expectedGeneration: readBeganAt))
        XCTAssertEqual(archive.entries["42"]?.count, 1)
        XCTAssertTrue(archive.applySnapshot([], recipeID: 42, expectedGeneration: archive.generation))
        XCTAssertEqual(archive.entries["42"]?.count, 0)
    }

    func testImportReviewDecodesRealQualityAndNestedAudit() throws {
        let report = try decode(ImportReviewResponse.self, reviewJSON)
        XCTAssertFalse(report.canApply)
        XCTAssertEqual(report.quality.issues.first?.section, "verification")
        XCTAssertEqual(report.corrections.first?.before.ingredients.first?.amount, 200)
        XCTAssertEqual(report.corrections.first?.proposed.ingredients.first?.amount, 300)
        var draft = ImportReviewDraft(report: report)
        XCTAssertFalse(draft.valid, "Reason is mandatory")
        draft.reason = "Originalquelle geprüft"
        XCTAssertTrue(draft.valid)
        XCTAssertEqual(draft.payload.expectedRevision, report.revision)
        XCTAssertEqual(draft.payload.ingredients.first?.raw, "200 g Nudeln", "Untouched ingredient qualifiers must survive a step-only correction")
        draft.ingredients[0].amount = "300"
        draft.ingredients[0].wasEdited = true
        XCTAssertNil(draft.payload.ingredients.first?.raw, "Edited structured quantities must not keep obsolete raw text")
        draft.steps[0].timerSeconds = "0"
        XCTAssertFalse(draft.valid)
        draft.steps[0].timerSeconds = ""
        XCTAssertTrue(draft.valid)
        draft.ingredients = []
        XCTAssertFalse(draft.valid)
    }

    func testMemoryPOSTUsesPrivateAuthenticatedEndpointAndStableRequestID() async throws {
        let client = APIClient(session: MockURLProtocol.makeSession())
        try await client.configure(server: "https://example.de", token: "private-session")
        let account = try XCTUnwrap(OfflineAccount(server: "https://example.de", username: "anna"))
        await client.setOfflineAccount(account)
        let entry = try XCTUnwrap(decode(CookingMemoryResponse.self, memoryJSON).items.first)
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        let encoded = String(decoding: try encoder.encode(entry), as: UTF8.self)
        MockURLProtocol.respond(json: "{\"ok\":true,\"entry\":\(encoded)}")
        var request = CookingMemoryRequest(note: "Gut geworden", servings: 3)
        request.clientEntryId = "stable-1"
        _ = try await client.saveCookingMemory(recipeID: 42, request: request, expectedAccount: account)
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/recipes/42/cooking-memory")
        XCTAssertEqual(MockURLProtocol.lastHeader("Authorization"), "Bearer private-session")
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: MockURLProtocol.lastBody()) as? [String: Any])
        XCTAssertEqual(body["client_entry_id"] as? String, "stable-1")
    }

    func testPrivateArchiveAndDraftSurviveRelaunchButNotLogoutPurge() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = OfflineStore(directory: directory)
        let anna = try XCTUnwrap(OfflineAccount(server: "https://example.de", username: "anna"))
        let ben = try XCTUnwrap(OfflineAccount(server: "https://example.de", username: "ben"))
        let otherServer = try XCTUnwrap(OfflineAccount(server: "https://other.example.de", username: "anna"))
        var archive = CookingMemoryArchive()
        archive.entries["42"] = try decode(CookingMemoryResponse.self, memoryJSON).items
        archive.pending = [PendingCookingMemory(recipeID: 42, createdAt: 1000, request: CookingMemoryRequest(note: "Lokal"))]
        archive.drafts["42:recipe"] = CookingMemoryRequest(nextTime: "Nicht vergessen")
        try store.write(archive, key: "memory", account: anna)
        let reopened = OfflineStore(directory: directory)
        XCTAssertEqual(try reopened.read(CookingMemoryArchive.self, key: "memory", account: anna)?.pending.first?.request.note, "Lokal")
        XCTAssertNil(try reopened.read(CookingMemoryArchive.self, key: "memory", account: ben))
        XCTAssertNil(try reopened.read(CookingMemoryArchive.self, key: "memory", account: otherServer))
        try reopened.clear(account: anna)
        XCTAssertNil(try reopened.read(CookingMemoryArchive.self, key: "memory", account: anna))
    }
}
