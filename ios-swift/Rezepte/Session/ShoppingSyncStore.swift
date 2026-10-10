import Combine
import CryptoKit
import Foundation

struct ShoppingJournalStorage {
    var read: (String) throws -> Data?
    var write: (String, Data) throws -> Void
    /// Receives only the account namespace digest, never a server, user ID or path.
    var eraseNamespace: (String) throws -> Void = { _ in throw CocoaError(.fileWriteUnknown) }

    static func files(directory: URL? = nil) -> Self {
        let root = directory ?? FileManager.default.urls(for: .applicationSupportDirectory,
            in: .userDomainMask)[0].appendingPathComponent("ShoppingJournal", isDirectory: true)
        return Self(read: { key in
            let url = root.appendingPathComponent(key)
            guard FileManager.default.fileExists(atPath: url.path) else { return nil }
            return try Data(contentsOf: url)
        }, write: { key, data in
            try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
            let url = root.appendingPathComponent(key)
            // Exclude account data from cloud device backups; the journal is
            // deliberately local and is never silently copied to another user.
            try data.write(to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
            var resourceURL = root
            var values = URLResourceValues()
            values.isExcludedFromBackup = true
            try? resourceURL.setResourceValues(values)
        }, eraseNamespace: { digest in
            guard digest.count == 64, digest.allSatisfy({ "0123456789abcdef".contains($0) }) else {
                throw CocoaError(.fileWriteInvalidFileName)
            }
            let files: [URL]
            do {
                guard try root.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink != true else {
                    throw CocoaError(.fileWriteUnknown)
                }
                files = try FileManager.default.contentsOfDirectory(at: root,
                    includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey])
            } catch let error as CocoaError where error.code == .fileReadNoSuchFile { return }
            var failure: Error?
            for file in files where file.lastPathComponent.hasPrefix(digest + "-") {
                do {
                    let values = try file.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
                    // Journals are files. Never recursively remove an unexpected directory.
                    guard values.isDirectory != true || values.isSymbolicLink == true else {
                        throw CocoaError(.fileWriteUnknown)
                    }
                    try FileManager.default.removeItem(at: file)
                } catch { failure = failure ?? error }
            }
            if let failure { throw failure }
        })
    }
}

struct ShoppingSyncContext {
    let identity: UUID
    let namespace: String
    let writable: Bool
    let isCurrent: @MainActor () -> Bool
    let fetch: @MainActor () async throws -> ShoppingCartResponse
    let synchronize: @MainActor (ShoppingSyncPayload) async throws -> ShoppingCartResponse
}

@MainActor
final class ShoppingSyncStore: ObservableObject {
    @Published private(set) var items: [CartItem] = []
    @Published private(set) var isReady = false
    @Published private(set) var isSyncing = false
    /// All unconfirmed operations, including conflicts. Keep write guards on
    /// this total; only waitingCount can make progress through another sync.
    @Published private(set) var pendingCount = 0
    @Published private(set) var waitingCount = 0
    @Published private(set) var conflictCount = 0
    @Published private(set) var conflictOperationIDs: Set<String> = []
    @Published private(set) var conflictDetails: [String] = []
    @Published private(set) var errorMessage: String?
    @Published private(set) var householdID: Int?
    @Published private(set) var undoLabel: String?
    @Published private(set) var undoID: String?

    private struct Pointer: Codable { let householdId: Int; let notice: String? }
    private let storage: ShoppingJournalStorage
    private let defaults: UserDefaults
    private var context: ShoppingSyncContext?
    private var document: ShoppingDocument?
    private var flight: Task<Void, Error>?
    private var flightID: UUID?
    private var notice: String?
    private var onlineEdit = false
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()
    private static let erasureFailure = "Dein Konto ist serverseitig gelöscht. Die lokalen Einkaufsdaten bleiben gesperrt, konnten aber nicht vollständig vom Gerät entfernt werden."

    init(storage: ShoppingJournalStorage = .files(), defaults: UserDefaults = .standard) {
        self.storage = storage
        self.defaults = defaults
    }

