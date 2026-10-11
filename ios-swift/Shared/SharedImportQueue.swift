import Foundation

enum SharedImportQueue {
    static let appGroup = "group.de.mausbaeren.rezepte"
    @discardableResult static func enqueue(_ url: String) -> Bool { SharedImportQueueStore.shared.enqueue(url) }
    static func all() -> [String] { SharedImportQueueStore.shared.all() }
    static func remove(_ url: String) { SharedImportQueueStore.shared.remove(url) }
}

/// App and extension coordinate the complete queue in their shared container.
/// URL, scope and attempted status are persisted together; no credentials are stored.
final class SharedImportQueueStore {
    static let shared: SharedImportQueueStore = {
        guard let container = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: SharedImportQueue.appGroup),
              let defaults = UserDefaults(suiteName: SharedImportQueue.appGroup) else {
            return SharedImportQueueStore()
        }
        return SharedImportQueueStore(defaults: defaults, storageURL: container.appendingPathComponent("shared-import-queue-v2.json"))
    }()

    private struct Entry: Codable, Equatable {
        let url: String
        var scope: String?
        var attempted = false
    }
    private struct Document: Codable, Equatable {
        var version = 2
        var activeScope: String?
        var entries: [Entry] = []
    }
    private enum Storage {
        case defaults(UserDefaults)
        case file(URL, UserDefaults)
        case unavailable
    }
    private let storage: Storage
    private static let documentKey = "shared-import-queue-document-v2"
    private static let defaultsLock = NSLock()

    /// Defaults-only storage keeps isolated unit-test fixtures independent of an App Group.
    init(defaults: UserDefaults, storageURL: URL? = nil) {
        if let storageURL { storage = .file(storageURL, defaults) }
        else { storage = .defaults(defaults) }
    }
    private init() { storage = .unavailable }

    @discardableResult
    func enqueue(_ url: String) -> Bool {
        transact { document in
            guard !document.entries.contains(where: { $0.url == url }) else { return true }
            document.entries.append(Entry(url: url, scope: document.activeScope))
            return true
        } ?? false
    }

    func all() -> [String] { transact { $0.entries.map(\.url) } ?? [] }

    func remove(_ url: String) {
        _ = transact { $0.entries.removeAll { $0.url == url }; return true }
    }

    func setActiveScope(_ scope: String?) {
        _ = transact { $0.activeScope = scope?.isEmpty == false ? scope : nil; return true }
    }

    func scope(for url: String) -> String? {
        transact { $0.entries.first { $0.url == url }?.scope } ?? nil
    }

    func claimUnassigned(scope: String) {
        guard !scope.isEmpty else { return }
        _ = transact { document in
            for index in document.entries.indices where document.entries[index].scope == nil {
                document.entries[index].scope = scope
            }
            return true
        }
    }

    func wasAttempted(_ url: String) -> Bool {
        transact { $0.entries.first { $0.url == url }?.attempted ?? false } ?? false
    }

    /// A request must not start unless this durable marker was written successfully.
    @discardableResult
    func markAttempted(_ url: String) -> Bool {
        transact { document in
            guard let index = document.entries.firstIndex(where: { $0.url == url }) else { return false }
            document.entries[index].attempted = true
            return true
        } ?? false
    }

    private func transact<T>(_ operation: @escaping (inout Document) -> T) -> T? {
        switch storage {
        case .unavailable: return nil
        case .defaults(let defaults):
            Self.defaultsLock.lock()
            defer { Self.defaultsLock.unlock() }
            do {
                let data = defaults.data(forKey: Self.documentKey)
                var document = try Self.document(data: data, legacy: defaults)
                let previous = document
                let result = operation(&document)
                if data == nil || document != previous {
                    defaults.set(try JSONEncoder().encode(document), forKey: Self.documentKey)
                }
                if data == nil { Self.clearLegacy(defaults) }
                return result
            } catch { return nil }
        case .file(let storageURL, let legacy):
            let coordinator = NSFileCoordinator(filePresenter: nil)
            var coordinationError: NSError?
            var result: T?
            coordinator.coordinate(writingItemAt: storageURL, options: [], error: &coordinationError) { url in
                do {
                    let exists = FileManager.default.fileExists(atPath: url.path)
                    let data = exists ? try Data(contentsOf: url) : nil
                    var document = try Self.document(data: data, legacy: legacy)
                    let previous = document
                    let value = operation(&document)
                    if !exists || document != previous {
                        try JSONEncoder().encode(document).write(to: url, options: .atomic)
                    }
                    if !exists { Self.clearLegacy(legacy) }
                    result = value
                } catch { result = nil }
            }
            return coordinationError == nil ? result : nil
        }
    }

    private static func document(data: Data?, legacy: UserDefaults) throws -> Document {
        if let data {
            let document = try JSONDecoder().decode(Document.self, from: data)
            guard document.version == 2,
                  Set(document.entries.map(\.url)).count == document.entries.count,
                  document.activeScope?.isEmpty != true,
                  document.entries.allSatisfy({ $0.scope?.isEmpty != true }) else {
                throw CocoaError(.fileReadCorruptFile)
            }
            return document
        }
        // Missing file is the only migration trigger. Corrupt or future documents
        // never fall back to stale defaults or inherit a previous active scope.
        var seen = Set<String>()
        let entries = (legacy.stringArray(forKey: "shared-import-urls") ?? [])
            .filter { seen.insert($0).inserted }.map { Entry(url: $0, scope: nil) }
        return Document(entries: entries)
    }

    private static func clearLegacy(_ defaults: UserDefaults) {
        for key in ["shared-import-urls", "shared-import-url-scopes-v1", "shared-import-attempted-urls-v1", "shared-import-active-scope-v1"] {
            defaults.removeObject(forKey: key)
        }
    }
}
