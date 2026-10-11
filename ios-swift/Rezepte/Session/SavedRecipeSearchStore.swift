import Combine
import CryptoKit
import Foundation

struct SavedRecipeSearch: Codable, Identifiable, Equatable {
    let id: UUID
    var name: String
    let query: String
    let filters: RecipeFilters
}

/// Local bookmarks are scoped to both the account and its verified household.
@MainActor
final class SavedRecipeSearchStore: ObservableObject {
    @Published private(set) var items: [SavedRecipeSearch] = []
    @Published private(set) var isReady = false
    @Published private(set) var errorMessage: String?
    private let defaults: UserDefaults
    private var accountKey: String?
    private var documentKey: String?
    private var generation = UUID()
    private var isCurrent: () -> Bool = { false }
    private struct Document: Codable { let version: Int; let items: [SavedRecipeSearch] }

    init(defaults: UserDefaults = .standard) { self.defaults = defaults }

    static func accountKey(server: String, userID: Int) -> String {
        let server = APIClient.normalizedServerURL(server)?.absoluteString ?? server
        let digest = SHA256.hash(data: Data("\(server)\u{0}\(userID)".utf8))
            .map { String(format: "%02x", $0) }.joined()
        return "recipe-searches-v1-\(digest)"
    }

    func activate(session: SessionStore) async {
        deactivate()
        guard case .signedIn = session.state, let userID = session.userID, !session.readOnly else { return }
        let identity = session.identity
        let request = generation
        let key = Self.accountKey(server: session.savedServer, userID: userID)
        guard !defaults.bool(forKey: key + "-erased") else { return }
        accountKey = key
        let current = { [weak session] in session?.identity == identity && session?.readOnly == false }
        do {
            let account = try await session.api.account()
            guard generation == request, current() else { return }
            guard !account.isGuest, let householdID = account.id, householdID >= 0 else { throw APIError.unauthenticated }
            try activate(accountKey: key, householdID: householdID, isCurrent: current)
        } catch {
            guard generation == request, current() else { return }
            if let network = error as? URLError,
               [.notConnectedToInternet, .networkConnectionLost, .timedOut, .cannotFindHost, .cannotConnectToHost, .dnsLookupFailed].contains(network.code),
               let householdID = defaults.object(forKey: key + "-household") as? Int {
                do { try activate(accountKey: key, householdID: householdID, isCurrent: current) }
                catch { errorMessage = error.localizedDescription }
            } else {
                defaults.removeObject(forKey: key + "-household")
                errorMessage = error.localizedDescription
                session.handle(error)
            }
        }
    }

    func activate(accountKey: String, householdID: Int, isCurrent: @escaping () -> Bool = { true }) throws {
        guard !defaults.bool(forKey: accountKey + "-erased"), householdID >= 0, isCurrent() else {
            throw APIError.sessionChanged
        }
        let key = accountKey + "-household-\(householdID)"
        let loaded: [SavedRecipeSearch]
        if let data = defaults.data(forKey: key) {
            let document = try JSONDecoder().decode(Document.self, from: data)
            guard document.version == 1, document.items.count <= 50 else { throw APIError.invalidResponse("Gespeicherte Suchen") }
            loaded = document.items
        } else { loaded = [] }
        self.accountKey = accountKey
        documentKey = key
        self.isCurrent = isCurrent
        defaults.set(householdID, forKey: accountKey + "-household")
        items = loaded
        errorMessage = nil
        isReady = true
    }

    func deactivate() {
        generation = UUID()
        items = []
        accountKey = nil
        documentKey = nil
        isReady = false
        errorMessage = nil
        isCurrent = { false }
    }

    func invalidateHousehold() {
        if let accountKey { defaults.removeObject(forKey: accountKey + "-household") }
        deactivate()
    }

    func eraseAccountData(session: SessionStore) {
        guard let userID = session.userID else { return }
        eraseAccountData(accountKey: Self.accountKey(server: session.savedServer, userID: userID))
    }

    func eraseAccountData(accountKey: String) {
        defaults.set(true, forKey: accountKey + "-erased")
        for key in defaults.dictionaryRepresentation().keys where key.hasPrefix(accountKey + "-household") {
            defaults.removeObject(forKey: key)
        }
        if self.accountKey == accountKey { deactivate() }
    }

    func save(name: String, query: String, filters: RecipeFilters) throws {
        let name = try validatedName(name)
        guard items.count < 50 else { throw APIError.server(0, "Du kannst bis zu 50 Suchen speichern.") }
        guard query.count <= 500 else { throw APIError.server(0, "Die Suche darf höchstens 500 Zeichen enthalten.") }
        var next = items
        next.insert(SavedRecipeSearch(id: UUID(), name: name, query: query, filters: filters), at: 0)
        try persist(next)
    }

    func rename(id: UUID, name: String) throws {
        let name = try validatedName(name)
        var next = items
        guard let index = next.firstIndex(where: { $0.id == id }) else { throw APIError.sessionChanged }
        next[index].name = name
        try persist(next)
    }

    func remove(id: UUID) throws { try persist(items.filter { $0.id != id }) }

    private func validatedName(_ value: String) throws -> String {
        let value = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty, value.count <= 80 else { throw APIError.server(0, "Bitte einen Namen mit 1 bis 80 Zeichen eingeben.") }
        return value
    }

    private func persist(_ next: [SavedRecipeSearch]) throws {
        guard isReady, isCurrent(), let accountKey, let documentKey,
              !defaults.bool(forKey: accountKey + "-erased") else { throw APIError.sessionChanged }
        let data = try JSONEncoder().encode(Document(version: 1, items: next))
        defaults.set(data, forKey: documentKey)
        items = next
    }
}