    func activate(session: SessionStore) async {
        let identity = session.identity
        guard session.userID != nil, !session.readOnly, session.role != .guest else {
            deactivate()
            return
        }
        let server = APIClient.normalizedServerURL(session.savedServer)?.absoluteString ?? session.savedServer
        let namespace = "\(server)\u{0}\(session.userID!)"
        let client = session.api
        await activate(context: ShoppingSyncContext(identity: identity, namespace: namespace, writable: true,
            isCurrent: { [weak session] in
                session?.identity == identity && session?.readOnly == false && session?.role != .guest
            }, fetch: { try await client.shoppingCart() },
            synchronize: { try await client.shoppingSynchronize($0) }))
    }

    /// Injectable context permits deterministic lost-response and persistence
    /// failure tests without exposing authentication tokens to this store.
    func activate(context next: ShoppingSyncContext) async {
        if context?.identity == next.identity, context?.namespace == next.namespace { return }
        deactivate()
        context = next
        if isErased(next.namespace) {
            // A failed erase must never restore or upload an old journal, even
            // after restarting the app or receiving a late network response.
            do {
                try storage.eraseNamespace(digest(next.namespace))
                errorMessage = "Dieses Konto wurde gelöscht. Gespeicherte Einkaufsdaten werden nicht mehr geöffnet."
            } catch { errorMessage = Self.erasureFailure }
            context = nil
            return
        }
        guard next.writable, next.isCurrent() else { return }
        do {
            if !defaults.bool(forKey: key("invalidated", context: next)),
               let data = try storage.read(key("active", context: next)) {
                let pointer = try decoder.decode(Pointer.self, from: data)
                if pointer.householdId >= 0 { document = try read(household: pointer.householdId, context: next) }
                notice = pointer.notice
            }
            try assertCurrent(next)
            publish()
            try await refresh()
        } catch {
            guard isCurrent(next) else { return }
            errorMessage = error.localizedDescription
        }
    }

    func deactivate() {
        flight?.cancel()
        flight = nil
        flightID = nil
        context = nil
        document = nil
        notice = nil
        onlineEdit = false
        items = []
        isReady = false
        isSyncing = false
        pendingCount = 0
        waitingCount = 0
        conflictCount = 0
        conflictOperationIDs = []
        conflictDetails = []
        errorMessage = nil
        householdID = nil
        undoLabel = nil
        undoID = nil
    }

    /// Call only after the server acknowledged deletion, before signing out.
    /// Ordinary sign-out intentionally keeps the account's offline journal.
    func eraseAccountData(session: SessionStore) throws {
        guard let userID = session.userID else {
            if let context, context.identity == session.identity {
                try eraseAccountData(namespace: context.namespace)
                return
            }
            deactivate()
            throw APIError.server(0, "Dein Konto ist serverseitig gelöscht. Die lokalen Einkaufsdaten konnten diesem Konto nicht sicher zugeordnet und deshalb nicht vollständig entfernt werden.")
        }
        let server = APIClient.normalizedServerURL(session.savedServer)?.absoluteString ?? session.savedServer
        try eraseAccountData(namespace: "\(server)\u{0}\(userID)")
    }

    /// Injectable account boundary also covers journals from former households.
    func eraseAccountData(namespace: String) throws {
        defaults.set(true, forKey: erasedKey(namespace))
        if context?.namespace == namespace { deactivate() }
        do {
            try storage.eraseNamespace(digest(namespace))
            defaults.removeObject(forKey: "\(digest(namespace))-invalidated.json")
        } catch {
            throw APIError.server(0, Self.erasureFailure)
        }
        // Keep the non-content tombstone: a stale task or restored session must
        // never recreate this deleted account's namespace, even after success.
    }

    func invalidateHousehold() throws {
        let context = try requireContext()
        let message = "Änderungen des vorherigen Haushalts bleiben separat gespeichert und werden nicht übertragen."
        defer { deactivate() }
        defaults.set(true, forKey: key("invalidated", context: context))
        try storage.write(key("active", context: context), encoder.encode(Pointer(householdId: -1, notice: message)))
    }

    private func digest(_ namespace: String) -> String {
        SHA256.hash(data: Data(namespace.utf8)).map { String(format: "%02x", $0) }.joined()
    }

    private func erasedKey(_ namespace: String) -> String { "\(digest(namespace))-erased.json" }

    private func isErased(_ namespace: String) -> Bool { defaults.bool(forKey: erasedKey(namespace)) }

