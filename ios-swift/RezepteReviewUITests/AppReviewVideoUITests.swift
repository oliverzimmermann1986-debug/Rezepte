import XCTest

final class AppReviewVideoUITests: XCTestCase {
    private let app = XCUIApplication()

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testReviewTour() throws {
        let environment = ProcessInfo.processInfo.environment
        let server = environment["APP_REVIEW_SERVER"] ?? "https://rezepte-review.mausbaeren.me"
        let username = environment["APP_REVIEW_USERNAME"] ?? "app-review"
        guard let password = environment["APP_REVIEW_PASSWORD"], !password.isEmpty else {
            throw XCTSkip("APP_REVIEW_PASSWORD is required for the recorded review tour.")
        }

        app.launchArguments += ["-AppleLanguages", "(de)", "-AppleLocale", "de_DE"]
        app.launchEnvironment["APP_REVIEW_AUTOMATION"] = "1"
        app.launchEnvironment["APP_REVIEW_SERVER"] = server
        app.launchEnvironment["APP_REVIEW_USERNAME"] = username
        app.launchEnvironment["APP_REVIEW_PASSWORD"] = password
        app.launch()

        let serverField = app.textFields["review.server"]
        XCTAssertTrue(serverField.waitForExistence(timeout: 20), "The review login screen did not appear.")
        XCTAssertTrue(app.textFields["review.username"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.secureTextFields["review.password"].waitForExistence(timeout: 10))
        pause(2)

        let loginButton = app.buttons["Anmelden"]
        XCTAssertTrue(loginButton.waitForExistence(timeout: 10))
        XCTAssertTrue(loginButton.isEnabled, "The prefilled review login is incomplete.")
        reveal(loginButton, maximumSwipes: 3)
        loginButton.tap()
        let archiveTab = app.tabBars.buttons["Archiv"]
        XCTAssertTrue(archiveTab.waitForExistence(timeout: 35), "Login to the isolated review server failed.")
        XCTAssertTrue(app.navigationBars["Heute"].waitForExistence(timeout: 20))
        pause(4)
        capture("01-heute")

        // The home shortcut must open this week's wishes directly.
        let wishesShortcut = app.buttons["today.wishes"]
        reveal(wishesShortcut, maximumSwipes: 4)
        wishesShortcut.tap()
        XCTAssertTrue(app.navigationBars["Wochenwünsche"].waitForExistence(timeout: 20))
        XCTAssertFalse(app.navigationBars["Wochenplan"].exists,
                       "The home shortcut must not require an intermediate planner screen.")
        pause(3)
        app.buttons["Fertig"].tap()
        XCTAssertTrue(app.navigationBars["Heute"].waitForExistence(timeout: 10))
        archiveTab.tap()
        XCTAssertTrue(app.navigationBars["Archiv"].waitForExistence(timeout: 20))
        pause(4)
        capture("02-rezepte")

        let recipe = app.staticTexts["Zitronen-Ricotta-Pasta"].firstMatch
        reveal(recipe, maximumSwipes: 4)
        recipe.tap()
        let passportTitle = app.staticTexts["Rezeptpass"]
        XCTAssertTrue(passportTitle.waitForExistence(timeout: 20))
        pause(4)
        capture("03-rezept")

        reveal(passportTitle)
        pause(4)
        reveal(app.staticTexts["Originalquelle"])
        pause(5)
        verifyRecipeReviewActions()

        app.navigationBars.buttons["Archiv"].tap()
        XCTAssertTrue(archiveTab.waitForExistence(timeout: 10))
        app.buttons["Sammlung & Import"].tap()
        app.buttons["Meine Kochbücher"].tap()
        XCTAssertTrue(app.navigationBars["Kochbücher"].waitForExistence(timeout: 20))
        pause(4)
        capture("07-kochbuecher")
        app.buttons["Fertig"].tap()

        let planTab = app.tabBars.buttons["Plan"]
        planTab.tap()
        XCTAssertTrue(app.navigationBars["Wochenplan"].waitForExistence(timeout: 20))
        pause(4)
        capture("04-wochenplan")
        app.buttons["Wochenwünsche"].tap()
        XCTAssertTrue(app.navigationBars["Wochenwünsche"].waitForExistence(timeout: 20))
        pause(4)
        capture("08-wochenwuensche")
        app.buttons["Fertig"].tap()
        verifyWeeklyPlanningOptions()

        let todayTab = app.tabBars.buttons["Heute"]
        todayTab.tap()
        XCTAssertTrue(app.navigationBars["Heute"].waitForExistence(timeout: 20))
        pause(5)

        let shoppingTab = app.tabBars.buttons["Einkauf"]
        shoppingTab.tap()
        XCTAssertTrue(app.navigationBars["Einkauf"].waitForExistence(timeout: 20))
        pause(3)
        capture("05-einkauf")
        verifyShoppingUndo()
        let recurring = app.segmentedControls.buttons["Wiederkehrend"]
        XCTAssertTrue(recurring.waitForExistence(timeout: 10))
        recurring.tap()
        XCTAssertTrue(app.staticTexts["Hafermilch"].waitForExistence(timeout: 20))
        pause(5)

        let settingsTab = app.tabBars.buttons["Einstellungen"]
        settingsTab.tap()
        XCTAssertTrue(app.navigationBars["Einstellungen"].waitForExistence(timeout: 20))
        pause(4)
        capture("06-einstellungen")
        verifyAutomaticLinkImportDisclosure()

        let administration = app.buttons["Administration öffnen"]
        reveal(administration, maximumSwipes: 6)
        administration.tap()
        XCTAssertTrue(app.navigationBars["Administration"].waitForExistence(timeout: 20))
        pause(4)

        let adminSettings = app.staticTexts["Admin-Einstellungen"].firstMatch
        reveal(adminSettings, maximumSwipes: 6)
        adminSettings.tap()
        XCTAssertTrue(app.navigationBars["Admin-Einstellungen"].waitForExistence(timeout: 20))
        reveal(app.staticTexts["Sicherheitsgrenzen"], maximumSwipes: 4)
        pause(5)
        app.swipeUp()
        pause(5)
    }

    private func verifyRecipeReviewActions() {
        let actions = app.buttons["Weitere Rezeptaktionen"]
        XCTAssertTrue(actions.waitForExistence(timeout: 10))
        actions.tap()
        XCTAssertTrue(app.buttons["Rezept löschen"].firstMatch.waitForExistence(timeout: 5))
        let portions = app.buttons["Portionsanzahl mit KI prüfen"]
        XCTAssertTrue(portions.waitForExistence(timeout: 5))
        portions.tap()
        XCTAssertTrue(app.navigationBars["Portionen prüfen"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["Mit KI prüfen"].waitForExistence(timeout: 10))
        pause(4)
        // Opening the review explains the feature without submitting any paid AI work.
        app.buttons["Schließen"].tap()
        XCTAssertTrue(actions.waitForExistence(timeout: 10))
    }

    private func verifyWeeklyPlanningOptions() {
        let suggestions = app.buttons["Vorschlagen"]
        XCTAssertTrue(suggestions.waitForExistence(timeout: 10))
        reveal(suggestions, maximumSwipes: 3)
        suggestions.tap()
        XCTAssertTrue(app.navigationBars["Woche vorschlagen"].waitForExistence(timeout: 10))

        let profiles = app.buttons["Wochenprofile"]
        XCTAssertTrue(profiles.waitForExistence(timeout: 10))
        profiles.tap()
        XCTAssertTrue(app.navigationBars["Wochenprofile"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.textFields["Name, z. B. Schnelle Woche"].waitForExistence(timeout: 10))
        pause(3)
        app.buttons["Schließen"].tap()
        XCTAssertTrue(app.navigationBars["Woche vorschlagen"].waitForExistence(timeout: 10))

        let preferences = app.staticTexts["Pflege eure Vorlieben mit dem Herz und den Sternen im Rezept."]
        reveal(preferences, maximumSwipes: 5)
        pause(3)
        let quotaPreset = app.buttons["7 Gerichte: 2 × Pasta, 4 × Kartoffeln"]
        reveal(quotaPreset, maximumSwipes: 3)
        quotaPreset.tap()
        pause(4)

        let history = app.staticTexts.matching(NSPredicate(
            format: "label == %@",
            "Ausgewertet werden deine gespeicherten Wochenpläne vor der ausgewählten Woche. Das gilt auch beim Austauschen eines Gerichts. Reichen andere Rezepte nicht aus, bleiben Plätze offen."
        )).firstMatch
        reveal(history, maximumSwipes: 6)
        pause(4)
        let monday = app.staticTexts["Montag"].firstMatch
        reveal(monday, maximumSwipes: 3)
        monday.tap()
        XCTAssertTrue(app.navigationBars["Montag"].waitForExistence(timeout: 10))
        let schedule = app.switches["Gericht für diesen Tag einplanen"]
        XCTAssertTrue(schedule.waitForExistence(timeout: 10))
        schedule.tap()
        let vegetarian = app.switches["Vegetarisch"]
        XCTAssertTrue(vegetarian.waitForExistence(timeout: 10))
        vegetarian.tap()
        pause(4)
        app.navigationBars["Montag"].buttons.firstMatch.tap()
        XCTAssertTrue(app.navigationBars["Woche vorschlagen"].waitForExistence(timeout: 10))
        // These are unsaved draft preferences: preserve the household's existing plan.
        app.buttons["Fertig"].tap()
        XCTAssertTrue(app.navigationBars["Wochenplan"].waitForExistence(timeout: 10))
    }

    private func verifyAutomaticLinkImportDisclosure() {
        let enable = app.buttons["settings.link-imports.enable"]
        reveal(enable, maximumSwipes: 5)
        XCTAssertTrue(enable.isEnabled, "The review account must support automatic link imports.")
        enable.tap()
        XCTAssertTrue(app.navigationBars["KI-Verarbeitung"].waitForExistence(timeout: 10))
        let explanation = app.staticTexts["Einmal zustimmen, Links automatisch importieren"]
        reveal(explanation, maximumSwipes: 5)
        pause(4)
        let approve = app.buttons["ai-consent.approve"]
        reveal(approve, maximumSwipes: 6)
        XCTAssertEqual(approve.label, "Zustimmen und automatisch importieren")
        XCTAssertFalse(app.buttons["ai-consent.approve-once"].exists,
                       "Settings must explain the lasting link-import permission.")
        pause(3)
        // Show the scope and revocation instructions, but never grant AI permission in a media run.
        app.navigationBars["KI-Verarbeitung"].buttons["Abbrechen"].tap()
        XCTAssertTrue(app.navigationBars["Einstellungen"].waitForExistence(timeout: 10))
        XCTAssertTrue(enable.waitForExistence(timeout: 10), "Cancelling must leave automatic imports disabled.")
        XCTAssertFalse(app.buttons["settings.link-imports.revoke"].exists)
    }

    private func verifyShoppingUndo() {
        let name = "UX-Prüfung 14102"
        let input = app.textFields["Was fehlt?"]
        reveal(input, maximumSwipes: 3)
        input.tap()
        input.typeText(name + "\n")
        // A recent-product suggestion may keep the same name after deletion.
        // Only a real cart row contains its item-specific quantity editor.
        let item = app.cells.containing(.button, identifier: "Menge von \(name) bearbeiten").firstMatch
        XCTAssertTrue(item.waitForExistence(timeout: 15))
        reveal(item, maximumSwipes: 4)
        item.swipeLeft()
        let delete = app.buttons["Löschen"].firstMatch
        XCTAssertTrue(delete.waitForExistence(timeout: 5))
        delete.tap()
        XCTAssertTrue(waitForAbsence(item))

        let undo = app.buttons["cart.undo"]
        XCTAssertTrue(undo.waitForExistence(timeout: 10))
        XCTAssertTrue(undo.isHittable, "Undo must be visible without scrolling to the top.")
        capture("09-einkauf-rueckgaengig")
        undo.tap()
        XCTAssertTrue(item.waitForExistence(timeout: 15), "Undo did not restore the deleted item.")

        // Remove only the synthetic item created above; preserve existing demo purchases.
        reveal(item, maximumSwipes: 4)
        item.swipeLeft()
        XCTAssertTrue(delete.waitForExistence(timeout: 5))
        delete.tap()
        XCTAssertTrue(waitForAbsence(item))
    }

    private func waitForAbsence(_ element: XCUIElement) -> Bool {
        let expectation = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: element)
        return XCTWaiter.wait(for: [expectation], timeout: 10) == .completed
    }

    private func reveal(_ element: XCUIElement, maximumSwipes: Int = 6) {
        for _ in 0..<maximumSwipes where !element.isHittable {
            app.swipeUp()
        }
        XCTAssertTrue(element.exists && element.isHittable, "Expected review section is not visible.")
    }

    private func pause(_ seconds: TimeInterval) {
        Thread.sleep(forTimeInterval: seconds)
    }

    private func capture(_ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
