import Combine
import Foundation

@MainActor
final class SessionStore: ObservableObject {
    enum State: Equatable {
        case checking
        case signedOut
        case signedIn
    }

    @Published private(set) var state: State = .checking
    @Published private(set) var username = ""
    @Published private(set) var fullAccess = false
    @Published private(set) var readOnly = false
    @Published private(set) var serverVersion = ""
    @Published private(set) var serverCapabilities: Set<String> = []
    @Published private(set) var compatibilityWarning: String?
    @Published private(set) var isOffline = false
    @Published private(set) var offlineAccount: OfflineAccount?
    @Published private(set) var pendingCookingCount = 0
    @Published var alertMessage: String?

    let api: APIClient
    let offlineStore: OfflineStore
    private let defaults: UserDefaults
    private let tokenAccount: String
    private let lastAccountKey = "offline-account-v1"
    private var isSyncingCooking = false
    private var sessionGeneration = UUID()
    private let cloudflareClientIDAccount = "cloudflare-client-id"
    private let cloudflareClientSecretAccount = "cloudflare-client-secret"
    private let serverKey = "server-url"

    init(api: APIClient = APIClient(), defaults: UserDefaults = .standard,
         offlineStore: OfflineStore = .shared, tokenAccount: String = "api-token") {
        self.api = api
        self.defaults = defaults
        self.offlineStore = offlineStore
        self.tokenAccount = tokenAccount
    }

    var savedServer: String {
        defaults.string(forKey: serverKey) ?? ""
    }

    var savedCloudflareClientID: String {
        KeychainStore.read(account: cloudflareClientIDAccount) ?? ""
    }

    var savedCloudflareClientSecret: String {
        KeychainStore.read(account: cloudflareClientSecretAccount) ?? ""
    }

    func restore() async {
        let generation = sessionGeneration
        guard !savedServer.isEmpty,
              let token = KeychainStore.read(account: tokenAccount) else {
            state = .signedOut
            return
        }
        do {
            let cloudflareCredentials = try CloudflareAccessCredentials(
                clientID: savedCloudflareClientID,
                clientSecret: savedCloudflareClientSecret
            )
            try await api.configure(
                server: savedServer,
                token: token,
                cloudflareCredentials: cloudflareCredentials
            )
            let session = try await api.sessionInfo()
            guard generation == sessionGeneration else { return }
            await establish(session, server: savedServer)
            guard generation == sessionGeneration else { return }
            await refreshSystemInfo()
            guard generation == sessionGeneration else { return }
            saveOfflineSession(token: token)
            await syncCooking()
            await syncCookingMemory()
            if !readOnly { await drainSharedImports() }
        } catch {
            guard generation == sessionGeneration else { return }
            if let apiError = error as? APIError, case .unauthenticated = apiError {
                signOut()
            } else if APIError.permitsOfflineFallback(error),
                      let account = savedOfflineAccount,
                      let cached = try? offlineStore.read(OfflineSession.self, key: "session", account: account),
                      cached.matches(server: savedServer, token: token) {
                offlineAccount = cached.account
                offlineStore.activate(account: cached.account)
                username = cached.account.username
                fullAccess = cached.fullAccess
                readOnly = cached.readOnly
                serverVersion = cached.serverVersion
                serverCapabilities = cached.capabilities
                isOffline = true
                await api.setOfflineAccount(cached.account)
                guard generation == sessionGeneration else { return }
                refreshPendingCookingCount()
                state = .signedIn
            } else {
                // Keep credentials on transport/configuration failures, even if
                // this device has no verified offline session yet.
                state = .signedOut
                alertMessage = error.localizedDescription
            }
        }
    }

    func signIn(
        server: String,
        username: String,
        password: String,
        cloudflareClientID: String,
        cloudflareClientSecret: String
    ) async throws {
        let generation = UUID()
        sessionGeneration = generation
        let cloudflareCredentials = try CloudflareAccessCredentials(
            clientID: cloudflareClientID,
            clientSecret: cloudflareClientSecret
        )
        try await api.configure(
            server: server,
            token: nil,
            cloudflareCredentials: cloudflareCredentials
        )
        let response = try await api.login(username: username, password: password)
        guard generation == sessionGeneration else { throw CancellationError() }
        try await activate(
            server: server,
            token: response.token,
            cloudflareCredentials: cloudflareCredentials,
            generation: generation
        )
    }