    private func key(_ suffix: String, context: ShoppingSyncContext) -> String {
        "\(digest(context.namespace))-\(suffix).json"
    }

    private func isCurrent(_ expected: ShoppingSyncContext) -> Bool {
        context?.identity == expected.identity && context?.namespace == expected.namespace
            && !isErased(expected.namespace) && expected.isCurrent()
    }

    private func assertCurrent(_ expected: ShoppingSyncContext) throws {
        guard isCurrent(expected) else { throw APIError.sessionChanged }
    }

    private func requireContext() throws -> ShoppingSyncContext {
        guard let context, context.writable else {
            throw APIError.server(403, "Gastzugang ist schreibgeschützt.")
        }
        try assertCurrent(context)
        return context
    }

    private func read(household: Int, context: ShoppingSyncContext) throws -> ShoppingDocument? {
        guard let data = try storage.read(key("household-\(household)", context: context)) else { return nil }
        do {
            let value = try decoder.decode(ShoppingDocument.self, from: data)
            guard value.version == 1, value.householdId == household,
                  Set(value.operations.map { $0.wire.operationId }).count == value.operations.count,
                  value.operations.allSatisfy({ ["add", "check", "delete", "restore"].contains($0.wire.kind) }) else {
                throw APIError.invalidResponse("gespeicherte Einkaufsliste")
            }
            return value
        } catch {
            throw APIError.server(0, "Gespeicherte Einkaufsliste ist beschädigt. Die lokalen Änderungen wurden aufbewahrt.")
        }
    }

    private func save(_ next: ShoppingDocument, context: ShoppingSyncContext, setActive: Bool = false) throws {
        try assertCurrent(context)
        // Synchronous local I/O on MainActor serializes mutations. No UI state
        // or network operation is published before this atomic write succeeds.
        try storage.write(key("household-\(next.householdId)", context: context), encoder.encode(next))
        if setActive {
            try storage.write(key("active", context: context), encoder.encode(Pointer(householdId: next.householdId, notice: notice)))
            defaults.removeObject(forKey: key("invalidated", context: context))
        }
        try assertCurrent(context)
        document = next
        publish()
    }

    private func publish() {
        items = document?.projectedItems ?? []
        isReady = document != nil
        householdID = document?.householdId
        let operations = document?.operations ?? []
        pendingCount = operations.count
        conflictOperationIDs = Self.conflictedOperationIDs(in: operations)
        let conflicts = operations.filter { conflictOperationIDs.contains($0.wire.operationId) }
        conflictCount = conflicts.count
        waitingCount = pendingCount - conflictCount
        conflictDetails = conflicts.map { operation in
            let reason: String
            switch operation.conflict {
            case "item_missing": reason = "bereits entfernt"
            case "item_exists": reason = "bereits wieder vorhanden"
            case "delete_missing": reason = "Löschung nicht mehr verfügbar"
            case "already_restored": reason = "bereits wiederhergestellt"
            case "dependency_failed": reason = "vorherige Änderung abgelehnt"
            case nil: reason = "hängt von einer abgelehnten Änderung ab"
            default: reason = "inzwischen geändert"
            }
            return "\(operation.label ?? operation.wire.name ?? "Artikel"): \(reason)"
        }
        undoLabel = document?.undo.last?.label
        undoID = document?.undo.last?.id
    }

    /// Include unsent descendants as soon as a parent fails, even when the
    /// chain crosses a 50-operation request boundary. These cannot be retried
    /// successfully and must remain stored until the user discards them.
    private static func conflictedOperationIDs(in operations: [ShoppingLocalOperation]) -> Set<String> {
        var rejected = Set(operations.filter { $0.conflict != nil }.map { $0.wire.operationId })
        var previousCount = -1
        while previousCount != rejected.count {
            previousCount = rejected.count
            for operation in operations where operation.wire.targetOperationId.map(rejected.contains) == true
                || operation.wire.afterOperationId.map(rejected.contains) == true {
                rejected.insert(operation.wire.operationId)
            }
        }
        return rejected
    }

