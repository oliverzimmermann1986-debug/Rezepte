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
        let localFixture = environment["APP_REVIEW_LOCAL_FIXTURE"] == "1"
        if localFixture {
            // Mutations are permitted only in the disposable loopback fixture.
            // Fail closed before login if the opt-in was copied to a remote run.
            let url = URL(string: server)
            guard url?.scheme == "https", url?.host == "localhost",
                  url?.user == nil, url?.password == nil else {
                XCTFail("Local persistence checks require exactly an HTTPS localhost server.")
                return
            }
        }
        guard let password = environment["APP_REVIEW_PASSWORD"], !password.isEmpty else {
            throw XCTSkip("APP_REVIEW_PASSWORD is required for the recorded review tour.")
        }

        app.launchArguments += ["-AppleLanguages", "(de)", "-AppleLocale", "de_DE"]
        app.launchEnvironment["APP_REVIEW_AUTOMATION"] = "1"
        app.launchEnvironment["APP_REVIEW_SERVER"] = server
        app.launchEnvironment["APP_REVIEW_USERNAME"] = username
        app.launchEnvironment["APP_REVIEW_PASSWORD"] = password
        app.launch()

        XCTAssertTrue(app.textFields["review.server"].waitForExistence(timeout: 20))
        XCTAssertTrue(app.textFields["review.username"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.secureTextFields["review.password"].waitForExistence(timeout: 10))
        let loginButton = app.buttons["Anmelden"]
        XCTAssertTrue(loginButton.waitForExistence(timeout: 10))
        XCTAssertTrue(loginButton.isEnabled, "The protected review login is incomplete.")
        loginButton.tap()

        let archiveTab = app.tabBars.buttons["Archiv"]
        XCTAssertTrue(archiveTab.waitForExistence(timeout: 35), "Review login failed.")
        XCTAssertTrue(app.tabBars.buttons["Eingang"].exists, "The fixture requires a writable review account.")
        app.tabBars.buttons["Eingang"].tap()
        XCTAssertTrue(app.navigationBars["Eingang"].waitForExistence(timeout: 20))
        capture("01-eingang")

        archiveTab.tap()
        XCTAssertTrue(app.navigationBars["Archiv"].waitForExistence(timeout: 20))
        let recipe = app.staticTexts["Zitronen-Ricotta-Pasta"].firstMatch
        reveal(recipe, maximumSwipes: 8)
        capture("02-archiv")
        recipe.tap()
        let passport = app.staticTexts["Rezeptpass"]
        XCTAssertTrue(passport.waitForExistence(timeout: 20))
        capture("03-rezept-mit-quelle")

        let importReview = element("recipeImportReview")
        reveal(importReview)
        importReview.tap()
        XCTAssertTrue(element("importReviewSource").waitForExistence(timeout: 20))
        reveal(element("importReviewSource"))
        capture("04-import-originalquelle")
        let ingredientsPage = app.segmentedControls.buttons["Zutaten"]
        XCTAssertTrue(ingredientsPage.waitForExistence(timeout: 10))
        ingredientsPage.tap()
        reveal(element("importReviewIngredient-0"))
        capture("05-import-zutaten-nacharbeiten")
        let importClose = element("importReviewClose")
        XCTAssertTrue(importClose.waitForExistence(timeout: 10))
        importClose.tap()

        let memoryAdd = element("cookMemoryAdd")
        reveal(memoryAdd)
        memoryAdd.tap()
        let note = element("cookMemoryNote")
        XCTAssertTrue(note.waitForExistence(timeout: 20))
        fill(note, with: "Beim ersten Kochen war die Sauce etwas dick.")
        fill(element("cookMemoryAdjustments"), with: "Zwei Esslöffel Nudelwasser ergänzt.")
        fill(element("cookMemoryNextTime"), with: "Nudelwasser vor dem Abgießen auffangen.")
        dismissKeyboard()
        capture("06-persoenliches-kochgedaechtnis-entwurf")
        let memoryCancel = element("cookMemoryCancel")
        XCTAssertTrue(memoryCancel.waitForExistence(timeout: 10))
        memoryCancel.tap()

        let backToArchive = app.navigationBars.buttons["Archiv"]
        XCTAssertTrue(backToArchive.waitForExistence(timeout: 10))
        backToArchive.tap()
        let localLibrary = element("offlineLibraryButton")
        XCTAssertTrue(localLibrary.waitForExistence(timeout: 10))
        localLibrary.tap()
        XCTAssertTrue(app.navigationBars["Offline-Regal"].waitForExistence(timeout: 20))
        let localRecipe = app.staticTexts["Zitronen-Ricotta-Pasta"].firstMatch
        XCTAssertTrue(localRecipe.waitForExistence(timeout: 15), "The opened recipe was not cached.")
        capture("07-auf-diesem-iphone")
        localRecipe.tap()
        XCTAssertTrue(app.staticTexts["Zitronen-Ricotta-Pasta"].firstMatch.waitForExistence(timeout: 15))
        capture("08-lokales-rezept")

        app.tabBars.buttons["Heute"].tap()
        XCTAssertTrue(app.navigationBars["Heute"].waitForExistence(timeout: 20))
        capture("09-heute")
        app.tabBars.buttons["Einkauf"].tap()
        XCTAssertTrue(app.navigationBars["Einkauf"].waitForExistence(timeout: 20))
        capture("10-einkauf")
        let recurring = app.segmentedControls.buttons["Wiederkehrend"]
        XCTAssertTrue(recurring.waitForExistence(timeout: 10))
        recurring.tap()
        XCTAssertTrue(app.staticTexts["Hafermilch"].waitForExistence(timeout: 20))
        capture("11-wiederkehrender-bedarf")

        if localFixture {
            exerciseLocalPersistence(server: server)
        }
    }

    private func exerciseLocalPersistence(server: String) {
        // This function is reached only after the strict opt-in/localhost guard.
        // All edits use the public UI and normal authentication/authorization.
        reopenRecipe()
        let memoryAdd = element("cookMemoryAdd")
        reveal(memoryAdd)
        memoryAdd.tap()
        let note = element("cookMemoryNote")
        XCTAssertTrue(note.waitForExistence(timeout: 15))
        XCTAssertEqual(note.value as? String, "Beim ersten Kochen war die Sauce etwas dick.",
                       "The draft did not survive closing the sheet and restarting the app.")
        let saveMemory = element("cookMemorySave")
        reveal(saveMemory)
        XCTAssertTrue(saveMemory.isEnabled)
        saveMemory.tap()
        let syncedMemory = app.descendants(matching: .any).matching(
            NSPredicate(format: "identifier BEGINSWITH %@", "cookMemorySaved-")
        ).firstMatch
        XCTAssertTrue(syncedMemory.waitForExistence(timeout: 30),
                      "The note remained pending instead of being acknowledged by the local server.")
        reopenRecipe()
        let savedNote = app.staticTexts["Beim ersten Kochen war die Sauce etwas dick."].firstMatch
        reveal(savedNote)
        XCTAssertTrue(syncedMemory.exists, "The acknowledged note did not survive restart.")
        capture("12-kochgedaechtnis-gespeichert-und-neu-geladen")

        // Reload the detail at its beginning so all subsequent sections scroll forward.
        reopenRecipe()
        openImportReview()
        let originalSource = app.staticTexts["\(server)/static/review-source-zitronen-ricotta-pasta.html"]
        XCTAssertTrue(originalSource.waitForExistence(timeout: 15))
        app.segmentedControls.buttons["Zutaten"].tap()
        let ingredient = element("importReviewIngredient-0")
        reveal(ingredient)
        XCTAssertEqual(ingredient.value as? String, "Pasta")
        capture("13-import-vorher")
        replace(ingredient, with: "Pasta nach Wahl")
        dismissKeyboard(identifier: "importReviewHideKeyboard")
        let completionPage = app.segmentedControls.buttons["Abschluss"]
        reveal(completionPage, direction: .down)
        completionPage.tap()
        let comparison = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "250 g Pasta nach Wahl")).firstMatch
        reveal(comparison)
        capture("14-import-vergleich-vor-dem-speichern")
        let reason = element("importReviewReason")
        fill(reason, with: "Zutatenbezeichnung mit dem gespeicherten Originaltext abgeglichen; Menge unverändert.")
        dismissKeyboard(identifier: "importReviewHideKeyboard")
        let saveImport = element("importReviewSave")
        reveal(saveImport)
        XCTAssertTrue(saveImport.isEnabled, "The isolated admin must be able to apply a valid correction.")
        saveImport.tap()
        let importSuccess = element("importReviewSuccess")
        // Form may not expose its first section while the save button is scrolled
        // into view. Return toward the header before asserting the server result.
        reveal(importSuccess, direction: .down)
        XCTAssertTrue(importSuccess.label.contains("Korrektur übernommen"))
        capture("15-import-korrektur-uebernommen")
        element("importReviewClose").tap()
        reopenRecipe()
        openImportReview()
        XCTAssertTrue(originalSource.waitForExistence(timeout: 15), "The original source was not retained.")
        app.segmentedControls.buttons["Zutaten"].tap()
        reveal(ingredient)
        XCTAssertEqual(ingredient.value as? String, "Pasta nach Wahl", "The applied ingredient did not survive reload.")
        capture("16-import-korrektur-neu-geladen")
        element("importReviewClose").tap()

        reopenRecipe()
        let cookButton = element("recipeCookButton")
        reveal(cookButton)
        cookButton.tap()
        let cookNow = element("recipeCookNow")
        XCTAssertTrue(cookNow.waitForExistence(timeout: 10))
        cookNow.tap()
        let start = element("cookingStart")
        reveal(start)
        start.tap()
        let firstStep = app.staticTexts["Schritt 1 von 3"]
        XCTAssertTrue(firstStep.waitForExistence(timeout: 15))
        capture("17-kochen-erster-schritt")
        let nextStep = element("cookingStepNext")
        reveal(nextStep)
        XCTAssertTrue(nextStep.isEnabled)
        nextStep.tap()
        let secondStep = app.staticTexts["Schritt 2 von 3"]
        XCTAssertTrue(secondStep.waitForExistence(timeout: 15))
        reveal(secondStep, direction: .down)
        XCTAssertTrue(app.staticTexts["Speicherstatus: 1 erledigt · auf diesem iPhone gespeichert"].exists)
        capture("18-kochen-fortschritt-gesichert")

        // Recreate the app process, then explicitly use the cached library path.
        reopenRecipe(offline: true)
        reveal(cookButton)
        cookButton.tap()
        let resume = element("cookingResume")
        reveal(resume)
        capture("19-kochen-fortsetzen-nach-neustart")
        resume.tap()
        XCTAssertTrue(secondStep.waitForExistence(timeout: 15), "The active cooking step did not survive restart.")
        XCTAssertTrue(app.staticTexts["Speicherstatus: 1 erledigt · auf diesem iPhone gespeichert"].exists)
        capture("20-kochen-wiederaufgenommen")
    }

    private func reopenRecipe(offline: Bool = false) {
        app.terminate()
        app.launch()
        let archive = app.tabBars.buttons["Archiv"]
        XCTAssertTrue(archive.waitForExistence(timeout: 35), "The normal authenticated session did not restore.")
        archive.tap()
        XCTAssertTrue(app.navigationBars["Archiv"].waitForExistence(timeout: 15))
        if offline {
            let library = element("offlineLibraryButton")
            XCTAssertTrue(library.waitForExistence(timeout: 10))
            library.tap()
            XCTAssertTrue(app.navigationBars["Offline-Regal"].waitForExistence(timeout: 15))
        }
        let recipe = app.staticTexts["Zitronen-Ricotta-Pasta"].firstMatch
        reveal(recipe, maximumSwipes: 8)
        recipe.tap()
        XCTAssertTrue(app.staticTexts["Rezeptpass"].waitForExistence(timeout: 20))
    }

    private func openImportReview() {
        let review = element("recipeImportReview")
        reveal(review)
        review.tap()
        XCTAssertTrue(element("importReviewSource").waitForExistence(timeout: 20))
    }

    private func element(_ identifier: String) -> XCUIElement {
        app.descendants(matching: .any).matching(identifier: identifier).firstMatch
    }

    private enum ScrollDirection { case up, down }

    private func reveal(_ element: XCUIElement, maximumSwipes: Int = 10,
                        direction: ScrollDirection = .up, file: StaticString = #filePath, line: UInt = #line) {
        _ = element.waitForExistence(timeout: 10)
        for _ in 0..<maximumSwipes {
            if element.exists && element.isHittable { return }
            switch direction {
            case .up: app.swipeUp()
            case .down: app.swipeDown()
            }
        }
        guard element.exists && element.isHittable else {
            capture("failure-missing-review-control")
            let hierarchy = XCTAttachment(string: app.debugDescription)
            hierarchy.name = "failure-accessibility-hierarchy"
            hierarchy.lifetime = .keepAlways
            add(hierarchy)
            // Reading identifier on a missing query throws a snapshot error and
            // obscures the original assertion. Report the actual call site instead.
            XCTFail("Expected review section is not visible after scrolling.", file: file, line: line)
            return
        }
    }

    private func fill(_ field: XCUIElement, with text: String) {
        reveal(field)
        field.tap()
        field.typeText(text)
    }

    private func replace(_ field: XCUIElement, with text: String) {
        reveal(field)
        let previous = field.value as? String ?? ""
        field.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.5)).tap()
        field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: previous.count) + text)
        XCTAssertEqual(field.value as? String, text)
    }

    private func dismissKeyboard(identifier: String = "cookMemoryHideKeyboard") {
        // Prefer the app's explicit keyboard action before native form scrolling.
        if app.keyboards.count > 0 {
            let done = element(identifier)
            if done.exists && done.isHittable {
                done.tap()
            } else {
                app.swipeDown()
            }
        }
        let keyboardGone = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "exists == false"),
            object: app.keyboards.firstMatch
        )
        XCTAssertEqual(XCTWaiter.wait(for: [keyboardGone], timeout: 5), .completed,
                       "The keyboard still covers the review screenshot.")
    }

    private func capture(_ name: String) {
        Thread.sleep(forTimeInterval: 2)
        let screenshot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        screenshot.name = name
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }
}
