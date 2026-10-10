import Foundation

/// Login-only parsing: inspecting an invitation must never select its server or send credentials.
enum LoginServerSetup {
    static let defaultServer = "https://rezepte.mausbaeren.me"

    struct Invitation: Equatable {
        let server: URL
        let token: String

        var host: String { server.host ?? "" }
    }

    enum InvitationInput: Equatable {
        case empty
        case code(String)
        case link(Invitation)
        case invalid
    }

    static func initialServer(saved: String, reviewEnvironment: [String: String] = [:]) -> String {
        if reviewEnvironment["APP_REVIEW_AUTOMATION"] == "1",
           let reviewServer = reviewEnvironment["APP_REVIEW_SERVER"], !reviewServer.isEmpty {
            return reviewServer
        }
        let saved = saved.trimmingCharacters(in: .whitespacesAndNewlines)
        // Never silently move a returning user's login to another server, even if
        // an old saved address now needs correcting.
        return saved.isEmpty ? defaultServer : saved
    }

    static func serverURL(_ input: String) -> URL? {
        guard var components = URLComponents(string: input.trimmingCharacters(in: .whitespacesAndNewlines)),
              components.scheme?.lowercased() == "https",
              let host = components.host, !host.isEmpty,
              components.user == nil, components.password == nil,
              components.query == nil, components.fragment == nil,
              components.port.map({ (1...65535).contains($0) }) ?? true,
              !host.contains(where: { $0.isWhitespace || $0.isNewline }),
              validPath(components.percentEncodedPath) else { return nil }
        components.scheme = "https"
        components.host = host.lowercased()
        while components.path.hasSuffix("/") { components.path.removeLast() }
        return components.url
    }

    static func invitation(_ input: String) -> InvitationInput {
        let value = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return .empty }
        if validToken(value) { return .code(value) }
        guard var components = URLComponents(string: value),
              components.fragment == nil,
              validPath(components.percentEncodedPath),
              components.percentEncodedPath.hasSuffix("/register"),
              let query = components.queryItems, query.count == 1,
              query[0].name == "invite", let token = query[0].value,
              validToken(token) else { return .invalid }
        components.percentEncodedPath = String(components.percentEncodedPath.dropLast("/register".count))
        components.query = nil
        guard let rawServer = components.string, let server = serverURL(rawServer) else { return .invalid }
        return .link(Invitation(server: server, token: token))
    }

    static func registrationToken(input: String, server: String, selectedInvitation: Invitation?) -> String? {
        switch invitation(input) {
        case .empty: return ""
        case let .code(token): return token
        case let .link(invitation):
            guard selectedInvitation == invitation, serverURL(server) == invitation.server else { return nil }
            return invitation.token
        case .invalid: return nil
        }
    }

    static func canChangeServer(password: String, confirmation: String) -> Bool {
        password.isEmpty && confirmation.isEmpty
    }

    private static func validToken(_ token: String) -> Bool {
        (16...512).contains(token.utf8.count)
            && token.utf8.allSatisfy { byte in
                (65...90).contains(byte) || (97...122).contains(byte)
                    || (48...57).contains(byte) || byte == 45 || byte == 95
            }
    }

    private static func validPath(_ path: String) -> Bool {
        guard path.isEmpty || path.hasPrefix("/") else { return false }
        // Accept base paths used by self-hosted installations; refuse encoded
        // separators, traversal and ambiguous URL forms before provider discovery.
        return path.split(separator: "/", omittingEmptySubsequences: false).allSatisfy { part in
            part != "." && part != ".." && part.utf8.allSatisfy { byte in
                (65...90).contains(byte) || (97...122).contains(byte)
                    || (48...57).contains(byte) || [45, 46, 95, 126].contains(byte)
            }
        } && !path.contains("//")
    }
}
