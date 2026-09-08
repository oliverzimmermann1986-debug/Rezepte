import Foundation
import XCTest
@testable import Rezepte

final class CookingMemorySyncTests: XCTestCase {
    @MainActor
    func testRejectedStaleStepRemainsForReviewWhileFollowingNoteSynchronizes() async throws {
        let context = try await MemorySyncTestContext.make()
        defer { context.cleanUp() }
        var stale = CookingMemoryRequest(note: "Tipp für einen alten Schritt", stepNumber: 2,
                                         stepInstruction: "Frühere Anleitung")
        stale.clientEntryId = "stale-step"
        var valid = CookingMemoryRequest(note: "Beim nächsten Mal weniger Salz", servings: 3)
        valid.clientEntryId = "valid-note"
        context.transport.setHandler { request in
            guard request.method == "POST", request.path == "/api/recipes/42/cooking-memory" else {
                return .unexpected
            }
            if request.clientEntryID == "stale-step" {
                return MemorySyncReply(status: 409, json: "{\"detail\":\"Der Schritt wurde geändert.\"}")
            }
            return .saved(clientEntryID: "valid-note", note: "Beim nächsten Mal weniger Salz")
        }
        try CookingMemoryStorage.enqueue(recipeID: 42, request: stale, session: context.session)
        try CookingMemoryStorage.enqueue(recipeID: 42, request: valid, session: context.session)

        await CookingMemoryStorage.sync(session: context.session)

        let archive = try CookingMemoryStorage.load(session: context.session)
        XCTAssertEqual(archive.pending.map(\.id), ["stale-step"])
        XCTAssertEqual(archive.pending.first?.error, "Der Schritt wurde geändert.")
        XCTAssertEqual(archive.pending.first?.request.stepInstruction, "Frühere Anleitung")
        XCTAssertEqual(archive.entries["42"]?.map(\.clientEntryId), ["valid-note"])
        XCTAssertEqual(context.transport.memoryRequests.map(\.method), ["POST", "POST"])
        XCTAssertEqual(context.transport.memoryRequests.compactMap(\.clientEntryID), ["stale-step", "valid-note"])
        XCTAssertTrue(context.transport.memoryRequests.allSatisfy { $0.authorization == "Bearer \(context.token)" })
        XCTAssertFalse(context.session.isOffline)

        // Verify the result on disk, not just the in-memory archive value.
        let reloaded = try XCTUnwrap(OfflineStore(directory: context.directory).read(
            CookingMemoryArchive.self, key: CookingMemoryStorage.key, account: context.account
        ))
        XCTAssertEqual(reloaded.pending.map(\.id), ["stale-step"])
        XCTAssertEqual(reloaded.entries["42"]?.map(\.clientEntryId), ["valid-note"])
    }

    @MainActor
    func testCancelledPendingNoteSendsDeleteByClientIDAndNeverPosts() async throws {
        let context = try await MemorySyncTestContext.make()
        defer { context.cleanUp() }
        var request = CookingMemoryRequest(note: "Diese Notiz soll nicht übertragen werden")
        request.clientEntryId = "cancel-before-post"
        context.transport.setHandler { request in
            guard request.method == "DELETE",
                  request.path == "/api/recipes/42/cooking-memory/client/cancel-before-post" else {
                return .unexpected
            }
            return MemorySyncReply(status: 200, json: "{\"ok\":true}")
        }
        try CookingMemoryStorage.enqueue(recipeID: 42, request: request, session: context.session)
        let item = try XCTUnwrap(CookingMemoryStorage.load(session: context.session).pending.first)
        try CookingMemoryStorage.cancel(item, session: context.session)

        await CookingMemoryStorage.sync(session: context.session)

        let archive = try CookingMemoryStorage.load(session: context.session)
        XCTAssertTrue(archive.pending.isEmpty)
        XCTAssertTrue((archive.entries["42"] ?? []).isEmpty)
        XCTAssertEqual(context.transport.memoryRequests.map(\.method), ["DELETE"])
        XCTAssertEqual(context.transport.memoryRequests.map(\.path), ["/api/recipes/42/cooking-memory/client/cancel-before-post"])
        XCTAssertEqual(context.transport.memoryRequests.first?.authorization, "Bearer \(context.token)")
    }

