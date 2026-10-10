import Foundation
import XCTest
@testable import Rezepte

final class AIProcessingConsentTests: XCTestCase {
    func testStructuredConsentAndProviderErrorsPreserveTheServerExplanation() async throws {
        let client = APIClient(session: MockURLProtocol.makeSession())
        try await client.configure(server: "https://example.de", token: "synthetic")
        for (status, body, expected) in [
            (428, #"{"detail":{"code":"AI_CONSENT_REQUIRED","message":"Bitte bestätige die Übermittlung an OpenAI."}}"#,
             "Bitte bestätige die Übermittlung an OpenAI."),
            (409, #"{"detail":{"code":"AI_PROVIDER_UNSUPPORTED","message":"Die Freigabe für OpenAI gilt nicht für diesen KI-Dienst."}}"#,
             "Die Freigabe für OpenAI gilt nicht für diesen KI-Dienst."),
            (409, #"{"detail":"Quellenstand ist veraltet"}"#, "Quellenstand ist veraltet")
        ] {
            MockURLProtocol.respond(body: body, statusCode: status)
            do {
                _ = try await client.recipe(id: 42)
                XCTFail("An unsuccessful response must be reported")
            } catch APIError.server(let actualStatus, let message) {
                XCTAssertEqual(actualStatus, status)
                XCTAssertEqual(message, expected)
            }
        }
    }

    func testDownloadErrorsAlsoAcceptStructuredDetails() async throws {
        let client = APIClient(session: MockURLProtocol.makeSession())
        try await client.configure(server: "https://example.de", token: "synthetic")
        MockURLProtocol.respond(body: #"{"detail":{"code":"AI_PROVIDER_UNSUPPORTED","message":"Anderer KI-Dienst konfiguriert."}}"#, statusCode: 409)
        do {
            _ = try await client.recipePDF(id: 42)
            XCTFail("An unsuccessful download must be reported")
        } catch APIError.server(let status, let message) {
            XCTAssertEqual(status, 409)
            XCTAssertEqual(message, "Anderer KI-Dienst konfiguriert.")
        }
    }

    @MainActor
    func testDecliningAnImportStartsNoNetworkRequest() async throws {
        let identity = UUID()
        let coordinator = AIConsentCoordinator()
        let client = APIClient(session: MockURLProtocol.makeSession())
        try await client.configure(server: "https://example.de", token: "synthetic", sessionID: identity)
        let before = MockURLProtocol.requestCount
        let operation = Task {
            guard let consent = await coordinator.request(.importLink, identity: identity, server: "https://example.de") else { return }
            _ = try await client.importURL("https://source.example/recipe", consent: consent)
        }
        await waitForPrompt(coordinator)
        coordinator.cancel()
        try await operation.value
        XCTAssertNil(coordinator.pending)
        XCTAssertEqual(MockURLProtocol.requestCount, before)
    }

    @MainActor
    func testApprovalIsBoundToOneRequestAndCarriesTheDisclosureVersion() async throws {
        let identity = UUID()
        let coordinator = AIConsentCoordinator()
        let client = APIClient(session: MockURLProtocol.makeSession())
        try await client.configure(server: "https://example.de", token: "synthetic", sessionID: identity)
        let decision = Task { await coordinator.request(.importLink, identity: identity, server: "https://example.de") }
        await waitForPrompt(coordinator)
        coordinator.approve(identity: identity, server: "https://example.de")
        coordinator.approve(identity: identity, server: "https://example.de")
        let result = await decision.value
        let consent = try XCTUnwrap(result)
        MockURLProtocol.respond(json: #"{"ok":true}"#)
        let before = MockURLProtocol.requestCount
        _ = try await client.importURL("https://source.example/recipe", consent: consent)
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: MockURLProtocol.lastBody()) as? [String: String])
        XCTAssertEqual(body["ai_processing_consent"], "openai-recipe-v1")
        XCTAssertEqual(body["url"], "https://source.example/recipe")
        try await client.configure(server: "https://example.de", token: "synthetic", sessionID: identity)
        do {
            _ = try await client.importURL("https://source.example/other", consent: consent)
            XCTFail("A consent must not authorize a second transfer")
        } catch APIError.aiConsentRequired {}
        XCTAssertEqual(MockURLProtocol.requestCount, before + 1)
    }

    @MainActor
    func testChangingIdentityWhilePromptIsOpenInvalidatesApproval() async {
        let coordinator = AIConsentCoordinator()
        let decision = Task { await coordinator.request(.importLink, identity: UUID(), server: "https://example.de") }
        await waitForPrompt(coordinator)
        coordinator.approve(identity: UUID(), server: "https://example.de")
        let consent = await decision.value
        XCTAssertNil(consent)
        XCTAssertNil(coordinator.pending)
    }

    @MainActor
    func testChangingServerWhilePromptIsOpenInvalidatesApproval() async {
        let identity = UUID()
        let coordinator = AIConsentCoordinator()
        let decision = Task { await coordinator.request(.importLink, identity: identity, server: "https://example.de") }
        await waitForPrompt(coordinator)
        coordinator.approve(identity: identity, server: "https://other.example")
        let consent = await decision.value
        XCTAssertNil(consent)
    }

    func testAnAlreadyApprovedDecisionCannotCrossSessionsOrServers() async throws {
        let identity = UUID()
        let consent = AIProcessingConsent(id: UUID(), action: .importLink, identity: identity, server: "https://example.de")
        let client = APIClient(session: MockURLProtocol.makeSession())
        try await client.configure(server: "https://example.de", token: "first", sessionID: identity)
        try await client.configure(server: "https://example.de", token: "second", sessionID: UUID())
        let before = MockURLProtocol.requestCount
        do {
            _ = try await client.importURL("https://source.example/recipe", consent: consent)
            XCTFail("Consent from the old account must be rejected")
        } catch APIError.aiConsentRequired {}
        try await client.configure(server: "https://other.example", token: "first", sessionID: identity)
        do {
            _ = try await client.importURL("https://source.example/recipe", consent: consent)
            XCTFail("Consent from a different server must be rejected")
        } catch APIError.aiConsentRequired {}
        XCTAssertEqual(MockURLProtocol.requestCount, before)
    }

    func testConsentForDifferentActionCannotAuthorizeFileUpload() async throws {
        let identity = UUID()
        let client = APIClient(session: MockURLProtocol.makeSession())
        try await client.configure(server: "https://example.de", token: "synthetic", sessionID: identity)
        let consent = AIProcessingConsent(id: UUID(), action: .translation, identity: identity, server: "https://example.de")
        let before = MockURLProtocol.requestCount
        do {
            _ = try await client.importFile(data: Data([1]), filename: "photo.jpg", mimeType: "image/jpeg", consent: consent)
            XCTFail("A translation decision must not authorize uploading a photo")
        } catch APIError.aiConsentRequired {}
        XCTAssertEqual(MockURLProtocol.requestCount, before)
    }

    func testPendingSaveRequiresConsentButSkippingDoesNot() async throws {
        let client = APIClient(session: MockURLProtocol.makeSession())
        try await client.configure(server: "https://example.de", token: "synthetic")
        let before = MockURLProtocol.requestCount
        do {
            _ = try await client.resolvePending(url: "https://source.example/recipe", action: "save")
            XCTFail("Saving can trigger additional AI processing")
        } catch APIError.aiConsentRequired {}
        XCTAssertEqual(MockURLProtocol.requestCount, before)
        MockURLProtocol.respond(json: #"{"ok":true}"#)
        _ = try await client.resolvePending(url: "https://source.example/recipe", action: "skip")
        XCTAssertEqual(MockURLProtocol.requestCount, before + 1)
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: MockURLProtocol.lastBody()) as? [String: Any])
        XCTAssertNil(body["ai_processing_consent"])
    }

    @MainActor
    private func waitForPrompt(_ coordinator: AIConsentCoordinator) async {
        for _ in 0..<1_000 {
            if coordinator.pending != nil { return }
            await Task.yield()
        }
        XCTFail("The explicit consent prompt was not presented")
        coordinator.cancel()
    }
}