    private func mutate(expectedHouseholdID: Int? = nil, expectedIdentity: UUID? = nil,
                        _ change: (inout ShoppingDocument) throws -> Void) throws {
        let context = try requireContext()
        if let expectedIdentity, context.identity != expectedIdentity { throw APIError.sessionChanged }
        guard !onlineEdit else { throw APIError.server(0, "Bitte die laufende Mengenänderung abwarten.") }
        guard var next = document else {
            throw APIError.server(0, "Bitte die Einkaufsliste einmal mit Verbindung öffnen, bevor du offline einkaufst.")
        }
        if let expectedHouseholdID, next.householdId != expectedHouseholdID { throw APIError.sessionChanged }
        try change(&next)
        guard next.operations.filter({ $0.wire.kind != "restore" }).count <= 500 else {
            throw APIError.server(0, "Bitte zuerst die gespeicherten Änderungen synchronisieren.")
        }
        next.undo = Array(next.undo.suffix(20))
        let restores = next.operations.filter { $0.wire.kind == "restore" }.count
        while next.undo.count > 1 && restores + next.undo.reduce(0, { $0 + $1.deletions.count }) > 500 {
            next.undo.removeFirst()
        }
        guard restores + next.undo.reduce(0, { $0 + $1.deletions.count }) <= 500 else {
            throw APIError.server(0, "Bitte zuerst synchronisieren, damit diese Löschung vollständig rückgängig bleibt.")
        }
        try save(next, context: context)
    }

    private func nextLocalID(_ document: ShoppingDocument) -> Int {
        min(-1, (document.operations.map(\.localId) + document.bindings.keys.compactMap(Int.init)).min() ?? -1) - 1
    }

    func addMany(_ entries: [ShoppingAddItem], expectedHouseholdID: Int? = nil) throws {
        guard (1...50).contains(entries.count) else { throw APIError.server(0, "Bitte 1 bis 50 Artikel hinzufügen.") }
        let validated = try entries.map(ShoppingInput.validate)
        try mutate(expectedHouseholdID: expectedHouseholdID) { next in
            var localID = nextLocalID(next)
            for entry in validated {
                next.operations.append(ShoppingLocalOperation(wire: ShoppingWireOperation(kind: "add", name: entry.name,
                    amount: entry.amount, unit: entry.unit, category: entry.category), localId: localID))
                localID -= 1
            }
        }
    }

    private func targetChange(_ document: ShoppingDocument, itemID: Int, kind: String) throws -> (ShoppingLocalOperation, CartItem) {
        let binding = document.bindings[String(itemID)]
        let resolved = binding?.itemId ?? itemID
        guard let item = document.projectedItems.first(where: { $0.id == resolved }) else {
            throw APIError.server(0, "Dieser Artikel wurde bereits entfernt.")
        }
        if itemID > 0, item.syncRevision == nil { throw APIError.server(0, "Bitte die Einkaufsliste zuerst aktualisieren.") }
        let create = document.operations.first { ["add", "restore"].contains($0.wire.kind) && $0.localId == itemID }
        let previous = document.operations.last { (document.bindings[String($0.localId)]?.itemId ?? $0.localId) == resolved }
        let rejected = Self.conflictedOperationIDs(in: document.operations)
        guard previous.map({ !rejected.contains($0.wire.operationId) }) ?? true else {
            throw APIError.server(0, "Bitte zuerst den Konflikt für diesen Artikel prüfen.")
        }
        var wire = ShoppingWireOperation(kind: kind)
        if itemID < 0 {
            wire.targetOperationId = create?.wire.operationId ?? binding?.operationId
            guard wire.targetOperationId != nil else { throw APIError.server(0, "Die lokale Artikelzuordnung fehlt.") }
        } else {
            wire.itemId = itemID
            wire.expectedRevision = item.syncRevision
        }
        wire.afterOperationId = previous?.wire.operationId
        wire.expectedChecked = item.checked
        if kind == "check" { wire.checked = !item.checked }
        return (ShoppingLocalOperation(wire: wire, localId: itemID, label: item.name), item)
    }