    @MainActor
    func testCancelDuringInflightPOSTDeletesServerEntryWithoutMakingItVisible() async throws {
        let context = try await MemorySyncTestContext.make()
        defer { context.cleanUp() }
        let postReachedServer = expectation(description: "POST reached the controlled transport")
        let postGate = MemorySyncResponseGate { postReachedServer.fulfill() }
        let deleteReachedServer = expectation(description: "Cancellation DELETE reached the controlled transport")
        let deleteGate = MemorySyncResponseGate { deleteReachedServer.fulfill() }
        defer { postGate.release() }
        defer { deleteGate.release() }
        var request = CookingMemoryRequest(note: "Während der Übertragung zurückgenommen")
        request.clientEntryId = "cancel-inflight"
        context.transport.setHandler { request in
            if request.method == "POST", request.path == "/api/recipes/42/cooking-memory",
               request.clientEntryID == "cancel-inflight" {
                return .saved(clientEntryID: "cancel-inflight", note: "Während der Übertragung zurückgenommen",
                              gate: postGate)
            }
            if request.method == "DELETE", request.path == "/api/recipes/42/cooking-memory/client/cancel-inflight" {
                return MemorySyncReply(status: 200, json: "{\"ok\":true}", gate: deleteGate)
            }
            return .unexpected
        }
        try CookingMemoryStorage.enqueue(recipeID: 42, request: request, session: context.session)
        let original = try XCTUnwrap(CookingMemoryStorage.load(session: context.session).pending.first)
        let syncTask = Task { await CookingMemoryStorage.sync(session: context.session) }
        await fulfillment(of: [postReachedServer], timeout: 3)

        // Mutate the actual durable queue while URLSession is awaiting the POST.
        try CookingMemoryStorage.cancel(original, session: context.session)
        XCTAssertEqual(try CookingMemoryStorage.load(session: context.session).pending.first?.cancelled, true)
        XCTAssertTrue((try CookingMemoryStorage.load(session: context.session).entries["42"] ?? []).isEmpty)
        postGate.release()
        await fulfillment(of: [deleteReachedServer], timeout: 3)
        let cancelling = try CookingMemoryStorage.load(session: context.session)
        XCTAssertTrue((cancelling.entries["42"] ?? []).isEmpty, "The successful POST must not flash a cancelled entry while DELETE is pending")
        XCTAssertEqual(cancelling.pending.first?.cancelled, true)
        deleteGate.release()
        await syncTask.value

        let archive = try CookingMemoryStorage.load(session: context.session)
        XCTAssertTrue(archive.pending.isEmpty)
        XCTAssertTrue((archive.entries["42"] ?? []).isEmpty, "The POST response must not resurrect a cancelled note")
        XCTAssertEqual(context.transport.memoryRequests.map(\.method), ["POST", "DELETE"])
        XCTAssertEqual(context.transport.memoryRequests.map(\.path), [
            "/api/recipes/42/cooking-memory", "/api/recipes/42/cooking-memory/client/cancel-inflight",
        ])
        let reloaded = try XCTUnwrap(OfflineStore(directory: context.directory).read(
            CookingMemoryArchive.self, key: CookingMemoryStorage.key, account: context.account
        ))
        XCTAssertTrue(reloaded.pending.isEmpty)
        XCTAssertTrue((reloaded.entries["42"] ?? []).isEmpty)
    }
}

/// This fixture restores a real SessionStore through its authenticated endpoints.
/// Every test gets a different host, account, keychain key and disk directory.
@MainActor
private final class MemorySyncTestContext {
    let session: SessionStore
    let transport: MemorySyncTransport
    let directory: URL
    let account: OfflineAccount
    let token: String
    private let tokenAccount: String
    private let defaults: UserDefaults
    private let defaultsName: String
    private let urlSession: URLSession