    func signInAsGuest(
        server: String,
        cloudflareClientID: String,
        cloudflareClientSecret: String
    ) async throws {
        let generation = UUID()
        sessionGeneration = generation
        let cloudflareCredentials = try CloudflareAccessCredentials(
            clientID: cloudflareClientID,
            clientSecret: cloudflareClientSecret
        )
        try await api.configure(
            server: server,
            token: nil,
            cloudflareCredentials: cloudflareCredentials
        )
        let response = try await api.guestLogin()
        guard generation == sessionGeneration else { throw CancellationError() }
        try await activate(
            server: server,
            token: response.token,
            cloudflareCredentials: cloudflareCredentials,
            generation: generation
        )
    }

    func signOut() {
        sessionGeneration = UUID()
        let oldAccount = offlineAccount ?? savedOfflineAccount
        if let oldAccount {
            do { try offlineStore.clear(account: oldAccount) }
            catch { alertMessage = "Lokale Rezeptdaten konnten nicht vollständig entfernt werden: \(error.localizedDescription)" }
            CookingTimerNotifications.cancel(account: oldAccount)
        }
        defaults.removeObject(forKey: lastAccountKey)
        KeychainStore.delete(account: tokenAccount)
        offlineAccount = nil
        isOffline = false
        pendingCookingCount = 0
        username = ""
        fullAccess = false
        readOnly = false
        serverVersion = ""
        serverCapabilities = []
        compatibilityWarning = nil
        state = .signedOut
        Task { await api.clearCredentials() }
    }

    func refreshAccess() async {
        guard case .signedIn = state else { return }
        let generation = sessionGeneration
        do {
            let session = try await api.sessionInfo()
            guard generation == sessionGeneration else { return }
            await establish(session, server: savedServer)
            guard generation == sessionGeneration else { return }
            await refreshSystemInfo()
            guard generation == sessionGeneration else { return }
            if let token = KeychainStore.read(account: tokenAccount) { saveOfflineSession(token: token) }
            await syncCooking()
            await syncCookingMemory()
        } catch {
            guard generation == sessionGeneration else { return }
            handle(error)
        }
    }

    private func saveCloudflareCredentials(_ credentials: CloudflareAccessCredentials?) throws {
        guard let credentials else {
            KeychainStore.delete(account: cloudflareClientIDAccount)
            KeychainStore.delete(account: cloudflareClientSecretAccount)
            return
        }
        try KeychainStore.save(credentials.clientID, account: cloudflareClientIDAccount)
        try KeychainStore.save(credentials.clientSecret, account: cloudflareClientSecretAccount)
    }

    private func activate(
        server: String,
        token: String,
        cloudflareCredentials: CloudflareAccessCredentials?,
        generation: UUID
    ) async throws {
        try await api.configure(
            server: server,
            token: token,
            cloudflareCredentials: cloudflareCredentials
        )
        let activeSession = try await api.sessionInfo()
        guard generation == sessionGeneration else { throw CancellationError() }
        try KeychainStore.save(token, account: tokenAccount)
        try saveCloudflareCredentials(cloudflareCredentials)
        defaults.set(
            server.trimmingCharacters(in: .whitespacesAndNewlines),
            forKey: serverKey
        )
        await establish(activeSession, server: server)
        guard generation == sessionGeneration else { throw CancellationError() }
        await refreshSystemInfo()
        guard generation == sessionGeneration else { throw CancellationError() }
        saveOfflineSession(token: token)
        await syncCooking()
        await syncCookingMemory()
        if !readOnly { await drainSharedImports() }
    }

    private func apply(_ session: SessionResponse) {
        username = session.username
        fullAccess = session.fullAccess ?? false
        readOnly = session.readOnly ?? false
        state = .signedIn
    }

    private var savedOfflineAccount: OfflineAccount? {
        guard let data = defaults.data(forKey: lastAccountKey) else { return nil }
        return try? JSONDecoder().decode(OfflineAccount.self, from: data)
    }

    private func establish(_ session: SessionResponse, server: String) async {
        let generation = sessionGeneration
        guard let account = OfflineAccount(server: server, username: session.username) else { return }
        if let previous = offlineAccount ?? savedOfflineAccount, previous != account {
            try? offlineStore.clear(account: previous)
            CookingTimerNotifications.cancel(account: previous)
        }
        offlineAccount = account
        offlineStore.activate(account: account)
        defaults.set(try? JSONEncoder().encode(account), forKey: lastAccountKey)
        await api.setOfflineAccount(account)
        guard generation == sessionGeneration else { return }
        isOffline = false
        apply(session)
        refreshPendingCookingCount()
    }

