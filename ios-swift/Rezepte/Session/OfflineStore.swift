import CryptoKit
import Foundation

/// No passwords or bearer tokens are written to the recipe cache.
struct OfflineAccount: Codable, Equatable, Hashable {
    let id: String
    let server: String
    let username: String

    init?(server: String, username: String) {
        guard let normalized = APIClient.normalizedServerURL(server),
              normalized.scheme == "https", !username.isEmpty else { return nil }
        self.server = normalized.absoluteString
        self.username = username
        id = Self.digest(self.server + "\n" + username)
    }

    private enum CodingKeys: String, CodingKey { case server, username }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        let server = try values.decode(String.self, forKey: .server)
        let username = try values.decode(String.self, forKey: .username)
        guard let account = Self(server: server, username: username) else {
            throw DecodingError.dataCorruptedError(forKey: .server, in: values,
                debugDescription: "Invalid offline account")
        }
        self = account
    }

    static func digest(_ value: String) -> String {
        SHA256.hash(data: Data(value.utf8)).map { String(format: "%02x", $0) }.joined()
    }
}

struct OfflineSession: Codable {
    let account: OfflineAccount
    let tokenFingerprint: String
    let fullAccess: Bool
    let readOnly: Bool
    let capabilities: Set<String>
    let serverVersion: String

    func matches(server: String, token: String) -> Bool {
        account.server == APIClient.normalizedServerURL(server)?.absoluteString
            && tokenFingerprint == OfflineAccount.digest(token)
    }
}

struct OfflineRecipe: Codable, Identifiable {
    let recipe: Recipe
    let savedAt: Date
    var id: Int { recipe.id }
}

struct LocalCookingProgress: Codable, Equatable {
    let recipeID: Int
    let runID: String
    let stepFingerprint: String
    var completedSteps: Set<Int>
    var activeStep: Int
    var servings: Int
    var started: Bool
    var needsSync: Bool
    var revision: String

    static func fingerprint(_ recipe: Recipe) -> String {
        OfflineAccount.digest(recipe.steps.map { "\($0.stableID):\($0.instruction)" }.joined(separator: "\n"))
    }

    init(recipe: Recipe, servings: Int) {
        recipeID = recipe.id
        runID = UUID().uuidString
        stepFingerprint = Self.fingerprint(recipe)
        completedSteps = []
        activeStep = 0
        self.servings = max(1, min(50, servings))
        started = false
        needsSync = false
        revision = UUID().uuidString
    }

    mutating func changed() {
        revision = UUID().uuidString
        needsSync = true
    }
}

struct PendingCookingCompletion: Codable, Identifiable {
    let id: String
    let recipeID: Int
    let servings: Int
}

/// A deadline, not a tick counter, survives view changes and process suspension.
struct PersistentCookingTimer: Codable, Equatable {
    var deadline: Date?
    var pausedSeconds: Int

    func remaining(at date: Date = Date()) -> Int {
        deadline.map { max(0, Int(ceil($0.timeIntervalSince(date)))) } ?? pausedSeconds
    }

    mutating func start(duration: Int, now: Date = Date()) {
        let value = remaining(at: now)
        deadline = now.addingTimeInterval(TimeInterval(value > 0 ? value : duration))
    }

    mutating func pause(now: Date = Date()) {
        pausedSeconds = remaining(at: now)
        deadline = nil
    }
}

/// Small synchronous, lock-serialized writes make each tap durable before the UI
/// confirms it. Account data lives in protected files outside UserDefaults.
final class OfflineStore: @unchecked Sendable {
    static let shared = OfflineStore()
    private let directory: URL
    private let lock = NSRecursiveLock()
    private var revokedAccounts: Set<String> = []

    enum StoreError: LocalizedError {
        case signedOut
        var errorDescription: String? { "Die lokale Sitzung wurde abgemeldet." }
    }

    init(directory: URL? = nil) {
        self.directory = directory ?? FileManager.default.urls(
            for: .applicationSupportDirectory, in: .userDomainMask
        )[0].appendingPathComponent("OfflineKitchen", isDirectory: true)
    }

    func read<Value: Decodable>(_ type: Value.Type, key: String, account: OfflineAccount) throws -> Value? {
        lock.lock()
        defer { lock.unlock() }
        let file = fileURL(key: key, account: account)
        guard FileManager.default.fileExists(atPath: file.path) else { return nil }
        return try JSONDecoder().decode(type, from: Data(contentsOf: file))
    }

    func write<Value: Encodable>(_ value: Value, key: String, account: OfflineAccount) throws {
        lock.lock()
        defer { lock.unlock() }
        guard !revokedAccounts.contains(account.id) else { throw StoreError.signedOut }
        var folder = directory.appendingPathComponent(account.id, isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try folder.setResourceValues(values)
        try JSONEncoder().encode(value).write(
            to: fileURL(key: key, account: account),
            options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication]
        )
    }