    private init(session: SessionStore, transport: MemorySyncTransport, directory: URL,
                 account: OfflineAccount, token: String, tokenAccount: String,
                 defaults: UserDefaults, defaultsName: String, urlSession: URLSession) {
        self.session = session
        self.transport = transport
        self.directory = directory
        self.account = account
        self.token = token
        self.tokenAccount = tokenAccount
        self.defaults = defaults
        self.defaultsName = defaultsName
        self.urlSession = urlSession
    }

    static func make() async throws -> MemorySyncTestContext {
        let suffix = UUID().uuidString.lowercased()
        let host = "memory-\(suffix).example.test"
        let username = "memory-user-\(suffix)"
        let account = try XCTUnwrap(OfflineAccount(server: "https://\(host)", username: username))
        let defaultsName = "CookingMemorySyncTests-\(suffix)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: defaultsName))
        let tokenAccount = "memory-sync-token-\(suffix)"
        let token = "isolated-token-\(suffix)"
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(defaultsName, isDirectory: true)
        let store = OfflineStore(directory: directory)
        let transport = MemorySyncTransport(host: host, username: username)
        let urlSession = transport.makeSession()
        let api = APIClient(session: urlSession, offlineStore: store)
        let session = SessionStore(api: api, defaults: defaults, offlineStore: store, tokenAccount: tokenAccount)
        let context = MemorySyncTestContext(session: session, transport: transport, directory: directory,
            account: account, token: token, tokenAccount: tokenAccount, defaults: defaults,
            defaultsName: defaultsName, urlSession: urlSession)
        do {
            defaults.set(account.server, forKey: "server-url")
            try KeychainStore.save(token, account: tokenAccount)
            await session.restore()
            XCTAssertEqual(session.state, .signedIn)
            XCTAssertEqual(session.offlineAccount, account)
            XCTAssertFalse(session.readOnly)
            XCTAssertFalse(session.isOffline)
            XCTAssertTrue(session.supports("cooking-memory-v1"))
            return context
        } catch {
            context.cleanUp()
            throw error
        }
    }

    func cleanUp() {
        // Revoke the disk scope before cancelling transport callbacks, including
        // failure paths where an assertion/throw interrupted an in-flight sync.
        session.signOut()
        urlSession.invalidateAndCancel()
        transport.unregister()
        KeychainStore.delete(account: tokenAccount)
        defaults.removePersistentDomain(forName: defaultsName)
        if FileManager.default.fileExists(atPath: directory.path) {
            do { try FileManager.default.removeItem(at: directory) }
            catch { XCTFail("Failed to remove isolated test cache: \(error)") }
        }
    }
}

private struct MemorySyncCapturedRequest {
    let method: String
    let path: String
    let authorization: String?
    let clientEntryID: String?

    init(_ request: URLRequest) {
        method = request.httpMethod ?? "GET"
        path = request.url?.path ?? ""
        authorization = request.value(forHTTPHeaderField: "Authorization")
        var data = request.httpBody ?? Data()
        if data.isEmpty, let stream = request.httpBodyStream {
            stream.open()
            defer { stream.close() }
            var buffer = [UInt8](repeating: 0, count: 4096)
            while stream.hasBytesAvailable {
                let count = stream.read(&buffer, maxLength: buffer.count)
                guard count > 0 else { break }
                data.append(buffer, count: count)
            }
        }
        let body = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        clientEntryID = body?["client_entry_id"] as? String
    }
}

private struct MemorySyncReply {
    let status: Int
    let json: String
    var gate: MemorySyncResponseGate? = nil

    static let unexpected = MemorySyncReply(status: 500, json: "{\"detail\":\"Unexpected test request\"}")

    static func saved(clientEntryID: String, note: String, gate: MemorySyncResponseGate? = nil) -> Self {
        // The values are fixed test data, not user input.
        Self(status: 200, json: """
        {"ok":true,"entry":{"id":101,"client_entry_id":"\(clientEntryID)","recipe_id":42,
        "created_at":1725000000,"note":"\(note)","adjustments":"","next_time":"",
        "servings":3,"step_number":null,"step_instruction":null,"step_is_current":null}}
        """, gate: gate)
    }
}