    private func saveOfflineSession(token: String) {
        guard let account = offlineAccount else { return }
        let cached = OfflineSession(account: account, tokenFingerprint: OfflineAccount.digest(token),
                                    fullAccess: fullAccess, readOnly: readOnly,
                                    capabilities: serverCapabilities, serverVersion: serverVersion)
        do { try offlineStore.write(cached, key: "session", account: account) }
        catch { alertMessage = "Offline-Sitzung konnte nicht gespeichert werden: \(error.localizedDescription)" }
    }

    func refreshPendingCookingCount() {
        guard let account = offlineAccount else { pendingCookingCount = 0; return }
        pendingCookingCount = (try? offlineStore.completions(account: account).count) ?? 0
    }

    /// Serialize writes, but never hold up a local cooking action. Callers launch
    /// this from a Task after their durable local update has succeeded.
    func syncCooking() async {
        guard !isSyncingCooking, !readOnly, !isOffline,
              let account = offlineAccount, case .signedIn = state else { return }
        let generation = sessionGeneration
        isSyncingCooking = true
        defer { isSyncingCooking = false; refreshPendingCookingCount() }
        do {
            while generation == sessionGeneration, account == offlineAccount, !readOnly {
                let completions = try offlineStore.completions(account: account)
                let pending = try offlineStore.progress(account: account).filter(\.needsSync)
                guard !completions.isEmpty || !pending.isEmpty else { break }
                // Completion clears server progress, so new-run progress follows it.
                for completion in completions {
                    guard generation == sessionGeneration, account == offlineAccount, !readOnly else { return }
                    _ = try await api.completeCooking(id: completion.recipeID, servings: completion.servings,
                                                     idempotencyKey: completion.id, expectedAccount: account)
                    guard generation == sessionGeneration, account == offlineAccount else { return }
                    try offlineStore.removeCompletion(id: completion.id, account: account)
                }
                for progress in pending {
                    guard generation == sessionGeneration, account == offlineAccount, !readOnly else { return }
                    // A tap or completion may have superseded this snapshot while
                    // another request was in flight. Only send the current run.
                    guard try offlineStore.progress(account: account).contains(where: {
                        $0.recipeID == progress.recipeID && $0.revision == progress.revision
                    }) else { continue }
                    if progress.started {
                        _ = try await api.updateCookingProgress(id: progress.recipeID,
                            completedSteps: progress.completedSteps.sorted(), activeStep: progress.activeStep,
                            servings: progress.servings, expectedAccount: account)
                    } else {
                        _ = try await api.clearCookingProgress(id: progress.recipeID, expectedAccount: account)
                    }
                    guard generation == sessionGeneration, account == offlineAccount else { return }
                    try offlineStore.markProgressSynced(progress, account: account)
                }
            }
        } catch {
            guard generation == sessionGeneration else { return }
            if APIError.permitsOfflineFallback(error) { isOffline = true }
            else { handle(error) }
        }
    }

    func supports(_ capability: String) -> Bool {
        serverCapabilities.contains(capability)
    }

    private func refreshSystemInfo() async {
        let generation = sessionGeneration
        do {
            let info = try await api.systemInfo()
            guard generation == sessionGeneration else { return }
            serverVersion = info.version
            serverCapabilities = Set(info.capabilities)
            let required = Set([
                "shopping-categories",
                "recurring-shopping",
                "weekly-meal-plan"
            ])
            let missing = required.subtracting(serverCapabilities).sorted()
            compatibilityWarning = missing.isEmpty
                ? nil
                : "Der Server ist älter als diese App. Es fehlen: \(missing.joined(separator: ", "))."
        } catch {
            guard generation == sessionGeneration else { return }
            serverVersion = "unbekannt"
            serverCapabilities = []
            compatibilityWarning = "Serverversion konnte nicht geprüft werden. App und Server bitte gemeinsam aktualisieren."
        }
    }

    func handle(_ error: Error) {
        if let apiError = error as? APIError,
           case .unauthenticated = apiError {
            signOut()
        }
        if APIError.permitsOfflineFallback(error), offlineAccount != nil {
            isOffline = true
            return
        }
        alertMessage = error.localizedDescription
    }

    func drainSharedImports() async {
        guard case .signedIn = state, !readOnly, !isOffline else { return }
        let queued = SharedImportQueue.all()
        guard !queued.isEmpty else { return }
        var imported = 0
        for url in queued {
            do {
                _ = try await api.importURL(url)
                SharedImportQueue.remove(url)
                imported += 1
            } catch {
                alertMessage = "Ein geteilter Link konnte noch nicht importiert werden: \(error.localizedDescription)"
                break
            }
        }
        if imported > 0 {
            alertMessage = imported == 1
                ? "Der geteilte Rezeptlink wurde importiert."
                : "\(imported) geteilte Links wurden importiert."
        }
    }
}