    func change(_ itemID: Int, kind: String, expectedHouseholdID: Int? = nil) throws {
        guard ["check", "delete"].contains(kind) else { throw APIError.server(0, "Ungültige Änderung.") }
        try mutate(expectedHouseholdID: expectedHouseholdID) { next in
            let (operation, item) = try targetChange(next, itemID: itemID, kind: kind)
            next.operations.append(operation)
            if kind == "delete" {
                next.undo.append(ShoppingUndoGroup(id: UUID().uuidString, label: item.name,
                    deletions: [ShoppingUndoDeletion(operationId: operation.wire.operationId, snapshot: item)]))
            }
        }
    }

    func clearChecked(_ captured: [CartItem], expectedHouseholdID: Int? = nil) throws {
        try remove(captured, checkedOnly: true, expectedHouseholdID: expectedHouseholdID)
    }

    func remove(_ captured: [CartItem], checkedOnly: Bool = false, expectedHouseholdID: Int? = nil) throws {
        guard !captured.isEmpty else { return }
        guard Set(captured.map(\.id)).count == captured.count else { throw APIError.server(0, "Doppelte Artikelauswahl.") }
        try mutate(expectedHouseholdID: expectedHouseholdID) { next in
            var deletions: [ShoppingUndoDeletion] = []
            for snapshot in captured {
                let (operation, item) = try targetChange(next, itemID: snapshot.id, kind: "delete")
                guard snapshot.checked == item.checked, snapshot.syncRevision == item.syncRevision,
                      !checkedOnly || item.checked else {
                    throw APIError.server(0, "Die Artikel wurden inzwischen geändert. Bitte die Auswahl erneut prüfen.")
                }
                next.operations.append(operation)
                deletions.append(ShoppingUndoDeletion(operationId: operation.wire.operationId, snapshot: item))
            }
            next.undo.append(ShoppingUndoGroup(id: UUID().uuidString,
                label: "\(deletions.count) \(checkedOnly ? "erledigte " : "")Artikel", deletions: deletions))
        }
    }

    func undoLatest(groupID: String? = nil, expectedHouseholdID: Int? = nil, expectedIdentity: UUID? = nil) throws {
        try mutate(expectedHouseholdID: expectedHouseholdID, expectedIdentity: expectedIdentity) { next in
            guard let latest = next.undo.last, groupID == nil || latest.id == groupID else { return }
            var localID = nextLocalID(next)
            for deletion in latest.deletions {
                next.operations.append(ShoppingLocalOperation(wire: ShoppingWireOperation(kind: "restore",
                    targetOperationId: deletion.operationId, afterOperationId: deletion.operationId), localId: localID,
                    snapshot: deletion.snapshot, label: deletion.snapshot.name))
                localID -= 1
            }
            next.undo.removeLast()
        }
    }

    func discardConflicts(operationIDs: Set<String>? = nil, expectedHouseholdID: Int? = nil,
                          expectedIdentity: UUID? = nil) throws {
        try mutate(expectedHouseholdID: expectedHouseholdID, expectedIdentity: expectedIdentity) { next in
            let rejected = Self.conflictedOperationIDs(in: next.operations)
            if let operationIDs, operationIDs != rejected {
                throw APIError.server(0, "Die Konflikte haben sich inzwischen geändert. Bitte erneut prüfen.")
            }
            next.operations.removeAll { rejected.contains($0.wire.operationId) }
            next.undo = next.undo.compactMap { group in
                var group = group
                group.deletions.removeAll { rejected.contains($0.operationId) }
                return group.deletions.isEmpty ? nil : group
            }
        }
    }

    func refresh() async throws {
        let context = try requireContext()
        if let flight { return try await flight.value }
        let id = UUID()
        let task = Task { try await self.performRefresh(context: context) }
        flightID = id
        flight = task
        isSyncing = true
        defer {
            if flightID == id {
                flightID = nil
                flight = nil
                isSyncing = false
            }
        }
        try await task.value
    }

