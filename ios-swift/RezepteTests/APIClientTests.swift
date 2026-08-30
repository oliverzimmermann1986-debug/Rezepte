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

    func testGuestLoginUsesUnauthenticatedReadOnlyEndpoint() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: nil)
        MockURLProtocol.respond(json: """
        {"token":"guest-token","username":"Gast","expires_in":1209600,"read_only":true}
        """)

        let response = try await client.guestLogin()

        XCTAssertEqual(response.username, "Gast")
        XCTAssertEqual(response.readOnly, true)
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/auth/guest")
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

    func testCloudflareHTMLOnOriginalHostGetsSpecificError() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: nil)
        MockURLProtocol.respond(
            body: "<html><title>Cloudflare Access</title><a href='/cdn-cgi/access/login'>Login</a></html>",
            headers: ["Content-Type": "text/html; charset=utf-8"]
        )

        do {
            _ = try await client.login(username: "oliver", password: "password") as LoginResponse
            XCTFail("Cloudflare-HTML darf nicht als API-Antwort gelten")
        } catch let error as APIError {
            guard case .cloudflareAccessRequired = error else {
                return XCTFail("Erwartet cloudflareAccessRequired, war \(error)")
            }
        }
    }

    func testIncompatibleResponseNamesAffectedEndpoint() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "token")
        MockURLProtocol.respond(body: #"{"unexpected":true}"#)

        do {
            _ = try await client.cart()
            XCTFail("Inkompatible Antwort muss abgelehnt werden")
        } catch let error as APIError {
            guard case let .invalidResponse(endpoint) = error else {
                return XCTFail("Erwartet invalidResponse, war \(error)")
            }
            XCTAssertEqual(endpoint, "/api/cart")
            XCTAssertTrue(error.localizedDescription.contains("denselben Stand"))
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
        filters.allergenTagIDs = [13, 21]
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
        XCTAssertEqual(MockURLProtocol.lastQueryValues("tag_id"), ["2", "8", "13", "21"])
    }

    func testRecipeCountUsesLiveFilterEndpoint() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "token")
        MockURLProtocol.respond(json: #"{"total":17}"#)

        var filters = RecipeFilters()
        filters.includedIngredients = ["knoblauch"]
        filters.excludedIngredients = ["zwiebel"]
        filters.manualOnly = true

        let total = try await client.recipeCount(search: "pasta", filters: filters)
        let query = MockURLProtocol.lastQueryItems()

        XCTAssertEqual(total, 17)
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/recipes/count")
        XCTAssertEqual(query["search"], "pasta")
        XCTAssertEqual(query["ingredient"], "knoblauch")
        XCTAssertEqual(query["exclude_ingredient"], "zwiebel")
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

    func testRecipeDeleteMovesRecipeToTrashWithFiles() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "token")
        MockURLProtocol.respond(json: """
        {"ok":true}
        """)

        let result = try await client.deleteRecipe(id: 42)

        XCTAssertEqual(result.ok, true)
        XCTAssertEqual(MockURLProtocol.lastMethod(), "DELETE")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/recipes/42")
        XCTAssertEqual(MockURLProtocol.lastQueryItems()["delete_files"], "true")
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

    func testPendingSuggestionDecodesEditableRecipeContent() throws {
        let json = """
        {
          "url": "https://example.de/rezept",
          "description": "Originaltext",
          "ai_suggestion": {
            "name": "Kartoffelsuppe",
            "type": "Hauptgericht",
            "category": "Suppe",
            "confidence": 0.91,
            "servings": 4,
            "platform": "web",
            "has_thumbnail": true,
            "ingredients": [{"name":"Kartoffeln","amount":500,"unit":"g","raw":"500 g Kartoffeln"}],
            "steps": [{"instruction":"20 Minuten kochen","timer_seconds":1200}]
          }
        }
        """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase

        let item = try decoder.decode(PendingItem.self, from: Data(json.utf8))

        XCTAssertEqual(item.aiSuggestion?.servings, 4)
        XCTAssertEqual(item.aiSuggestion?.ingredients?.first?.name, "Kartoffeln")
        XCTAssertEqual(item.aiSuggestion?.steps?.first?.timerSeconds, 1200)
        XCTAssertEqual(item.aiSuggestion?.hasThumbnail, true)
    }

    func testResolvePendingSendsCompleteReviewedRecipe() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "token")
        MockURLProtocol.respond(json: #"{"ok":true}"#)

        _ = try await client.resolvePending(
            url: "https://example.de/rezept",
            action: "save",
            name: "Kartoffelsuppe",
            type: "Hauptgericht",
            category: "Suppe",
            description: "Cremig",
            ingredients: [PendingIngredient(name: "Kartoffeln", amount: 500, unit: "g", raw: nil)],
            steps: [PendingStep(instruction: "Kochen", timerSeconds: 1200)],
            servings: 4,
            verified: true
        )

        let data = MockURLProtocol.lastBody()
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/pending")
        XCTAssertEqual(body["servings"] as? Int, 4)
        XCTAssertEqual(body["verified"] as? Bool, true)
        XCTAssertEqual((body["ingredients"] as? [[String: Any]])?.first?["name"] as? String, "Kartoffeln")
        XCTAssertEqual((body["steps"] as? [[String: Any]])?.first?["timer_seconds"] as? Int, 1200)
    }

    func testPendingReanalysisUsesDedicatedEndpoint() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "token")
        MockURLProtocol.respond(json: """
        {"ok":true,"action":"still_pending","analysis":{"name":"Neu erkannt"}}
        """)

        let result = try await client.reanalyzePending(url: "https://example.de/rezept")

        XCTAssertEqual(result.analysis?.name, "Neu erkannt")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/pending/reanalyze")
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
    }

    func testCookingProgressAndCompletionUseNativeContracts() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "token")
        MockURLProtocol.respond(json: """
        {"recipe_id":42,"username":"oliver","completed_steps":[0,1],"active_step":2,"servings":3,"started_at":1,"updated_at":2,"exists":true,"step_count":4}
        """)

        let progress = try await client.updateCookingProgress(
            id: 42,
            completedSteps: [0, 1],
            activeStep: 2,
            servings: 3
        )

        XCTAssertEqual(progress.completedSteps, [0, 1])
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/recipes/42/cooking-progress")
        let progressBody = try XCTUnwrap(String(data: MockURLProtocol.lastBody(), encoding: .utf8))
        XCTAssertTrue(progressBody.contains("\"completed_steps\":[0,1]"))

        MockURLProtocol.respond(json: #"{"ok":true}"#)
        _ = try await client.completeCooking(
            id: 42,
            servings: 3,
            idempotencyKey: "stable-completion-id"
        )

        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/recipes/42/cooking-complete")
        XCTAssertEqual(MockURLProtocol.lastHeader("Idempotency-Key"), "stable-completion-id")
    }

    func testAddingRecipeToCartSendsSelectedServings() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "token")
        MockURLProtocol.respond(json: #"{"ok":true}"#)

        _ = try await client.addRecipeToCart(id: 42, servings: 6)

        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/cart/cook/42")
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        let body = try XCTUnwrap(String(data: MockURLProtocol.lastBody(), encoding: .utf8))
        XCTAssertTrue(body.contains("\"servings\":6"))
    }

    func testShoppingSuggestionsDecodeCatalogMetadata() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "token")
        MockURLProtocol.respond(json: """
        {"items":[{"canonical_name":"milch","name":"Milch","category":"Kühlregal","icon":"🥛","default_unit":"l","usage_count":4}]}
        """)

        let response = try await client.shoppingSuggestions(query: "mil")

        XCTAssertEqual(response.items.first?.name, "Milch")
        XCTAssertEqual(response.items.first?.icon, "🥛")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/cart/suggestions")
        XCTAssertEqual(MockURLProtocol.lastQueryItems()["q"], "mil")
    }

    func testManualCartItemSendsSupermarketCategory() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "token")
        MockURLProtocol.respond(json: #"{"ok":true}"#)

        _ = try await client.addCartItem(name: "Milch", amount: 1, unit: "l", category: "Kühlregal")

        let body = try XCTUnwrap(String(data: MockURLProtocol.lastBody(), encoding: .utf8))
        let payload = try XCTUnwrap(
            JSONSerialization.jsonObject(with: MockURLProtocol.lastBody()) as? [String: Any]
        )
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/cart/add")
        XCTAssertTrue(body.contains("\"category\":\"Kühlregal\""))
        XCTAssertEqual(payload["amount"] as? Double, 1)
        XCTAssertEqual(payload["unit"] as? String, "l")
    }

    func testRecurringCartDecodesScheduleAndSQLiteBoolean() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "token")
        MockURLProtocol.respond(json: """
        {"items":[{"id":7,"name":"Milch","amount":2,"default_unit":"l","category":"Kühlregal","icon":"🥛","interval_days":7,"next_due_on":"2026-09-01","due_in_days":4,"active":1}]}
        """)

        let response = try await client.recurringCart()

        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/cart/recurring")
        XCTAssertEqual(response.items.first?.name, "Milch")
        XCTAssertEqual(response.items.first?.defaultUnit, "l")
        XCTAssertEqual(response.items.first?.intervalDays, 7)
        XCTAssertEqual(response.items.first?.isActive, true)
    }

    func testCreatingRecurringCartItemSendsFullSchedule() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "token")
        MockURLProtocol.respond(json: #"{"ok":true}"#)

        _ = try await client.createRecurringCartItem(
            name: "Milch",
            amount: 2,
            unit: "l",
            category: "Kühlregal",
            intervalDays: 7,
            nextDueOn: "2026-09-01",
            active: true
        )

        let payload = try XCTUnwrap(
            JSONSerialization.jsonObject(with: MockURLProtocol.lastBody()) as? [String: Any]
        )
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/cart/recurring")
        XCTAssertEqual(payload["default_unit"] as? String, "l")
        XCTAssertEqual(payload["interval_days"] as? Int, 7)
        XCTAssertEqual(payload["next_due_on"] as? String, "2026-09-01")
        XCTAssertEqual(payload["active"] as? Bool, true)
    }

    func testRecurringCartCanBePausedAndMaterialized() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "token")
        MockURLProtocol.respond(json: #"{"ok":true}"#)

        _ = try await client.setRecurringCartItem(id: 7, active: false)

        XCTAssertEqual(MockURLProtocol.lastMethod(), "PATCH")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/cart/recurring/7")
        let pausePayload = try XCTUnwrap(
            JSONSerialization.jsonObject(with: MockURLProtocol.lastBody()) as? [String: Any]
        )
        XCTAssertEqual(pausePayload["active"] as? Bool, false)

        MockURLProtocol.respond(json: #"{"ok":true,"added":[],"count":2}"#)
        let result = try await client.runRecurringCart()

        XCTAssertEqual(result.count, 2)
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/cart/recurring/run")
    }

    func testImageBackfillUsesBackupFirstEndpoint() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "token")
        MockURLProtocol.respond(json: """
        {"ok":true,"task_id":14,"run_id":8,"batch_id":"safe-batch"}
        """)

        let result = try await client.startImageBackfill()

        XCTAssertEqual(result.runId, 8)
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/recipes/images/backfill")
    }

    func testImageBackupRequestCarriesAuthentication() async throws {
        let client = APIClient()
        try await client.configure(server: "https://example.de", token: "api-token")

        let request = try await client.imageBackupRequest(backupID: 19)

        XCTAssertEqual(request.url?.path, "/api/recipes/image-backups/19/file")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer api-token")
    }

    func testSystemInfoDecodesCapabilitiesWithoutBearerToken() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "api-token")
        MockURLProtocol.respond(json: """
        {"name":"Rezepte","version":"1.6.0","capabilities":["recurring-shopping","weekly-meal-plan"]}
        """)

        let info = try await client.systemInfo()

        XCTAssertEqual(info.version, "1.6.0")
        XCTAssertTrue(info.capabilities.contains("recurring-shopping"))
        XCTAssertNil(MockURLProtocol.lastHeader("Authorization"))
    }

    func testRecipeRatingKeepsQueryWhenPostingJsonBody() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "api-token")
        MockURLProtocol.respond(json: #"{"ok":true}"#)

        _ = try await client.setRecipeRating(id: 42, value: 5)

        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/recipes/42/rating")
        XCTAssertEqual(MockURLProtocol.lastQueryItems()["value"], "5")
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
    }

    func testSourceIntegrityCheckUsesDedicatedNonDestructiveEndpoint() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "api-token")
        MockURLProtocol.respond(json: """
        {"recipe_id":42,"recipe_name":"Pasta","source_url":"https://example.de/pasta","platform":"Webseite","status":"current","quality":{"status":"review","score":96,"issues":[],"checked_rules":8},"verified":false,"automatic_overwrite":false}
        """)

        let report = try await client.checkRecipeSourceIntegrity(id: 42)

        XCTAssertEqual(report.status, "current")
        XCTAssertFalse(report.automaticOverwrite)
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/recipes/42/source-integrity/check")
    }

    func testShoppingOptimizationPreviewDecodesServerContract() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "api-token")
        MockURLProtocol.respond(json: """
        {"preview_id":"abcdefghijklmnopqrstuvwxyz","items":[{"name":"Burrata","amount":1,"unit":"Stück","category":"Kühlregal"}],"summary":{"original_count":2,"optimized_count":1,"merged_count":1,"renamed_count":0,"categorized_count":1},"categories":["Kühlregal"],"expires_in_seconds":900}
        """)

        let preview = try await client.shoppingOptimizationPreview()

        XCTAssertEqual(preview.items.first?.category, "Kühlregal")
        XCTAssertEqual(preview.summary.mergedCount, 1)
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/cart/optimize/preview")
    }

    func testMealConductorSendsResourcesAndDecodesTimeline() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "api-token")
        MockURLProtocol.respond(json: """
        {"planned_for":"2026-07-27","serve_at":"2026-07-27T19:00","serve_time":"19:00","start_at":"2026-07-27T18:30","events":[{"id":"42-1","recipe_id":42,"recipe_name":"Pasta","planned_servings":2,"step_number":1,"instruction":"Kochen","resource":"burner","duration_minutes":30,"estimated":false,"resource_adjusted":false,"start_at":"2026-07-27T18:30","end_at":"2026-07-27T19:00","start_time":"18:30","end_time":"19:00"}],"warnings":[],"summary":{"recipes":1,"steps":1,"estimated_steps":0,"resource_adjustments":0,"burners":2,"oven_slots":1}}
        """)

        let plan = try await client.mealConductorPreview(
            date: "2026-07-27",
            serveAt: "19:00",
            burners: 2,
            ovenSlots: 1
        )

        XCTAssertEqual(plan.events.first?.resource, "burner")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/meal-plan/conductor/preview")
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        let payload = try XCTUnwrap(
            JSONSerialization.jsonObject(with: MockURLProtocol.lastBody()) as? [String: Any]
        )
        XCTAssertEqual(payload["serve_at"] as? String, "19:00")
        XCTAssertEqual(payload["burners"] as? Int, 2)
    }

    func testSubstitutionApplyCreatesNamedVariantThroughDedicatedEndpoint() async throws {
        let session = MockURLProtocol.makeSession()
        let client = APIClient(session: session)
        try await client.configure(server: "https://example.de", token: "api-token")
        MockURLProtocol.respond(json: """
        {"ok":true,"recipe_id":84,"name":"Pasta mit Haferdrink","substitution":{"ingredient_id":9,"from_name":"Milch","to_name":"Haferdrink","ratio":1.0,"review_required":true,"nutrition_invalidated":true}}
        """)

        let result = try await client.applyRecipeSubstitution(
            id: 42,
            ingredientID: 9,
            candidateID: "milk-oat-drink",
            variantName: "Pasta mit Haferdrink"
        )

        XCTAssertEqual(result.recipeId, 84)
        XCTAssertTrue(result.substitution.reviewRequired)
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/recipes/42/substitutions/apply")
        let payload = try XCTUnwrap(
            JSONSerialization.jsonObject(with: MockURLProtocol.lastBody()) as? [String: Any]
        )
        XCTAssertEqual(payload["ingredient_id"] as? Int, 9)
        XCTAssertEqual(payload["candidate_id"] as? String, "milk-oat-drink")
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

    static func lastQueryValues(_ name: String) -> [String] {
        guard let url = lastRequestURL,
              let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            return []
        }
        return (components.queryItems ?? [])
            .filter { $0.name == name }
            .compactMap(\.value)
    }

    static func lastHeader(_ name: String) -> String? {
        lastRequestHeaders.first { $0.key.caseInsensitiveCompare(name) == .orderedSame }?.value
    }

    static func lastBody() -> Data { requestBody }
    static func lastMethod() -> String { requestMethod }
    static func lastPath() -> String? { lastRequestURL?.path }

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
