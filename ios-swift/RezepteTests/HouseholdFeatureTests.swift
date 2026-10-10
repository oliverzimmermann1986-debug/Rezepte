import Foundation
import UIKit
import XCTest
@testable import Rezepte

final class HouseholdFeatureTests: XCTestCase {
    private let noteJSON = #"{"history_id":21,"cooked_at":1730000000,"cooked_by":"Mitglied","servings":3,"note":"Weniger Salz","photo_url":null,"can_edit":false,"updated_at":null}"#

    private func client() async throws -> APIClient {
        let client = APIClient(session: MockURLProtocol.makeSession())
        try await client.configure(server: "https://example.de/rezepte", token: "household-test-token")
        return client
    }

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(type, from: Data(json.utf8))
    }

    private func body() throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: MockURLProtocol.lastBody()) as? [String: Any])
    }

    func testCookbookMembershipUsesServerDecisionAndKeepsBasePath() async throws {
        let client = try await client()
        MockURLProtocol.respond(json: #"{"items":[{"id":3,"name":"Familie","recipe_count":4,"contains_recipe":true}]}"#)
        let books = try await client.householdCookbooks(recipeID: 77)
        XCTAssertEqual(books.first?.containsRecipe, true)
        XCTAssertEqual(books.first?.recipeCount, 4)
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/cookbooks")
        XCTAssertEqual(MockURLProtocol.lastQueryItems()["recipe_id"], "77")
        XCTAssertEqual(MockURLProtocol.lastHeader("Authorization"), "Bearer household-test-token")
        MockURLProtocol.respond(json: #"{"ok":true}"#)
        try await client.cookbookMembership(id: 3, recipeID: 77, included: true)
        XCTAssertEqual(MockURLProtocol.lastMethod(), "PUT")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/cookbooks/3/recipes/77")
        try await client.cookbookMembership(id: 3, recipeID: 77, included: false)
        XCTAssertEqual(MockURLProtocol.lastMethod(), "DELETE")
    }

    func testCookbooksCreateRenameAndDeleteUseOnlyTheirOwnResource() async throws {
        let client = try await client()
        MockURLProtocol.respond(json: #"{"item":{"id":3,"name":"Familie","recipe_count":0,"contains_recipe":false}}"#)
        _ = try await client.saveCookbook(name: "Familie")
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        XCTAssertEqual(try body() as? [String: String], ["name": "Familie"])
        _ = try await client.saveCookbook(name: "Sonntag", id: 3)
        XCTAssertEqual(MockURLProtocol.lastMethod(), "PATCH")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/cookbooks/3")
        MockURLProtocol.respond(json: #"{"ok":true}"#)
        try await client.deleteCookbook(id: 3)
        XCTAssertEqual(MockURLProtocol.lastMethod(), "DELETE")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/cookbooks/3")
    }

    func testCookingNotesPreserveHistoryIdentityAndServerEditPermission() throws {
        let note = try decode(CookingNote.self, noteJSON)
        XCTAssertEqual(note.id, 21)
        XCTAssertEqual(note.cookedBy, "Mitglied")
        XCTAssertEqual(note.note, "Weniger Salz")
        XCTAssertFalse(note.canEdit)
        XCTAssertNil(note.photoUrl)
        XCTAssertNil(note.updatedAt)
    }

    func testNoteUpsertNeverCreatesAnotherCookingHistoryEntry() async throws {
        let client = try await client()
        MockURLProtocol.respond(json: "{\"item\":\(noteJSON)}")
        _ = try await client.saveCookingNote(historyID: 21, note: "Nächstes Mal mehr Gemüse")
        XCTAssertEqual(MockURLProtocol.lastMethod(), "PUT")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/cook-notes/21")
        XCTAssertEqual(try body() as? [String: String], ["note": "Nächstes Mal mehr Gemüse"])
    }

    func testPhotoUploadUsesAuthenticatedMultipartJPEGAndDeleteOnlyPhoto() async throws {
        let client = try await client()
        MockURLProtocol.respond(json: "{\"item\":\(noteJSON)}")
        _ = try await client.saveCookingPhoto(historyID: 21, jpeg: Data("synthetic-jpeg".utf8))
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/cook-notes/21/photo")
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        XCTAssertEqual(MockURLProtocol.lastHeader("Authorization"), "Bearer household-test-token")
        XCTAssertTrue(MockURLProtocol.lastHeader("Content-Type")?.hasPrefix("multipart/form-data; boundary=") == true)
        let text = String(decoding: MockURLProtocol.lastBody(), as: UTF8.self)
        XCTAssertTrue(text.contains("name=\"file\""))
        XCTAssertTrue(text.contains("Content-Type: image/jpeg"))
        _ = try await client.removeCookingPhoto(historyID: 21)
        XCTAssertEqual(MockURLProtocol.lastMethod(), "DELETE")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/cook-notes/21/photo")
    }

    func testMealWishKeepsPerUserVoteAndAuthorPermission() throws {
        let wish = try decode(MealWish.self, #"{"id":5,"recipe_id":77,"recipe_name":"Suppe","created_by":"Mitglied","votes":3,"my_vote":true,"can_delete":false,"planned_for":null,"planned_servings":null}"#)
        XCTAssertEqual(wish.recipeId, 77)
        XCTAssertTrue(wish.myVote)
        XCTAssertFalse(wish.canDelete)
        XCTAssertEqual(wish.votes, 3)
    }

    func testWishVoteAndPlanningRetryKeepExactPayload() async throws {
        let client = try await client()
        MockURLProtocol.respond(json: #"{"ok":true}"#)
        try await client.voteMealWish(id: 5, voted: false)
        XCTAssertEqual(MockURLProtocol.lastMethod(), "PUT")
        XCTAssertEqual(try body()["voted"] as? Bool, false)
        let request = WishPlanRequest(plannedFor: "2026-10-13", plannedServings: 4)
        try await client.planMealWish(id: 5, request: request)
        let first = try body()
        try await client.planMealWish(id: 5, request: request)
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/meal-wishes/5/plan")
        XCTAssertEqual(first["planned_for"] as? String, "2026-10-13")
        XCTAssertEqual(try body()["planned_for"] as? String, first["planned_for"] as? String)
        XCTAssertEqual(try body()["planned_servings"] as? Int, 4)
    }

    func testSuggestionsSendConstraintsAndDistinctExclusions() async throws {
        let client = try await client()
        MockURLProtocol.respond(json: #"{"items":[],"warnings":["Zu wenige passende Rezepte"]}"#)
        let result = try await client.mealSuggestions(count: 4, vegetarian: 2, maxMinutes: 30, excluding: [5, 2, 5])
        XCTAssertEqual(result.warnings.count, 1)
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/discovery/meal-plan")
        XCTAssertEqual(try body()["count"] as? Int, 4)
        XCTAssertEqual(try body()["vegetarian_count"] as? Int, 2)
        XCTAssertEqual(try body()["max_minutes"] as? Int, 30)
        XCTAssertEqual(try body()["exclude_recipe_ids"] as? [Int], [2, 5])
    }

    func testSuggestionDraftPrefersEmptyDaysDeduplicatesAndBoundsServings() {
        let entry = MealEntry(id: 1, recipeId: 10, recipeName: "Geplant", plannedFor: "2026-10-12", plannedServings: 2,
                              recipeServings: 2, multiplier: 1, scalable: true)
        let days = [MealDay(date: "2026-10-12", label: "Montag", shortLabel: "Mo", dayNumber: 12, isToday: false, items: [entry]),
                    MealDay(date: "2026-10-13", label: "Dienstag", shortLabel: "Di", dayNumber: 13, isToday: false, items: [])]
        let first = MealSuggestion(recipeId: 3, name: "A", servings: 99, totalMinutes: 20, vegetarian: true)
        let second = MealSuggestion(recipeId: 4, name: "B", servings: 0, totalMinutes: nil, vegetarian: false)
        let rows = MealSuggestionDraft.make([first, first, second], days: days)
        XCTAssertEqual(rows.map(\.id), [3, 4])
        XCTAssertEqual(rows.map(\.date), ["2026-10-13", "2026-10-12"])
        XCTAssertEqual(rows.map(\.servings), [24, 1])
        XCTAssertTrue(rows.allSatisfy { $0.status == .pending })
    }

    @MainActor
    func testPhotoEncodingRejectsUnknownBytesAndDownsizesLargeImages() throws {
        XCTAssertThrowsError(try CookingPhotoEncoding.jpeg(Data("not an image".utf8)))
        let format = UIGraphicsImageRendererFormat(); format.scale = 1
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 3200, height: 800), format: format)
        let source = renderer.pngData { context in
            UIColor.yellow.setFill(); context.fill(CGRect(x: 0, y: 0, width: 3200, height: 800))
        }
        let result = try CookingPhotoEncoding.jpeg(source)
        let image = try XCTUnwrap(UIImage(data: result))
        XCTAssertLessThanOrEqual(max(image.size.width, image.size.height), 1600)
        XCTAssertLessThan(result.count, 8 * 1024 * 1024)
        XCTAssertEqual(Array(result.prefix(2)), [0xff, 0xd8])
    }

    func testLateHouseholdListCannotAppearInAnotherSession() async throws {
        let client = try await client()
        MockURLProtocol.respond(json: #"{"items":[{"id":3,"name":"Privat","recipe_count":4,"contains_recipe":false}]}"#)
        let started = expectation(description: "Old household request started")
        MockURLProtocol.suspendNextResponse { started.fulfill() }
        defer { MockURLProtocol.resumeResponse() }
        let pending = Task { try await client.householdCookbooks() }
        await fulfillment(of: [started], timeout: 2)
        try await client.configure(server: "https://example.de/rezepte", token: "new-household-token", sessionID: UUID())
        MockURLProtocol.resumeResponse()
        do { _ = try await pending.value; XCTFail("Old household data must be rejected") }
        catch let error as APIError { if case .sessionChanged = error {} else { XCTFail("Unexpected error: \(error)") } }
    }
}
