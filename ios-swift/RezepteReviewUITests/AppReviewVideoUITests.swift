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
    }

    private func element(_ identifier: String) -> XCUIElement {
        app.descendants(matching: .any).matching(identifier: identifier).firstMatch
    }

    private func reveal(_ element: XCUIElement, maximumSwipes: Int = 10) {
        _ = element.waitForExistence(timeout: 10)
        for _ in 0..<maximumSwipes where !element.isHittable {
            app.swipeUp()
        }
        XCTAssertTrue(element.exists && element.isHittable, "Expected review section is not visible: \(element.identifier)")
    }

    private func fill(_ field: XCUIElement, with text: String) {
        reveal(field)
        field.tap()
        field.typeText(text)
    }

    private func dismissKeyboard() {
        // Prefer the app's explicit keyboard action before native form scrolling.
        if app.keyboards.count > 0 {
            let done = element("cookMemoryHideKeyboard")
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
                       "The keyboard still covers the cooking-memory screenshot.")
    }

    private func capture(_ name: String) {
        Thread.sleep(forTimeInterval: 2)
        let screenshot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        screenshot.name = name
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }
}
