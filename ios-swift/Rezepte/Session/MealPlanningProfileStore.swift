import Combine
import CryptoKit
import Foundation

struct MealPlanningProfile: Codable, Identifiable, Equatable {
    let id: UUID
    var name: String
    let preferences: MealPlanningPreferences
}

/// Local profiles belong to an account and a verified household, never the device's next user.
@MainActor
final class MealPlanningProfileStore: ObservableObject {
    @Published private(set) var items: [MealPlanningProfile] = []
    @Published private(set) var isReady = false
    @Published private(set) var errorMessage: String?
    private let defaults: UserDefaults
    private var accountKey: String?
    private var documentKey: String?
    private var generation = UUID()
    private var isCurrent: () -> Bool = { false }
    private struct Document: Codable { let version: Int; let items: [MealPlanningProfile] }

    init(defaults: UserDefaults = .standard) { self.defaults = defaults }

    static func accountKey(server: String, userID: Int) -> String {
        let server = APIClient.normalizedServerURL(server)?.absoluteString ?? server
        let digest = SHA256.hash(data: Data("\(server)\u{0}\(userID)".utf8))
            .map { String(format: "%02x", $0) }.joined()
        return "meal-profiles-v1-\(digest)"
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
        guard !defaults.bool(forKey: accountKey + "-erased"), householdID >= 0, isCurrent() else { throw APIError.sessionChanged }
        let key = accountKey + "-household-\(householdID)"
        let loaded: [MealPlanningProfile]
        if let data = defaults.data(forKey: key) {
            let document = try JSONDecoder().decode(Document.self, from: data)
            guard document.version == 1, document.items.count <= 50,
                  Set(document.items.map(\.id)).count == document.items.count else { throw APIError.invalidResponse("Wochenprofile") }
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
        items = []; accountKey = nil; documentKey = nil
        isReady = false; errorMessage = nil; isCurrent = { false }
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
        for key in defaults.dictionaryRepresentation().keys where key.hasPrefix(accountKey + "-household") { defaults.removeObject(forKey: key) }
        if self.accountKey == accountKey { deactivate() }
    }

    func save(name: String, preferences: MealPlanningPreferences) throws {
        let name = try validatedName(name)
        if let message = preferences.validationMessage { throw APIError.server(0, message) }
        guard items.count < 50 else { throw APIError.server(0, "Du kannst bis zu 50 Wochenprofile speichern.") }
        var next = items
        next.insert(MealPlanningProfile(id: UUID(), name: name, preferences: preferences), at: 0)
        try persist(next)
    }

    func preferences(id: UUID) throws -> MealPlanningPreferences {
        try requireContext()
        guard let profile = items.first(where: { $0.id == id }) else { throw APIError.sessionChanged }
        if let message = profile.preferences.validationMessage { throw APIError.server(0, message) }
        return profile.preferences
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

    private func requireContext() throws {
        guard isReady, isCurrent(), let accountKey, documentKey != nil,
              !defaults.bool(forKey: accountKey + "-erased") else { throw APIError.sessionChanged }
    }

    private func persist(_ next: [MealPlanningProfile]) throws {
        try requireContext()
        guard let documentKey else { throw APIError.sessionChanged }
        let data = try JSONEncoder().encode(Document(version: 1, items: next))
        defaults.set(data, forKey: documentKey)
        items = next
    }
}
