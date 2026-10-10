import Foundation

enum AppLegalLinks {
    // These describe the published app and its service, regardless of the
    // server selected for an account or received in a household invitation.
    static let imprint = URL(string: "https://rezepte.mausbaeren.me/impressum")!
    static let privacy = URL(string: "https://rezepte.mausbaeren.me/privacy")!
    static let support = URL(string: "https://rezepte.mausbaeren.me/support")!

    static func additionalServerPrivacyURL(for server: String) -> URL? {
        guard var components = URLComponents(string: server.trimmingCharacters(in: .whitespacesAndNewlines)),
              components.scheme?.lowercased() == "https" else { return nil }

        // Previously saved addresses may contain query parameters or userinfo.
        // A public browser link must never carry those or an app session token.
        components.user = nil
        components.password = nil
        components.query = nil
        components.fragment = nil
        if components.port == 443 { components.port = nil }
        guard let address = components.string,
              let base = LoginServerSetup.serverURL(address) else { return nil }
        let url = base.appendingPathComponent("privacy")
        return url == privacy ? nil : url
    }
}
