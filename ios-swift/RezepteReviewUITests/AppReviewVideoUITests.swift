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
        app.launch()

        let serverField = app.textFields["review.server"]
        XCTAssertTrue(serverField.waitForExistence(timeout: 20), "The review login screen did not appear.")
        enter(server, into: serverField)
        enter(username, into: app.textFields["review.username"])
        enter(password, into: app.secureTextFields["review.password"])
        pause(2)

        app.buttons["Anmelden"].tap()
        let archiveTab = app.tabBars.buttons["Archiv"]
        XCTAssertTrue(archiveTab.waitForExistence(timeout: 35), "Login to the isolated review server failed.")
        pause(4)

        let recipe = app.staticTexts["Zitronen-Ricotta-Pasta"].firstMatch
        XCTAssertTrue(recipe.waitForExistence(timeout: 20), "The seeded review recipe is missing.")
        recipe.tap()
        XCTAssertTrue(app.staticTexts["Rezeptpass"].waitForExistence(timeout: 20))
        pause(4)

        reveal(app.otherElements["recipe.passport"])
        pause(4)
        reveal(app.otherElements["recipe.original-source"])
        pause(5)

        app.navigationBars.buttons["Archiv"].tap()
        XCTAssertTrue(archiveTab.waitForExistence(timeout: 10))

        let todayTab = app.tabBars.buttons["Heute"]
        todayTab.tap()
        XCTAssertTrue(app.navigationBars["Heute"].waitForExistence(timeout: 20))
        pause(5)

        let shoppingTab = app.tabBars.buttons["Einkauf"]
        shoppingTab.tap()
        XCTAssertTrue(app.navigationBars["Einkauf"].waitForExistence(timeout: 20))
        pause(3)
        let recurring = app.segmentedControls.buttons["Wiederkehrend"]
        XCTAssertTrue(recurring.waitForExistence(timeout: 10))
        recurring.tap()
        XCTAssertTrue(app.staticTexts["Hafermilch"].waitForExistence(timeout: 20))
        pause(5)

        let settingsTab = app.tabBars.buttons["Einstellungen"]
        settingsTab.tap()
        XCTAssertTrue(app.navigationBars["Einstellungen"].waitForExistence(timeout: 20))
        pause(4)

        let administration = app.buttons["Administration öffnen"]
        XCTAssertTrue(administration.waitForExistence(timeout: 15))
        administration.tap()
        XCTAssertTrue(app.navigationBars["Administration"].waitForExistence(timeout: 20))
        pause(4)

        let adminSettings = app.staticTexts["Admin-Einstellungen"].firstMatch
        XCTAssertTrue(adminSettings.waitForExistence(timeout: 20))
        adminSettings.tap()
        XCTAssertTrue(app.navigationBars["Admin-Einstellungen"].waitForExistence(timeout: 20))
        XCTAssertTrue(app.staticTexts["Sicherheitsgrenzen"].waitForExistence(timeout: 20))
        pause(5)
        app.swipeUp()
        pause(5)
    }

    private func enter(_ value: String, into element: XCUIElement) {
        XCTAssertTrue(element.waitForExistence(timeout: 10))
        element.tap()
        element.typeText(value)
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
}
