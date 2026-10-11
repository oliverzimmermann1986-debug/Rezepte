import Foundation
import XCTest
@testable import Rezepte

final class RecipeServingsEstimateTests: XCTestCase {
    private let response = #"{"current_servings":2,"suggested_servings":4,"min_servings":3,"max_servings":5,"confidence":"medium","rationale":"Die Zutatenmengen reichen eher für vier Hauptportionen.","warnings":["Appetit und Beilagen beeinflussen die Portionsgröße."],"fingerprint":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}"#

    private func client() async throws -> (APIClient, AIProcessingConsent) {
        let identity = UUID()
        let client = APIClient(session: MockURLProtocol.makeSession())
        try await client.configure(server: "https://example.de/rezepte", token: "test-token", sessionID: identity)
        MockURLProtocol.respond(json: #"{"name":"Rezepte","version":"1.12.0","capabilities":["ai-action-consent-v1","servings-estimate-v1"]}"#)
        _ = try await client.systemInfo()
        return (client, AIProcessingConsent(id: UUID(), action: .servingsEstimate, identity: identity, server: "https://example.de/rezepte"))
    }

    private func decode(_ text: String) throws -> RecipeServingsEstimate {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(RecipeServingsEstimate.self, from: Data(text.utf8)).validated()
    }

    func testEstimateIsOneExplicitAIRequestAndCarriesExplanation() async throws {
        let (client, consent) = try await client()
        MockURLProtocol.respond(json: response)
        let before = MockURLProtocol.requestCount
        let estimate = try await client.estimateRecipeServings(id: 42, consent: consent)
        XCTAssertEqual(MockURLProtocol.requestCount, before + 1)
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/recipes/42/servings-estimate")
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        XCTAssertEqual(MockURLProtocol.lastHeader("Authorization"), "Bearer test-token")
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: MockURLProtocol.lastBody()) as? [String: String])
        XCTAssertEqual(body, ["ai_processing_consent": "openai-recipe-v1"])
        XCTAssertEqual(estimate.currentServings, 2)
        XCTAssertEqual(estimate.suggestedServings, 4)
        XCTAssertEqual(estimate.minServings, 3)
        XCTAssertEqual(estimate.maxServings, 5)
        XCTAssertEqual(estimate.confidence, .medium)
        XCTAssertFalse(estimate.rationale.isEmpty)
        XCTAssertEqual(estimate.warnings.count, 1)
        XCTAssertEqual(estimate.fingerprint, "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
        do {
            _ = try await client.estimateRecipeServings(id: 42, consent: consent)
            XCTFail("A second estimate requires a new explicit consent")
        } catch APIError.aiConsentRequired {}
        XCTAssertEqual(MockURLProtocol.requestCount, before + 1)
    }

    func testDifferentActionCannotAuthorizeServingsEstimate() async throws {
        let (client, consent) = try await client()
        let wrong = AIProcessingConsent(id: UUID(), action: .nutrition, identity: consent.identity, server: consent.server)
        let before = MockURLProtocol.requestCount
        do {
            _ = try await client.estimateRecipeServings(id: 42, consent: wrong)
            XCTFail("Nutrition consent must not authorize another AI operation")
        } catch APIError.aiConsentRequired {}
        XCTAssertEqual(MockURLProtocol.requestCount, before)
    }

