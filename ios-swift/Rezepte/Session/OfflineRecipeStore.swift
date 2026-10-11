import Combine
import CryptoKit
import Foundation
import ImageIO
import UIKit

/// Flat, account-prefixed files make erasing former household caches explicit.
/// An index replacement is the commit point; unfinished downloads never appear saved.
struct OfflineRecipeStorage {
    var read: (String) throws -> Data?
    var write: (String, Data) throws -> Void
    var remove: (String) throws -> Void
    var eraseAccount: (String) throws -> Void

    static func files(directory: URL? = nil) -> Self {
        let root = directory ?? FileManager.default.urls(for: .applicationSupportDirectory,
            in: .userDomainMask)[0].appendingPathComponent("OfflineRecipes", isDirectory: true)
        func url(_ key: String) throws -> URL {
            guard !key.isEmpty, key.count < 200,
                  key.allSatisfy({ "abcdefghijklmnopqrstuvwxyz0123456789-._".contains($0) }) else {
                throw CocoaError(.fileWriteInvalidFileName)
            }
            return root.appendingPathComponent(key, isDirectory: false)
        }
        return Self(read: { key in
            let path = try url(key)
            guard FileManager.default.fileExists(atPath: path.path) else { return nil }
            let size = try path.resourceValues(forKeys: [.fileSizeKey, .isSymbolicLinkKey])
            guard size.isSymbolicLink != true, (size.fileSize ?? 0) <= 16 * 1_024 * 1_024 else {
                throw CocoaError(.fileReadCorruptFile)
            }
            return try Data(contentsOf: path)
        }, write: { key, data in
            try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
            guard try root.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink != true else {
                throw CocoaError(.fileWriteUnknown)
            }
            var protectedRoot = root
            var values = URLResourceValues(); values.isExcludedFromBackup = true
            try protectedRoot.setResourceValues(values)
            try data.write(to: url(key), options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        }, remove: { key in
            let path = try url(key)
            guard FileManager.default.fileExists(atPath: path.path) else { return }
            guard try path.resourceValues(forKeys: [.isDirectoryKey]).isDirectory != true else {
                throw CocoaError(.fileWriteUnknown)
            }
            try FileManager.default.removeItem(at: path)
        }, eraseAccount: { digest in
            guard digest.count == 64, digest.allSatisfy({ "0123456789abcdef".contains($0) }) else {
                throw CocoaError(.fileWriteInvalidFileName)
            }
            guard FileManager.default.fileExists(atPath: root.path) else { return }
            guard try root.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink != true else {
                throw CocoaError(.fileWriteUnknown)
            }
            let files = try FileManager.default.contentsOfDirectory(at: root,
                includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey])
            var failure: Error?
            for file in files where file.lastPathComponent.hasPrefix(digest + "-") {
                do {
                    let values = try file.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
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

struct OfflineRecipeContext {
    let identity: UUID
    let namespace: String
    let isCurrent: @MainActor () -> Bool
    let household: @MainActor () async throws -> Int
    let recipe: @MainActor (Int) async throws -> Recipe
    let image: @MainActor (Int) async throws -> Data?
    let cookbookRecipes: @MainActor (Int) async throws -> [HouseholdRecipe]
}

struct OfflineRecipeRecord: Codable, Identifiable {
    let recipe: Recipe
    let savedAt: Date
    let imageKey: String?
    let imageBytes: Int
    var pinned: Bool
    var id: Int { recipe.id }
}

private struct OfflineCookbookRecord: Codable {
    let id: Int
    let name: String
    let recipeIDs: [Int]
    let savedAt: Date
}

struct OfflineCookingProgress: Codable, Equatable {
    let completedSteps: [Int]
    let activeStep: Int
    let servings: Int
}

private struct OfflineRecipeIndex: Codable {
    let version: Int
    let householdID: Int
    var recipes: [String: OfflineRecipeRecord] = [:]
    var cookbooks: [String: OfflineCookbookRecord] = [:]
    var cooking: [String: OfflineCookingProgress] = [:]
}

@MainActor
final class OfflineRecipeStore: ObservableObject {
    @Published private(set) var isDownloading = false
    @Published private(set) var isReady = false
    @Published private(set) var progress: String?
    @Published private(set) var errorMessage: String?
    @Published private var revision = 0
    private let storage: OfflineRecipeStorage
    private let defaults: UserDefaults
    private var context: OfflineRecipeContext?
    private var index: OfflineRecipeIndex?
    private var activation: Task<Void, Never>?
    private var flight: Task<Void, Error>?
    private var operation = UUID()
    private static let maximumRecipes = 400
    private static let maximumImageBytes = 128 * 1_024 * 1_024

    init(storage: OfflineRecipeStorage = .files(), defaults: UserDefaults = .standard) {
        self.storage = storage; self.defaults = defaults
    }

    static func isConnectivityFailure(_ error: Error) -> Bool {
        guard let error = error as? URLError else { return false }
        return [.notConnectedToInternet, .networkConnectionLost, .timedOut,
                .cannotFindHost, .cannotConnectToHost, .dnsLookupFailed,
                .internationalRoamingOff, .dataNotAllowed].contains(error.code)
    }

    private static func namespace(_ session: SessionStore) -> String? {
        guard case .signedIn = session.state, let id = session.userID, session.role != .guest,
              let server = APIClient.normalizedServerURL(session.savedServer)?.absoluteString else { return nil }
        return "\(server)\u{0}\(id)"
    }

    func activate(session: SessionStore) async {
        guard let namespace = Self.namespace(session) else { deactivate(); return }
        let identity = session.identity
        let api = session.api
        await activate(context: OfflineRecipeContext(identity: identity, namespace: namespace,
            isCurrent: { [weak session] in
                guard let session else { return false }
                return session.identity == identity && Self.namespace(session) == namespace
            }, household: {
                let account = try await api.account()
                guard !account.isGuest, let id = account.id, id > 0 else { throw APIError.unauthenticated }
                return id
            }, recipe: { try await api.recipe(id: $0) }, image: { id in
                let request = try await api.imageRequest(recipeID: id, width: 1_200, forceRefresh: true)
                return try await OfflineImageDownload.fetch(request)
            }, cookbookRecipes: { try await api.cookbookRecipes(id: $0) }))
    }

    /// Foreground verification detects membership changes made on another device.
    /// A network outage preserves the last verified household; denial locks it.
    func refreshContext(session: SessionStore) async {
        guard matches(session), let expected = context else { await activate(session: session); return }
        do {
            let household = try await expected.household()
            try assertCurrent(expected)
            guard household > 0 else { throw APIError.unauthenticated }
            if household != index?.householdID {
                try invalidateHousehold()
                // Remount all household-bound screens, including an already open
                // private recipe, when membership changed on another device.
                do { try await session.householdDidChange() }
                catch {
                    // SessionStore owns the transition identity and reports its
                    // failure. The old cache context is deliberately invalid now.
                    return
                }
                await activate(session: session)
            }
        } catch {
            guard isCurrent(expected) else { return }
            if !Self.isConnectivityFailure(error) {
                try? invalidateHousehold()
                errorMessage = error.localizedDescription
            }
        }
    }

    /// Injectable authentication and IO boundaries exercise late-response and disk-failure paths.
    func activate(context next: OfflineRecipeContext) async {
        guard next.isCurrent(), !Task.isCancelled else { return }
        if context?.identity == next.identity, context?.namespace == next.namespace {
            if let activation {
                let waitingGeneration = operation
                await activation.value
                // A suspended caller belongs to the activation it joined. It
                // must never cancel a newer household/account activation.
                guard operation == waitingGeneration, next.isCurrent(), !Task.isCancelled else { return }
            }
            if isReady { return }
        }
        deactivate()
        context = next
        let activatingGeneration = operation
        let task = Task { await loadContext(next) }
        activation = task
        await task.value
        if operation == activatingGeneration { activation = nil }
    }

    private func loadContext(_ next: OfflineRecipeContext) async {
        do {
            try assertCurrent(next)
            var household: Int
            do {
                household = try await next.household()
                try assertCurrent(next)
                guard household > 0 else { throw APIError.invalidResponse("Haushalt") }
                defaults.set(household, forKey: pointerKey(next.namespace))
                defaults.removeObject(forKey: invalidatedKey(next.namespace))
            } catch {
                try assertCurrent(next)
                guard Self.isConnectivityFailure(error) else {
                    defaults.set(true, forKey: invalidatedKey(next.namespace))
                    defaults.removeObject(forKey: pointerKey(next.namespace))
                    throw error
                }
                guard
                      !defaults.bool(forKey: invalidatedKey(next.namespace)),
                      let cached = defaults.object(forKey: pointerKey(next.namespace)) as? Int, cached > 0 else { throw error }
                household = cached
            }
            let key = indexKey(next.namespace, household)
            if let data = try storage.read(key) {
                let decoded = try JSONDecoder().decode(OfflineRecipeIndex.self, from: data)
                guard decoded.version == 1, decoded.householdID == household,
                      decoded.recipes.count <= Self.maximumRecipes,
                      decoded.recipes.allSatisfy({ key, record in
                          key == String(record.id) && record.id > 0 && record.imageBytes >= 0 && record.imageBytes <= OfflineImageDownload.maximumBytes &&
                          (record.imageKey == nil || record.imageKey!.hasPrefix(prefix(next.namespace, household) + "image-"))
                      }), decoded.cookbooks.allSatisfy({ key, book in
                          key == String(book.id) && book.recipeIDs.allSatisfy { decoded.recipes[String($0)] != nil }
                      }) else { throw CocoaError(.fileReadCorruptFile) }
                index = decoded
            } else { index = OfflineRecipeIndex(version: 1, householdID: household) }
            try assertCurrent(next)
            isReady = true; revision += 1
        } catch {
            guard context?.identity == next.identity, next.isCurrent(), !Task.isCancelled else { return }
            index = nil; isReady = false; errorMessage = error.localizedDescription
        }
    }

    func deactivate() {
        activation?.cancel(); activation = nil
        flight?.cancel(); flight = nil
        operation = UUID(); context = nil; index = nil
        isReady = false; isDownloading = false; progress = nil; errorMessage = nil; revision += 1
    }

    func invalidateHousehold() throws {
        guard let context else { deactivate(); return }
        defaults.set(true, forKey: invalidatedKey(context.namespace))
        defaults.removeObject(forKey: pointerKey(context.namespace))
        deactivate()
    }

    func eraseAccountData(session: SessionStore) throws {
        guard let namespace = Self.namespace(session) else {
            throw APIError.server(0, "Die lokalen Rezeptdownloads konnten dem gelöschten Konto nicht sicher zugeordnet werden.")
        }
        try eraseAccountData(namespace: namespace)
    }

    func eraseAccountData(namespace: String) throws {
        defaults.set(true, forKey: erasedKey(namespace))
        defaults.removeObject(forKey: pointerKey(namespace))
        defaults.set(true, forKey: invalidatedKey(namespace))
        if context?.namespace == namespace { deactivate() }
        do { try storage.eraseAccount(digest(namespace)) }
        catch {
            throw APIError.server(0, "Dein Konto wurde gelöscht. Lokale Rezeptdownloads bleiben gesperrt, konnten aber nicht vollständig entfernt werden.")
        }
    }

    func cachedRecipes(session: SessionStore) -> [OfflineRecipeRecord] {
        guard matches(session) else { return [] }
        return (index?.recipes.values.map { $0 } ?? []).sorted { $0.recipe.name.localizedStandardCompare($1.recipe.name) == .orderedAscending }
    }

    func cachedRecipe(id: Int, session: SessionStore) -> Recipe? {
        guard matches(session) else { return nil }
        return index?.recipes[String(id)]?.recipe
    }

    func savedAt(id: Int, session: SessionStore) -> Date? {
        guard matches(session) else { return nil }
        return index?.recipes[String(id)]?.savedAt
    }

    func cachedImage(id: Int, session: SessionStore) -> Data? {
        guard matches(session), let key = index?.recipes[String(id)]?.imageKey else { return nil }
        return try? storage.read(key)
    }

    func cachedCookbooks(session: SessionStore) -> [HouseholdCookbook] {
        guard matches(session) else { return [] }
        return (index?.cookbooks.values.map {
            HouseholdCookbook(id: $0.id, name: $0.name, recipeCount: $0.recipeIDs.count, containsRecipe: false)
        } ?? []).sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }

    func cachedCookbookRecipes(id: Int, session: SessionStore) -> [HouseholdRecipe] {
        guard matches(session), let index, let book = index.cookbooks[String(id)] else { return [] }
        return book.recipeIDs.compactMap { id in
            index.recipes[String(id)].map { HouseholdRecipe(id: id, name: $0.recipe.name) }
        }
    }

    func isCookbookSaved(id: Int, session: SessionStore) -> Bool {
        matches(session) && index?.cookbooks[String(id)] != nil
    }

    func saveRecipe(id: Int, session: SessionStore) async throws {
        await activate(session: session)
        let context = try requireContext(session)
        guard !isDownloading else { throw busyError }
        try await runDownload {
            try await self.download(ids: [id], cookbook: nil, context: context, alreadyBusy: true)
        }
    }

    func saveCookbook(book: HouseholdCookbook, session: SessionStore) async throws {
        await activate(session: session)
        let context = try requireContext(session)
        guard !isDownloading else { throw busyError }
        try await runDownload {
            self.progress = "Kochbuch wird vorbereitet …"
            let recipes = try await context.cookbookRecipes(book.id)
            try self.assertCurrent(context)
            try await self.download(ids: recipes.map(\.id), cookbook: book, context: context, alreadyBusy: true)
        }
    }

    private func runDownload(_ action: @escaping @MainActor () async throws -> Void) async throws {
        isDownloading = true
        let token = operation
        let task = Task { try await action() }
        flight = task
        defer { if operation == token { flight = nil; isDownloading = false; progress = nil } }
        try await withTaskCancellationHandler(operation: { try await task.value }, onCancel: { task.cancel() })
    }

    /// Everything is fetched before replacing the index, including image validation.
    func download(ids: [Int], cookbook: HouseholdCookbook?, context expected: OfflineRecipeContext,
                  alreadyBusy: Bool = false) async throws {
        try assertCurrent(expected)
        guard !isDownloading || alreadyBusy else { throw busyError }
        guard var next = index else { throw APIError.sessionChanged }
        let ids = Array(Set(ids)).sorted()
        guard ids.count <= Self.maximumRecipes, ids.allSatisfy({ $0 > 0 }) else {
            throw APIError.server(0, "Ein Download darf höchstens 400 Rezepte enthalten.")
        }
        isDownloading = true; errorMessage = nil
        let token = operation
        var createdImages: [String] = []
        var committed = false
        defer {
            if !committed { for key in createdImages { try? storage.remove(key) } }
            if operation == token { isDownloading = false; progress = nil }
        }
        do {
            for (offset, id) in ids.enumerated() {
                progress = "Rezept \(offset + 1) von \(ids.count) wird geladen …"
                let recipe = try await expected.recipe(id)
                try assertCurrent(expected)
                guard recipe.id == id else { throw APIError.invalidResponse("Rezeptdownload") }
                let image = try await expected.image(id)
                try assertCurrent(expected)
                let key = image.map { _ in prefix(expected.namespace, next.householdID) + "image-\(id)-\(UUID().uuidString.lowercased()).jpg" }
                if let image, let key {
                    guard image.count <= OfflineImageDownload.maximumBytes else { throw OfflineImageDownload.tooLarge }
                    createdImages.append(key); try storage.write(key, image)
                }
                next.recipes[String(id)] = OfflineRecipeRecord(recipe: recipe, savedAt: Date(), imageKey: key,
                    imageBytes: image?.count ?? 0, pinned: cookbook == nil || next.recipes[String(id)]?.pinned == true)
                guard next.recipes.count <= Self.maximumRecipes,
                      next.recipes.values.reduce(0, { $0 + $1.imageBytes }) <= Self.maximumImageBytes else {
                    throw APIError.server(0, "Der Offline-Speicher ist voll (400 Rezepte oder 128 MB Bilder). Entferne zuerst einige Downloads.")
                }
            }
            if let cookbook {
                next.cookbooks[String(cookbook.id)] = OfflineCookbookRecord(id: cookbook.id,
                    name: cookbook.name, recipeIDs: ids, savedAt: Date())
                pruneUnreferenced(&next)
            }
            try commit(next, expected: expected)
            committed = true
        } catch {
            if operation == token, isCurrent(expected) { errorMessage = error.localizedDescription }
            throw error
        }
    }

    /// Removing a recipe also removes its books, so no partial book claims to be complete.
    func removeRecipe(id: Int, session: SessionStore) throws {
        let context = try requireContext(session)
        guard !isDownloading, var next = index else { throw busyError }
        next.recipes.removeValue(forKey: String(id)); next.cooking.removeValue(forKey: String(id))
        next.cookbooks = next.cookbooks.filter { !$0.value.recipeIDs.contains(id) }
        pruneUnreferenced(&next)
        try commit(next, expected: context)
    }

    func removeCookbook(id: Int, session: SessionStore) throws {
        let context = try requireContext(session)
        guard !isDownloading, var next = index else { throw busyError }
        next.cookbooks.removeValue(forKey: String(id)); pruneUnreferenced(&next)
        try commit(next, expected: context)
    }

    func cookingProgress(id: Int, session: SessionStore) -> OfflineCookingProgress? {
        guard matches(session) else { return nil }
        return index?.cooking[String(id)]
    }

    func saveCookingProgress(id: Int, value: OfflineCookingProgress?, session: SessionStore) throws {
        let context = try requireContext(session)
        guard !isDownloading, var next = index, let recipe = next.recipes[String(id)]?.recipe else {
            throw APIError.server(0, "Speichere dieses Rezept zuerst vollständig offline.")
        }
        if let value {
            guard (1...50).contains(value.servings), recipe.steps.indices.contains(value.activeStep),
                  value.completedSteps.allSatisfy({ recipe.steps.indices.contains($0) }) else {
                throw APIError.invalidResponse("lokaler Kochfortschritt")
            }
        }
        next.cooking[String(id)] = value
        try commit(next, expected: context)
    }

    private func pruneUnreferenced(_ next: inout OfflineRecipeIndex) {
        let referenced = Set(next.cookbooks.values.flatMap(\.recipeIDs))
        next.recipes = next.recipes.filter { $0.value.pinned || referenced.contains($0.value.id) }
        next.cooking = next.cooking.filter { next.recipes[$0.key] != nil }
    }

    private func commit(_ next: OfflineRecipeIndex, expected: OfflineRecipeContext) throws {
        try assertCurrent(expected)
        let data = try JSONEncoder().encode(next)
        guard data.count <= 12 * 1_024 * 1_024 else { throw APIError.server(0, "Der Offline-Speicher für Rezepttexte ist voll.") }
        try storage.write(indexKey(expected.namespace, next.householdID), data)
        let retained = Set(next.recipes.values.compactMap(\.imageKey))
        let obsolete = (index?.recipes.values.compactMap(\.imageKey) ?? []).filter { !retained.contains($0) }
        index = next; revision += 1
        for key in obsolete {
            do { try storage.remove(key) }
            catch { errorMessage = "Die Downloadliste wurde aktualisiert. Eine alte Bilddatei konnte noch nicht vom Gerät entfernt werden." }
        }
    }

    private func matches(_ session: SessionStore) -> Bool {
        guard let context, context.identity == session.identity,
              context.namespace == Self.namespace(session), isReady else { return false }
        return isCurrent(context)
    }
    private func requireContext(_ session: SessionStore) throws -> OfflineRecipeContext {
        guard matches(session), let context else {
            throw APIError.server(0, errorMessage ?? "Der Offline-Speicher ist noch nicht bereit. Stelle zunächst eine Verbindung zum Haushalt her.")
        }
        return context
    }
    private func isCurrent(_ expected: OfflineRecipeContext) -> Bool {
        context?.identity == expected.identity && context?.namespace == expected.namespace &&
        !defaults.bool(forKey: erasedKey(expected.namespace)) && expected.isCurrent() && !Task.isCancelled
    }
    private func assertCurrent(_ expected: OfflineRecipeContext) throws {
        guard isCurrent(expected) else { throw APIError.sessionChanged }
    }
    private var busyError: APIError { .server(0, "Ein Download läuft bereits. Bitte warte kurz.") }
    private func digest(_ value: String) -> String { SHA256.hash(data: Data(value.utf8)).map { String(format: "%02x", $0) }.joined() }
    private func prefix(_ namespace: String, _ household: Int) -> String { "\(digest(namespace))-household-\(household)-" }
    private func indexKey(_ namespace: String, _ household: Int) -> String { prefix(namespace, household) + "index.json" }
    private func pointerKey(_ namespace: String) -> String { "offline-recipes-\(digest(namespace))-household" }
    private func invalidatedKey(_ namespace: String) -> String { "offline-recipes-\(digest(namespace))-invalidated" }
    private func erasedKey(_ namespace: String) -> String { "offline-recipes-\(digest(namespace))-erased" }
}

/// Images use only the server-created authenticated thumbnail request. Never follow
/// redirects with its token, never download source URLs, and bound both bytes/pixels.
enum OfflineImageDownload {
    static let maximumBytes = 8 * 1_024 * 1_024
    static let tooLarge = APIError.server(0, "Das Rezeptbild ist zu groß für den Offline-Download.")

    static func fetch(_ request: URLRequest) async throws -> Data? {
        let delegate = OfflineImageRedirectBlocker()
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 30
        config.timeoutIntervalForResource = 60
        config.urlCache = nil
        let session = URLSession(configuration: config, delegate: delegate, delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        let (bytes, response) = try await session.bytes(for: request)
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse("Rezeptbild") }
        if http.statusCode == 404 { return nil }
        if http.statusCode == 401 { throw APIError.unauthenticated }
        guard (200..<300).contains(http.statusCode), response.mimeType?.hasPrefix("image/") == true else {
            throw APIError.server(http.statusCode, "Das Rezeptbild konnte nicht heruntergeladen werden.")
        }
        guard response.expectedContentLength <= Int64(maximumBytes) else { throw tooLarge }
        var data = Data()
        for try await byte in bytes {
            if data.count >= maximumBytes { throw tooLarge }
            data.append(byte)
        }
        try Task.checkCancellation()
        return try validatedJPEG(data)
    }

    static func validatedJPEG(_ data: Data) throws -> Data {
        guard data.count <= maximumBytes,
              let source = CGImageSourceCreateWithData(data as CFData, [kCGImageSourceShouldCache: false] as CFDictionary),
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = properties[kCGImagePropertyPixelWidth] as? Int,
              let height = properties[kCGImagePropertyPixelHeight] as? Int,
              width > 0, height > 0, width <= 20_000, height <= 20_000, width * height <= 40_000_000,
              let image = CGImageSourceCreateThumbnailAtIndex(source, 0, [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceCreateThumbnailWithTransform: true,
                kCGImageSourceThumbnailMaxPixelSize: 1_600
              ] as CFDictionary), let jpeg = UIImage(cgImage: image).jpegData(compressionQuality: 0.88) else {
            throw APIError.invalidResponse("Rezeptbild")
        }
        return jpeg
    }
}

private final class OfflineImageRedirectBlocker: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}
