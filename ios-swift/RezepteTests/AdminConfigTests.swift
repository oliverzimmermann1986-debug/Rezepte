import Foundation
import XCTest
@testable import Rezepte

final class AdminConfigTests: XCTestCase {
    func testDecodesMaskedConfigurationWithoutExposingSecrets() throws {
        let json = """
        {
          "ai": {
            "openai": {"api_key":"********","model":"gpt-4o-mini","base_url":"https://api.openai.com/v1","timeout":30},
            "confidence_threshold":0.75,
            "auto_translate":true,
            "video_fallback":{"enabled":true,"max_frames":10,"max_seconds":600,"transcription_model":"gpt-4o-mini-transcribe"},
            "image_generation":{"enabled":true,"model":"gpt-image-2","size":"1536x1024","quality":"medium","output_format":"jpeg"}
          },
          "mail": {
            "recipe":{"enabled":true,"imap_host":"imap.example.com","imap_port":993,"username":"recipes@example.com","password":"********","folder":"INBOX","max_mails":20,"attachment_max_mb":25},
            "wedding":{"enabled":false,"imap_host":"imap.example.com","imap_port":993,"username":"wedding@example.com","password":"********","folder":"INBOX","max_mails":20,"attachment_max_mb":25,"default_category":"Sonstiges","always_pending":true}
          },
          "pdf":{"auto_rotate":true,"use_tesseract_osd":true,"scan_dpi":300,"keep_original":true},
          "einkauf":{"api_url":"https://einkauf.example.com","app_token":"********","cf_access_client_id":"client-id","cf_access_client_secret":"********","auto_consolidate":true}
        }
        """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase

        let config = try decoder.decode(NativeAdminConfig.self, from: Data(json.utf8))

        XCTAssertEqual(config.ai?.openai?.apiKey, "********")
        XCTAssertEqual(config.mail?.recipe?.imapHost, "imap.example.com")
        XCTAssertEqual(config.pdf?.scanDpi, 300)
        XCTAssertEqual(config.einkauf?.apiUrl, "https://einkauf.example.com")
    }

    func testSafePatchOmitsSecretsAndServerManagedTargets() throws {
        let patch = makePatch()
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase

        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: encoder.encode(patch)) as? [String: Any]
        )
        let ai = try XCTUnwrap(object["ai"] as? [String: Any])
        let openAI = try XCTUnwrap(ai["openai"] as? [String: Any])
        let einkauf = try XCTUnwrap(object["einkauf"] as? [String: Any])
        let mail = try XCTUnwrap(object["mail"] as? [String: Any])
        let recipeMail = try XCTUnwrap(mail["recipe"] as? [String: Any])

        XCTAssertNil(object["paths"])
        XCTAssertNil(openAI["api_key"])
        XCTAssertNil(openAI["base_url"])
        XCTAssertNil(einkauf["app_token"])
        XCTAssertNil(einkauf["cf_access_client_secret"])
        XCTAssertNil(einkauf["api_url"])
        XCTAssertNil(recipeMail["password"])
        XCTAssertEqual(openAI["model"] as? String, "gpt-4o-mini")
    }

    func testAPIClientUsesAdminConfigPatchEndpoint() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "admin-token")
        MockURLProtocol.respond(json: #"{"ok":true}"#)

        _ = try await client.updateAdminConfiguration(makePatch())

        XCTAssertEqual(MockURLProtocol.lastMethod(), "PUT")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/config")
        XCTAssertEqual(MockURLProtocol.lastHeader("Authorization"), "Bearer admin-token")
    }

    func testMaintenanceModelsDecodeAndSortNewestBackupFirst() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let stats = try decoder.decode(
            NativeAdminLogStats.self,
            from: Data(#"{"path":"/logs","exists":true,"count":4,"total_bytes":2048,"oldest_age_days":12.5,"retention_days":30}"#.utf8)
        )
        let backups = try decoder.decode(
            NativeAdminBackupList.self,
            from: Data(#"{"tiers":{"daily":[{"name":"older.db","path":"/backups/older.db","size_bytes":100,"mtime":10},{"name":"newer.db","path":"/backups/newer.db","size_bytes":200,"mtime":20}]},"backups_root":"/backups"}"#.utf8)
        )

        XCTAssertEqual(stats.totalBytes, 2_048)
        XCTAssertEqual(stats.retentionDays, 30)
        XCTAssertEqual(backups.allBackups.map(\.name), ["newer.db", "older.db"])
    }

    func testAPIClientUsesDedicatedValidatedScheduleEndpoint() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "admin-token")
        MockURLProtocol.respond(json: #"{"scraper":{"ok":true,"error":null,"next_runs":[]}}"#)

        _ = try await client.previewAdminSchedule("*:0/30")

        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/schedule/preview")
    }

    func testAPIClientUsesExplicitMaintenanceEndpoints() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "admin-token")

        MockURLProtocol.respond(json: #"{"ok":true,"stdout":"backup complete","stderr":"","error":null}"#)
        _ = try await client.runAdminBackup()
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/config/backups/run-now")

        MockURLProtocol.respond(json: #"{"ok":true,"stdout":"cleanup complete","stderr":"","error":null}"#)
        _ = try await client.cleanupAdminLogs(days: 30)
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/config/logs/cleanup")
        XCTAssertEqual(MockURLProtocol.lastQueryItems()["days"], "30")
    }

    private func makePatch() -> NativeAdminConfigPatch {
        NativeAdminConfigPatch(
            ai: NativeAdminAIConfigPatch(
                openai: NativeAdminOpenAIConfigPatch(apiKey: nil, model: "gpt-4o-mini", timeout: 30),
                confidenceThreshold: 0.75,
                autoTranslate: true,
                videoFallback: NativeAdminVideoFallbackConfigPatch(
                    enabled: true,
                    maxFrames: 10,
                    maxSeconds: 600,
                    transcriptionModel: "gpt-4o-mini-transcribe"
                ),
                imageGeneration: NativeAdminImageGenerationConfigPatch(
                    enabled: true,
                    model: "gpt-image-2",
                    size: "1536x1024",
                    quality: "medium",
                    outputFormat: "jpeg"
                )
            ),
            mail: NativeAdminMailConfigPatch(
                recipe: NativeAdminMailAccountConfigPatch(
                    enabled: true,
                    imapHost: "imap.example.com",
                    imapPort: 993,
                    username: "recipes@example.com",
                    password: nil,
                    folder: "INBOX",
                    maxMails: 20,
                    attachmentMaxMb: 25,
                    defaultCategory: nil,
                    alwaysPending: nil
                ),
                wedding: NativeAdminMailAccountConfigPatch(
                    enabled: false,
                    imapHost: "imap.example.com",
                    imapPort: 993,
                    username: "wedding@example.com",
                    password: nil,
                    folder: "INBOX",
                    maxMails: 20,
                    attachmentMaxMb: 25,
                    defaultCategory: "Sonstiges",
                    alwaysPending: true
                )
            ),
            pdf: NativeAdminPDFConfigPatch(
                autoRotate: true,
                useTesseractOsd: true,
                useOcrVote: true,
                removeBlankPages: true,
                autoCrop: true,
                deskewScans: true,
                ocrScans: true,
                ocrLanguage: "deu+eng",
                improveContrast: true,
                sharpenScans: true,
                scanDpi: 300,
                keepOriginal: true
            ),
            einkauf: NativeAdminEinkaufConfigPatch(
                appToken: nil,
                cfAccessClientId: "client-id",
                cfAccessClientSecret: nil,
                autoConsolidate: true
            )
        )
    }
}