/// Explicit release avoids timing sleeps and deterministically pauses URLSession.
private final class MemorySyncResponseGate: @unchecked Sendable {
    private let lock = NSLock()
    private var released = false
    private var callback: (() -> Void)?
    private let onHeld: () -> Void

    init(onHeld: @escaping () -> Void) { self.onHeld = onHeld }

    func hold(_ callback: @escaping () -> Void) {
        lock.lock()
        let alreadyReleased = released
        if !alreadyReleased { self.callback = callback }
        lock.unlock()
        onHeld()
        if alreadyReleased { callback() }
    }

    func release() {
        lock.lock()
        released = true
        let callback = self.callback
        self.callback = nil
        lock.unlock()
        callback?()
    }
}

/// Registry is keyed per isolated hostname, rather than one mutable global reply.
private final class MemorySyncTransport: @unchecked Sendable {
    private static let registryLock = NSLock()
    nonisolated(unsafe) private static var registry: [String: MemorySyncTransport] = [:]
    private let lock = NSLock()
    private let host: String
    private let username: String
    private var captured: [MemorySyncCapturedRequest] = []
    private var handler: (MemorySyncCapturedRequest) -> MemorySyncReply = { _ in .unexpected }

    init(host: String, username: String) {
        self.host = host
        self.username = username
        Self.registryLock.lock()
        Self.registry[host] = self
        Self.registryLock.unlock()
    }

    func unregister() {
        Self.registryLock.lock()
        Self.registry.removeValue(forKey: host)
        Self.registryLock.unlock()
    }

    static func lookup(_ host: String?) -> MemorySyncTransport? {
        guard let host else { return nil }
        registryLock.lock()
        defer { registryLock.unlock() }
        return registry[host]
    }

    func makeSession() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MemorySyncURLProtocol.self]
        return URLSession(configuration: configuration)
    }

    func setHandler(_ handler: @escaping (MemorySyncCapturedRequest) -> MemorySyncReply) {
        lock.lock()
        self.handler = handler
        lock.unlock()
    }

    var memoryRequests: [MemorySyncCapturedRequest] {
        lock.lock()
        defer { lock.unlock() }
        return captured.filter { $0.path.contains("/cooking-memory") }
    }

    func reply(to request: URLRequest) -> MemorySyncReply {
        let captured = MemorySyncCapturedRequest(request)
        lock.lock()
        self.captured.append(captured)
        let handler = self.handler
        lock.unlock()
        if captured.method == "GET", captured.path == "/api/auth/session" {
            return MemorySyncReply(status: 200, json: "{\"username\":\"\(username)\",\"full_access\":false,\"read_only\":false}")
        }
        if captured.method == "GET", captured.path == "/api/system/info" {
            return MemorySyncReply(status: 200, json: """
            {"name":"Rezeptregal Test","version":"1.8.0","capabilities":["cooking-memory-v1",
            "shopping-categories","recurring-shopping","weekly-meal-plan"]}
            """)
        }
        return handler(captured)
    }
}

private final class MemorySyncURLProtocol: URLProtocol {
    private let lock = NSLock()
    private var stopped = false

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let transport = MemorySyncTransport.lookup(request.url?.host) else {
            client?.urlProtocol(self, didFailWithError: URLError(.unsupportedURL))
            return
        }
        let reply = transport.reply(to: request)
        if let gate = reply.gate {
            gate.hold { [weak self] in self?.complete(reply) }
        } else { complete(reply) }
    }

    private func complete(_ reply: MemorySyncReply) {
        lock.lock()
        guard !stopped else { lock.unlock(); return }
        stopped = true
        lock.unlock()
        guard let url = request.url,
              let response = HTTPURLResponse(url: url, statusCode: reply.status, httpVersion: nil,
                                             headerFields: ["Content-Type": "application/json"]) else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(reply.json.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {
        lock.lock()
        stopped = true
        lock.unlock()
    }
}