    private func performRefresh(context: ShoppingSyncContext) async throws {
        do {
            for _ in 0..<10 {
                try assertCurrent(context)
                let household = document?.householdId
                let operations = document?.operations ?? []
                let rejected = Self.conflictedOperationIDs(in: operations)
                let sent = Array(operations.filter { !rejected.contains($0.wire.operationId) }.prefix(50))
                let result: ShoppingCartResponse
                if let household, !sent.isEmpty {
                    result = try await context.synchronize(ShoppingSyncPayload(householdId: household, operations: sent.map(\.wire)))
                } else {
                    result = try await context.fetch()
                }
                try assertCurrent(context)
                guard result.householdId >= 0 else { throw APIError.invalidResponse("Einkaufshaushalt") }
                if !sent.isEmpty, result.householdId != household { throw APIError.sessionChanged }
                var next = try documentFor(result, context: context)
                var receipts: [String: ShoppingReceipt] = [:]
                let sentIDs = Set(sent.map { $0.wire.operationId })
                for receipt in result.results ?? [] {
                    guard receipts[receipt.operationId] == nil, sentIDs.contains(receipt.operationId),
                          ["applied", "conflict"].contains(receipt.status) else {
                        throw APIError.invalidResponse("Einkaufsbestätigung")
                    }
                    receipts[receipt.operationId] = receipt
                }
                for operation in sent {
                    guard let receipt = receipts[operation.wire.operationId] else {
                        throw APIError.server(0, "Der Server hat nicht alle Änderungen bestätigt. Sie bleiben gespeichert.")
                    }
                    if ["add", "restore"].contains(operation.wire.kind), receipt.status == "applied" {
                        guard let itemID = receipt.itemId, itemID > 0 else { throw APIError.invalidResponse("Artikelzuordnung") }
                        next.bindings[String(operation.localId)] = ShoppingBinding(itemId: itemID, operationId: operation.wire.operationId)
                    }
                }
                next.items = result.items
                next.operations = next.operations.compactMap { operation in
                    guard let receipt = receipts[operation.wire.operationId] else { return operation }
                    guard receipt.status != "applied" else { return nil }
                    var operation = operation
                    operation.conflict = receipt.reason ?? "conflict"
                    return operation
                }
                let conflictedIDs = Self.conflictedOperationIDs(in: next.operations)
                next.undo = next.undo.compactMap { group in
                    var group = group
                    group.deletions.removeAll { conflictedIDs.contains($0.operationId) }
                    return group.deletions.isEmpty ? nil : group
                }
                try save(next, context: context, setActive: true)
                errorMessage = notice
                if !next.operations.contains(where: { !conflictedIDs.contains($0.wire.operationId) }) { break }
            }
        } catch {
            try assertCurrent(context)
            if case APIError.server(409, _) = error,
               let live = try? await context.fetch() {
                try assertCurrent(context)
                if live.householdId >= 0, live.householdId != document?.householdId {
                    var next = try documentFor(live, context: context)
                    next.items = live.items
                    try save(next, context: context, setActive: true)
                    errorMessage = notice
                    return
                }
            }
            errorMessage = error.localizedDescription
            throw error
        }
    }

    private func documentFor(_ response: ShoppingCartResponse, context: ShoppingSyncContext) throws -> ShoppingDocument {
        if let document, document.householdId == response.householdId { return document }
        if let count = document?.operations.count, count > 0 {
            notice = "\(count) Änderungen des vorherigen Haushalts bleiben separat gespeichert und werden nicht übertragen."
        }
        return try read(household: response.householdId, context: context) ?? ShoppingDocument(householdId: response.householdId)
    }

    /// Existing amount editing remains online-only until prior operations are
    /// confirmed. This avoids replacing quantities underneath queued revisions.
    func amountEditingUnavailableReason(for item: CartItem) -> String? {
        if conflictCount > 0 { return "Menge erst nach Klärung der Konflikte bearbeitbar." }
        if pendingCount > 0 || item.id <= 0 { return "Menge erst nach dem Abgleich bearbeitbar." }
        if isSyncing { return "Menge nach dem laufenden Abgleich bearbeitbar." }
        return nil
    }

    func updateAmount(item: CartItem, amount: Double, session: SessionStore) async throws {
        let context = try requireContext()
        if let reason = amountEditingUnavailableReason(for: item) {
            throw APIError.server(0, reason)
        }
        guard !onlineEdit else { throw APIError.server(0, "Bitte die laufende Mengenänderung abwarten.") }
        let household = householdID
        onlineEdit = true
        defer { if isCurrent(context) { onlineEdit = false } }
        _ = try await session.api.updateCartItemAmount(id: item.id, amount: amount)
        try assertCurrent(context)
        guard householdID == household else { throw APIError.sessionChanged }
        try await refresh()
    }
}
