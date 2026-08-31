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
        loginButton.tap()
        let archiveTab = app.tabBars.buttons["Archiv"]
        XCTAssertTrue(archiveTab.waitForExistence(timeout: 35), "Login to the isolated review server failed.")
        archiveTab.tap()
        XCTAssertTrue(app.navigationBars["Archiv"].waitForExistence(timeout: 20))
        pause(4)

        let recipe = app.staticTexts["Zitronen-Ricotta-Pasta"].firstMatch
        reveal(recipe, maximumSwipes: 4)
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
