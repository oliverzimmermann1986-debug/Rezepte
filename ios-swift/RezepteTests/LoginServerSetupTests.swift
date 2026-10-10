import XCTest
@testable import Rezepte

final class LoginServerSetupTests: XCTestCase {
    private let token = "single-use-invitation-token_123"

    func testFirstLaunchOffersConfiguredService() {
        XCTAssertEqual(LoginServerSetup.initialServer(saved: ""), "https://rezepte.mausbaeren.me")
        XCTAssertEqual(LoginServerSetup.initialServer(saved: " \n "), LoginServerSetup.defaultServer)
    }

    func testReturningUsersKeepTheirServerIncludingAnAddressNeedingCorrection() {
        XCTAssertEqual(LoginServerSetup.initialServer(saved: " https://kitchen.example/household "), "https://kitchen.example/household")
        XCTAssertEqual(LoginServerSetup.initialServer(saved: "http://old.example"), "http://old.example")
        XCTAssertEqual(LoginServerSetup.initialServer(saved: "old-invalid-address"), "old-invalid-address")
    }

    func testReviewServerOnlyOverridesWhenAutomationExplicitlyEnabled() {
        let review = "https://rezepte-review.mausbaeren.me"
        XCTAssertEqual(LoginServerSetup.initialServer(saved: "https://saved.example", reviewEnvironment: ["APP_REVIEW_SERVER": review]), "https://saved.example")
        XCTAssertEqual(LoginServerSetup.initialServer(saved: "https://saved.example", reviewEnvironment: ["APP_REVIEW_AUTOMATION": "1", "APP_REVIEW_SERVER": review]), review)
        XCTAssertEqual(LoginServerSetup.initialServer(saved: "https://saved.example", reviewEnvironment: ["APP_REVIEW_AUTOMATION": "1"]), "https://saved.example")
    }

    func testServerSelectionSupportsHTTPSBasePathsAndPorts() {
        XCTAssertEqual(LoginServerSetup.serverURL(" HTTPS://KITCHEN.EXAMPLE:8443/my-recipes/ ")?.absoluteString, "https://kitchen.example:8443/my-recipes")
        XCTAssertEqual(LoginServerSetup.serverURL("https://kitchen.example/")?.absoluteString, "https://kitchen.example")
        XCTAssertNotNil(LoginServerSetup.serverURL("https://localhost:8443"))
    }

    func testServerSelectionRefusesCredentialsQueriesFragmentsAndInsecureURLs() {
        for value in ["http://kitchen.example", "https://user@kitchen.example", "https://user:secret@kitchen.example",
                      "https://kitchen.example?token=secret", "https://kitchen.example#secret", "kitchen.example",
                      "https://kitchen.example:0", "https://kitchen.example:65536"] {
            XCTAssertNil(LoginServerSetup.serverURL(value), value)
        }
    }

    func testServerSelectionRefusesTraversalAndEncodedPathSeparators() {
        for path in ["/../other", "/a/./b", "/%2e%2e/other", "/a%2fb", "/a%5cb", "/a//b"] {
            XCTAssertNil(LoginServerSetup.serverURL("https://kitchen.example" + path), path)
        }
    }

    func testInvitationSupportsActualRegistrationRouteAndSelfHostedBasePath() throws {
        let invitation = try link(" https://KITCHEN.EXAMPLE:8443/my-recipes/register?invite=\(token) \n")
        XCTAssertEqual(invitation.server.absoluteString, "https://kitchen.example:8443/my-recipes")
        XCTAssertEqual(invitation.token, token)
        XCTAssertEqual(invitation.host, "kitchen.example")
    }

    func testRawCodeAndEmptyInvitationNeedNoHostSelection() {
        XCTAssertEqual(LoginServerSetup.invitation(" \n "), .empty)
        XCTAssertEqual(LoginServerSetup.invitation(" \(token) "), .code(token))
        XCTAssertEqual(LoginServerSetup.registrationToken(input: "", server: LoginServerSetup.defaultServer, selectedInvitation: nil), "")
        XCTAssertEqual(LoginServerSetup.registrationToken(input: token, server: LoginServerSetup.defaultServer, selectedInvitation: nil), token)
    }

    func testInvitationMustUseHTTPSWithoutCredentialsOrFragment() {
        for prefix in ["http://kitchen.example", "https://user@kitchen.example", "https://user:secret@kitchen.example"] {
            XCTAssertEqual(LoginServerSetup.invitation("\(prefix)/register?invite=\(token)"), .invalid)
        }
        XCTAssertEqual(LoginServerSetup.invitation("https://kitchen.example/register?invite=\(token)#extra"), .invalid)
    }

    func testInvitationRejectsOtherRoutesAndAmbiguousQueryItems() {
        for path in ["/login", "/register/", "/not-register", "/%72egister"] {
            XCTAssertEqual(LoginServerSetup.invitation("https://kitchen.example\(path)?invite=\(token)"), .invalid)
        }
        for query in ["invite=\(token)&invite=other-valid-token", "invite=\(token)&redirect=elsewhere", "token=\(token)", "invite="] {
            XCTAssertEqual(LoginServerSetup.invitation("https://kitchen.example/register?\(query)"), .invalid)
        }
    }

    func testInvitationRejectsMalformedTokensAndTraversal() {
        for value in ["short", String(repeating: "a", count: 513), "sixteen-characters+", "sixteen-characters/", "sixteen characters"] {
            XCTAssertEqual(LoginServerSetup.invitation(value), .invalid)
        }
        for path in ["/../register", "/%2e%2e/register", "/a%2fb/register", "/a//register"] {
            XCTAssertEqual(LoginServerSetup.invitation("https://kitchen.example\(path)?invite=\(token)"), .invalid)
        }
    }

    func testPastedLinkNeverBecomesATokenWithoutExplicitSelectionEvenOnCurrentHost() throws {
        let input = "https://kitchen.example/register?invite=\(token)"
        let invitation = try link(input)
        XCTAssertNil(LoginServerSetup.registrationToken(input: input, server: invitation.server.absoluteString, selectedInvitation: nil))
        XCTAssertEqual(LoginServerSetup.registrationToken(input: input, server: invitation.server.absoluteString, selectedInvitation: invitation), token)
    }

    func testSelectionIsBoundToExactInvitationAndServerBasePath() throws {
        let input = "https://kitchen.example/household/register?invite=\(token)"
        let invitation = try link(input)
        XCTAssertNil(LoginServerSetup.registrationToken(input: input, server: "https://other.example/household", selectedInvitation: invitation))
        XCTAssertNil(LoginServerSetup.registrationToken(input: input, server: "https://kitchen.example/other", selectedInvitation: invitation))
        XCTAssertNil(LoginServerSetup.registrationToken(input: "https://kitchen.example/household/register?invite=another-invitation-token", server: invitation.server.absoluteString, selectedInvitation: invitation))
    }

    func testEitherPasswordFieldPreventsServerSwitch() {
        XCTAssertTrue(LoginServerSetup.canChangeServer(password: "", confirmation: ""))
        XCTAssertFalse(LoginServerSetup.canChangeServer(password: "secret", confirmation: ""))
        XCTAssertFalse(LoginServerSetup.canChangeServer(password: "", confirmation: "secret"))
        XCTAssertFalse(LoginServerSetup.canChangeServer(password: "secret", confirmation: "secret"))
    }

    private func link(_ input: String) throws -> LoginServerSetup.Invitation {
        guard case let .link(invitation) = LoginServerSetup.invitation(input) else {
            XCTFail("Expected a valid invitation")
            throw APIError.invalidServer
        }
        return invitation
    }
}
