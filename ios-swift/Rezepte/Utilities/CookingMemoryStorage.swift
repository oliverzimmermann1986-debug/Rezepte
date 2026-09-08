import Foundation

struct PendingCookingMemory: Codable, Identifiable {
    let recipeID: Int
    let createdAt: Double
    let request: CookingMemoryRequest
    var error: String?
    var cancelled = false
    var id: String { request.clientEntryId }
}

struct CookingMemoryArchive: Codable {
    var generation = 0
    var entries: [String: [CookingMemoryEntry]] = [:]
    var pending: [PendingCookingMemory] = []
    var drafts: [String: CookingMemoryRequest] = [:]

    mutating func applySnapshot(_ items: [CookingMemoryEntry], recipeID: Int, expectedGeneration: Int) -> Bool {
        guard generation == expectedGeneration else { return false }
        let cancelling = Set(pending.filter(\.cancelled).map(\.id))
        entries[String(recipeID)] = items.filter { !cancelling.contains($0.clientEntryId) }
        return true
    }
}

/// Main-actor read/modify/write operations keep concurrent screens from losing a draft.
/// Data uses the same protected, account-scoped directory as offline recipes.
@MainActor
enum CookingMemoryStorage {
    static let key = "personal-cooking-memory-v1"
    private static var syncing: Set<String> = []
    private struct SyncAttempt: Hashable {
        let id: String
        let cancelled: Bool
    }

    static func load(session: SessionStore) throws -> CookingMemoryArchive {
        guard let account = session.offlineAccount else { throw APIError.unauthenticated }
        return try session.offlineStore.read(CookingMemoryArchive.self, key: key, account: account)
            ?? CookingMemoryArchive()
    }

    static func update(session: SessionStore, _ change: (inout CookingMemoryArchive) -> Void) throws {
        guard let account = session.offlineAccount else { throw APIError.unauthenticated }
        var archive = try load(session: session)
        change(&archive)
        archive.generation += 1
        try session.offlineStore.write(archive, key: key, account: account)
    }

    static func draftKey(recipeID: Int, stepNumber: Int?) -> String {
        "\(recipeID):\(stepNumber.map(String.init) ?? "recipe")"
    }

    static func enqueue(recipeID: Int, request: CookingMemoryRequest, session: SessionStore) throws {
        guard request.isValid, !session.readOnly else {
            throw APIError.server(422, "Bitte eine Notiz mit höchstens 2.000 Zeichen je Feld eingeben.")
        }
        var archive = try load(session: session)
        guard archive.pending.count < 200 else {
            throw APIError.server(422, "Bitte zuerst die ausstehenden Kochnotizen synchronisieren.")
        }
        if !archive.pending.contains(where: { $0.id == request.id }) {
            archive.pending.append(PendingCookingMemory(
                recipeID: recipeID, createdAt: Date().timeIntervalSince1970, request: request
            ))
        }
        archive.drafts.removeValue(forKey: draftKey(recipeID: recipeID, stepNumber: request.stepNumber))
        archive.generation += 1
        guard let account = session.offlineAccount else { throw APIError.unauthenticated }
        try session.offlineStore.write(archive, key: key, account: account)
        NotificationCenter.default.post(name: .cookingMemoryChanged, object: recipeID)
    }

    static func cancel(_ item: PendingCookingMemory, session: SessionStore) throws {
        try update(session: session) { archive in
            if let index = archive.pending.firstIndex(where: { $0.id == item.id }) {
                archive.pending[index].cancelled = true
                archive.pending[index].error = nil
            } else {
                var tombstone = item
                tombstone.cancelled = true
                archive.pending.append(tombstone)
            }
            archive.entries[String(item.recipeID)]?.removeAll { $0.clientEntryId == item.id }
        }
        NotificationCenter.default.post(name: .cookingMemoryChanged, object: item.recipeID)
    }

    static func sync(session: SessionStore) async {
        guard !session.readOnly, !session.isOffline, session.supports("cooking-memory-v1"),
              let account = session.offlineAccount, !syncing.contains(account.id) else { return }
        syncing.insert(account.id)
        defer { syncing.remove(account.id) }
        do {
            var attempted: Set<SyncAttempt> = []
            while session.offlineAccount == account, !session.readOnly, !session.isOffline {
                // A second save may enqueue while URLSession is awaiting this
                // loop's POST. Its own sync call returns busy, so drain the live
                // queue here. A failed POST and a later cancellation are distinct
                // attempts; permanent errors must not spin or block other notes.
                guard let item = try load(session: session).pending.first(where: {
                    !attempted.contains(SyncAttempt(id: $0.id, cancelled: $0.cancelled))
                }) else { break }
                attempted.insert(SyncAttempt(id: item.id, cancelled: item.cancelled))
                do {
                    if item.cancelled {
                        try await cancelOnServer(item, account: account, session: session)
                        continue
                    }
                    let response = try await session.api.saveCookingMemory(recipeID: item.recipeID, request: item.request, expectedAccount: account)
                    guard session.offlineAccount == account else { return }
                    if try load(session: session).pending.first(where: { $0.id == item.id })?.cancelled == true {
                        attempted.insert(SyncAttempt(id: item.id, cancelled: true))
                        try await cancelOnServer(item, account: account, session: session)
                        continue
                    }
                    try update(session: session) { archive in
                        archive.pending.removeAll { $0.id == item.id }
                        var entries = archive.entries[String(item.recipeID)] ?? []
                        entries.removeAll { $0.clientEntryId == item.id }
                        entries.insert(response.entry, at: 0)
                        archive.entries[String(item.recipeID)] = Array(entries.prefix(100))
                    }
                    NotificationCenter.default.post(name: .cookingMemoryChanged, object: item.recipeID)
                } catch {
                    guard session.offlineAccount == account else { return }
                    if let apiError = error as? APIError, case .unauthenticated = apiError {
                        session.handle(error)
                        return
                    }
                    try update(session: session) { archive in
                        if let index = archive.pending.firstIndex(where: { $0.id == item.id }) {
                            archive.pending[index].error = error.localizedDescription
                        }
                    }
                    NotificationCenter.default.post(name: .cookingMemoryChanged, object: item.recipeID)
                    if canContinueSync(after: error) { continue }
                    if APIError.permitsOfflineFallback(error) { session.handle(error) }
                    return
                }
            }
        } catch {
            session.alertMessage = "Kochnotizen konnten nicht lokal gesichert werden: \(error.localizedDescription)"
        }
    }

    private static func cancelOnServer(_ item: PendingCookingMemory, account: OfflineAccount, session: SessionStore) async throws {
        try await session.api.cancelPendingCookingMemory(recipeID: item.recipeID, clientEntryID: item.id, expectedAccount: account)
        guard session.offlineAccount == account else { return }
        try update(session: session) { archive in
            archive.pending.removeAll { $0.id == item.id }
            archive.entries[String(item.recipeID)]?.removeAll { $0.clientEntryId == item.id }
        }
        NotificationCenter.default.post(name: .cookingMemoryChanged, object: item.recipeID)
    }

    /// A stale step tip must not prevent unrelated valid notes from reaching the
    /// server. Preserve rejected entries for review; never auto-rewrite the tip.
    static func canContinueSync(after error: Error) -> Bool {
        guard let apiError = error as? APIError, case let .server(status, _) = apiError else { return false }
        return [400, 404, 409, 410, 422].contains(status)
    }
}

extension SessionStore {
    func syncCookingMemory() async {
        await CookingMemoryStorage.sync(session: self)
    }
}

extension Notification.Name {
    static let cookingMemoryChanged = Notification.Name("cookingMemoryChanged")
}