    func testInsufficientQuantitiesDoNotInventAPortionCountOrRange() throws {
        let estimate = try decode(#"{"current_servings":null,"suggested_servings":null,"min_servings":null,"max_servings":null,"confidence":"low","rationale":"Zu wenige Mengenangaben.","warnings":[],"fingerprint":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}"#)
        XCTAssertNil(estimate.currentServings)
        XCTAssertNil(estimate.suggestedServings)
        XCTAssertNil(estimate.rangeText)
        XCTAssertEqual(estimate.confidence, .low)
    }

    func testInvalidEstimateNumbersAndRangesAreRejected() throws {
        for (before, after) in [("\"suggested_servings\":4", "\"suggested_servings\":0"),
                                ("\"suggested_servings\":4", "\"suggested_servings\":51"),
                                ("\"min_servings\":3", "\"min_servings\":5"),
                                ("\"max_servings\":5", "\"max_servings\":null"),
                                ("\"fingerprint\":\"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\"", "\"fingerprint\":\"\"")] {
            XCTAssertThrowsError(try decode(response.replacingOccurrences(of: before, with: after)))
        }
    }

    func testEstimateRequiresCompleteRangeAndNoEstimateRequiresLowConfidence() throws {
        let withoutRange = response.replacingOccurrences(of: "\"min_servings\":3", with: "\"min_servings\":null")
            .replacingOccurrences(of: "\"max_servings\":5", with: "\"max_servings\":null")
        XCTAssertThrowsError(try decode(withoutRange))
        let withoutSuggestion = withoutRange.replacingOccurrences(of: "\"suggested_servings\":4", with: "\"suggested_servings\":null")
        XCTAssertThrowsError(try decode(withoutSuggestion))
        XCTAssertNoThrow(try decode(withoutSuggestion.replacingOccurrences(of: "\"confidence\":\"medium\"", with: "\"confidence\":\"low\"")))
        XCTAssertThrowsError(try decode(response.replacingOccurrences(of: "\"suggested_servings\":4", with: "\"suggested_servings\":null")))
        let rationale = "Die Zutatenmengen reichen eher für vier Hauptportionen."
        XCTAssertThrowsError(try decode(response.replacingOccurrences(of: rationale, with: "   ")))
    }

    func testFingerprintRequiresExactly64LowercaseHexCharacters() {
        XCTAssertTrue(RecipeServingsEstimate.isValidFingerprint(String(repeating: "a", count: 64)))
        XCTAssertFalse(RecipeServingsEstimate.isValidFingerprint(String(repeating: "a", count: 63)))
        XCTAssertFalse(RecipeServingsEstimate.isValidFingerprint(String(repeating: "A", count: 64)))
        XCTAssertFalse(RecipeServingsEstimate.isValidFingerprint(String(repeating: "g", count: 64)))
        XCTAssertFalse(RecipeServingsEstimate.isValidFingerprint("snapshot-42"))
    }

    func testApplyUsesChosenPortionsAndExactSnapshotWithoutAnotherAITransfer() async throws {
        let (client, _) = try await client()
        MockURLProtocol.respond(json: #"{"ok":true,"servings":5}"#)
        let result = try await client.applyRecipeServingsEstimate(id: 42, servings: 5, fingerprint: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
        XCTAssertTrue(result.ok)
        XCTAssertEqual(result.servings, 5)
        XCTAssertEqual(MockURLProtocol.lastPath(), "/rezepte/api/recipes/42/servings-estimate/apply")
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: MockURLProtocol.lastBody()) as? [String: Any])
        XCTAssertEqual(body["servings"] as? Int, 5)
        XCTAssertEqual(body["fingerprint"] as? String, "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
        XCTAssertNil(body["ai_processing_consent"])
        XCTAssertEqual(body.count, 2)
    }

    func testInvalidApplyDoesNotSendAndUnconfirmedApplyDoesNotSucceed() async throws {
        let (client, _) = try await client()
        let before = MockURLProtocol.requestCount
        for value in [0, 51] {
            do {
                _ = try await client.applyRecipeServingsEstimate(id: 42, servings: value, fingerprint: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
                XCTFail("Invalid portions must not be sent")
            } catch APIError.invalidResponse {}
        }
        for fingerprint in ["snapshot-42", String(repeating: "A", count: 64)] {
            do {
                _ = try await client.applyRecipeServingsEstimate(id: 42, servings: 4, fingerprint: fingerprint)
                XCTFail("Malformed fingerprints must not be sent")
            } catch APIError.invalidResponse {}
        }
        XCTAssertEqual(MockURLProtocol.requestCount, before)
        for response in [#"{"ok":false,"servings":4}"#, #"{"ok":true,"servings":2}"#] {
            MockURLProtocol.respond(json: response)
            do {
                _ = try await client.applyRecipeServingsEstimate(id: 42, servings: 4, fingerprint: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
                XCTFail("Only the confirmed chosen portions count as saved")
            } catch APIError.invalidResponse {}
        }
    }

    func testStaleRecipeResponseIsPreservedForExplicitRecheck() async throws {
        let (client, _) = try await client()
        MockURLProtocol.respond(body: #"{"detail":"Rezept wurde inzwischen geändert."}"#, statusCode: 409)
        do {
            _ = try await client.applyRecipeServingsEstimate(id: 42, servings: 4, fingerprint: "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789")
            XCTFail("A stale estimate must not overwrite newer recipe changes")
        } catch APIError.server(let status, let message) {
            XCTAssertEqual(status, 409)
            XCTAssertEqual(message, "Rezept wurde inzwischen geändert.")
        }
    }

    func testLateEstimateCannotCrossAccountChange() async throws {
        let (client, consent) = try await client()
        MockURLProtocol.respond(json: response)
        let started = expectation(description: "Estimate requested")
        MockURLProtocol.suspendNextResponse { started.fulfill() }
        defer { MockURLProtocol.resumeResponse() }
        let request = Task { try await client.estimateRecipeServings(id: 42, consent: consent) }
        await fulfillment(of: [started], timeout: 2)
        try await client.configure(server: "https://other.example", token: "next-account", sessionID: UUID())
        MockURLProtocol.resumeResponse()
        do {
            _ = try await request.value
            XCTFail("An old account's estimate must not reach the new session")
        } catch APIError.sessionChanged {}
    }
}