    func remove(key: String, account: OfflineAccount) throws {
        lock.lock()
        defer { lock.unlock() }
        let file = fileURL(key: key, account: account)
        if FileManager.default.fileExists(atPath: file.path) {
            try FileManager.default.removeItem(at: file)
        }
    }

    func clear(account: OfflineAccount) throws {
        lock.lock()
        defer { lock.unlock() }
        // In-flight network callbacks must not recreate files after logout.
        revokedAccounts.insert(account.id)
        let folder = directory.appendingPathComponent(account.id, isDirectory: true)
        if FileManager.default.fileExists(atPath: folder.path) {
            try FileManager.default.removeItem(at: folder)
        }
    }

    func activate(account: OfflineAccount) {
        lock.lock()
        defer { lock.unlock() }
        revokedAccounts.remove(account.id)
    }

    func recipes(account: OfflineAccount) throws -> [OfflineRecipe] {
        try read([OfflineRecipe].self, key: "recipes", account: account) ?? []
    }

    func saveRecipe(_ recipe: Recipe, account: OfflineAccount) throws {
        lock.lock()
        defer { lock.unlock() }
        var records = try recipes(account: account)
        records.removeAll { $0.id == recipe.id }
        records.insert(OfflineRecipe(recipe: recipe, savedAt: Date()), at: 0)
        try write(records, key: "recipes", account: account)
    }

    func removeRecipe(id: Int, account: OfflineAccount) throws {
        lock.lock()
        defer { lock.unlock() }
        try write(try recipes(account: account).filter { $0.id != id }, key: "recipes", account: account)
    }

    func progress(account: OfflineAccount) throws -> [LocalCookingProgress] {
        lock.lock()
        defer { lock.unlock() }
        let completedRunIDs = Set(try completions(account: account).map(\.id))
        return (try read([LocalCookingProgress].self, key: "cooking-progress", account: account) ?? [])
            .filter { !completedRunIDs.contains($0.runID) }
    }

    func saveProgress(_ progress: LocalCookingProgress, account: OfflineAccount) throws {
        lock.lock()
        defer { lock.unlock() }
        var records = try self.progress(account: account)
        records.removeAll { $0.recipeID == progress.recipeID }
        records.append(progress)
        try write(records, key: "cooking-progress", account: account)
    }

    func markProgressSynced(_ progress: LocalCookingProgress, account: OfflineAccount) throws {
        lock.lock()
        defer { lock.unlock() }
        guard var current = try self.progress(account: account).first(where: { $0.recipeID == progress.recipeID }),
              current.revision == progress.revision else { return }
        current.needsSync = false
        try saveProgress(current, account: account)
    }

    func completions(account: OfflineAccount) throws -> [PendingCookingCompletion] {
        try read([PendingCookingCompletion].self, key: "cooking-completions", account: account) ?? []
    }

    func finish(_ progress: LocalCookingProgress, account: OfflineAccount) throws {
        lock.lock()
        defer { lock.unlock() }
        var pending = try completions(account: account)
        if !pending.contains(where: { $0.id == progress.runID }) {
            pending.append(PendingCookingCompletion(id: progress.runID, recipeID: progress.recipeID, servings: progress.servings))
            // Write outbox first: a crash between writes cannot lose a completion.
            try write(pending, key: "cooking-completions", account: account)
        }
        try write(try self.progress(account: account).filter { $0.runID != progress.runID }, key: "cooking-progress", account: account)
    }

    func removeCompletion(id: String, account: OfflineAccount) throws {
        lock.lock()
        defer { lock.unlock() }
        // Also repair a crash between outbox persistence and progress removal.
        // Remove stale progress before removing the outbox's suppression marker.
        let rawProgress = try read([LocalCookingProgress].self, key: "cooking-progress", account: account) ?? []
        try write(rawProgress.filter { $0.runID != id }, key: "cooking-progress", account: account)
        try write(try completions(account: account).filter { $0.id != id }, key: "cooking-completions", account: account)
    }

    private func fileURL(key: String, account: OfflineAccount) -> URL {
        directory.appendingPathComponent(account.id, isDirectory: true)
            .appendingPathComponent(OfflineAccount.digest(key) + ".json")
    }
}

extension APIError {
    static func permitsOfflineFallback(_ error: Error) -> Bool {
        if let urlError = error as? URLError {
            return [.notConnectedToInternet, .networkConnectionLost, .timedOut,
                    .cannotConnectToHost, .cannotFindHost, .dnsLookupFailed,
                    .dataNotAllowed, .internationalRoamingOff].contains(urlError.code)
        }
        if let error = error as? APIError, case let .server(status, _) = error {
            return (500...599).contains(status)
        }
        return false
    }
}
