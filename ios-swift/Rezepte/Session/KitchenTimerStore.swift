import ActivityKit
import Combine
import CryptoKit
import Foundation
import UserNotifications

struct KitchenTimerEntry: Codable, Identifiable, Equatable {
    let id: String
    var label: String
    var duration: Int
    var remaining: TimeInterval
    var endsAt: Date?
    func seconds(at now: Date = Date()) -> Int {
        let value = endsAt?.timeIntervalSince(now) ?? remaining
        guard value.isFinite else { return 0 }
        return Int(ceil(min(86400, max(0, value))))
    }

    static func identifier(recipeID: Int, stepID: Int?, index: Int) -> String {
        "\(recipeID)-\(stepID ?? index)"
    }
}

@MainActor final class KitchenTimerStore: ObservableObject {
    static let shared = KitchenTimerStore()
    @Published private(set) var entries: [KitchenTimerEntry] = []
    @Published var message: String?
    @Published private(set) var startingIDs: Set<String> = []
    private var identity: UUID?
    private var namespace = ""
    private var generation = UUID()
    private var effectsGeneration = UUID()
    private var starts: [String: UUID] = [:]
    private var effects: Task<Void, Never>?
    private let prefix = "rezepte.kitchen.timer."
    private let center = UNUserNotificationCenter.current()
    private let defaults: UserDefaults
    private let directory: URL?
    private let requestAuthorization: () async -> Bool
    private let systemEffects: Bool

    init(defaults: UserDefaults = .standard, directory: URL? = nil,
         requestAuthorization: @escaping () async -> Bool = {
             (try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound])) ?? false
         }, systemEffects: Bool = true) {
        self.defaults = defaults
        self.directory = directory
        self.requestAuthorization = requestAuthorization
        self.systemEffects = systemEffects
    }

    func activate(session: SessionStore) async {
        activate(identity: session.identity, server: session.savedServer, userID: session.userID,
                 writable: !session.readOnly && session.role != .guest)
    }

    func activate(identity nextIdentity: UUID, server: String, userID: Int?, writable: Bool) {
        guard writable, let userID else { clear(); return }
        guard identity != nextIdentity else { return }
        if identity != nil { clear() }
        identity = nextIdentity
        namespace = OfflineSessionSnapshot.digest("\(APIClient.normalizedServerURL(server)?.absoluteString ?? server)\u{0}\(userID)")
        do {
            let url = try storageURL()
            if !defaults.bool(forKey: "kitchen-timers-cleared-\(namespace)"), FileManager.default.fileExists(atPath: url.path) {
                let loaded = try JSONDecoder().decode([KitchenTimerEntry].self, from: Data(contentsOf: url))
                var seen = Set<String>()
                entries = loaded.filter {
                    !$0.id.isEmpty && $0.id.count <= 200 && seen.insert($0.id).inserted && $0.label.count <= 300
                    && $0.duration > 0 && $0.duration <= 86400 && $0.remaining.isFinite && $0.remaining >= 0 && $0.remaining <= 86400
                    && ($0.endsAt.map { $0.timeIntervalSince1970.isFinite && $0.timeIntervalSinceNow <= 86400 } ?? true)
                }
            }
        } catch { message = "Gespeicherte Timer konnten nicht geladen werden: \(error.localizedDescription)" }
        enqueueEffects()
    }

    func start(id: String, seconds: Int, label: String) async {
        guard identity != nil, !id.isEmpty, id.count <= 200, seconds > 0, seconds <= 86400, starts[id] == nil else { return }
        if let running = entries.first(where: { $0.id == id }), running.endsAt != nil, running.seconds() > 0 { return }
        let expected = generation, operation = UUID()
        starts[id] = operation; startingIDs.insert(id)
        defer { if starts[id] == operation { starts[id] = nil; startingIDs.remove(id) } }
        let granted = await requestAuthorization()
        guard generation == expected, identity != nil, starts[id] == operation, !Task.isCancelled else { return }
        let existing = entries.first { $0.id == id }
        if existing?.endsAt != nil && existing!.seconds() > 0 { return }
        let remaining = existing.map { $0.seconds() > 0 ? TimeInterval($0.seconds()) : TimeInterval(seconds) } ?? TimeInterval(seconds)
        var next = entries.filter { $0.id != id }
        next.append(KitchenTimerEntry(id: id, label: String(label.prefix(300)), duration: seconds, remaining: remaining, endsAt: Date().addingTimeInterval(remaining)))
        commit(next)
        if !granted { message = "Mitteilungen sind nicht erlaubt. Timer laufen weiter; Freigabe ist in den iPhone-Einstellungen möglich." }
    }

    func pause(id: String) {
        starts[id] = nil; startingIDs.remove(id)
        commit(entries.map { item in
            guard item.id == id else { return item }
            var copy = item; copy.remaining = TimeInterval(item.seconds()); copy.endsAt = nil; return copy
        })
    }
    func remove(id: String) { starts[id] = nil; startingIDs.remove(id); commit(entries.filter { $0.id != id }) }
    func refresh() { enqueueEffects() }
    func clear() {
        generation = UUID()
        starts = [:]; startingIDs = []
        if !namespace.isEmpty {
            // A tombstone survives a failed file deletion or a locked device.
            defaults.set(true, forKey: "kitchen-timers-cleared-\(namespace)")
            if systemEffects {
                let known = entries.map { prefix + OfflineSessionSnapshot.digest(namespace + $0.id) }
                center.removePendingNotificationRequests(withIdentifiers: known)
                center.removeDeliveredNotifications(withIdentifiers: known)
            }
            if let url = try? storageURL() { try? FileManager.default.removeItem(at: url) }
        }
        entries = []; identity = nil; namespace = ""; message = nil
        enqueueEffects()
    }
    private func storageURL() throws -> URL {
        let directory = try self.directory ?? FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
            .appendingPathComponent("KitchenTimers", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        var resourceURL = directory
        var values = URLResourceValues(); values.isExcludedFromBackup = true
        try? resourceURL.setResourceValues(values)
        return directory.appendingPathComponent(namespace + ".json")
    }
    private func commit(_ value: [KitchenTimerEntry]) {
        guard identity != nil else { return }
        do {
            try JSONEncoder().encode(value).write(to: storageURL(), options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
            defaults.removeObject(forKey: "kitchen-timers-cleared-\(namespace)")
            entries = value; message = nil
            enqueueEffects()
        } catch { message = "Timer konnte nicht gespeichert werden: \(error.localizedDescription)" }
    }
    private func enqueueEffects() {
        effectsGeneration = UUID()
        guard systemEffects else { return }
        let previous = effects, expected = effectsGeneration
        effects = Task { [weak self] in
            await previous?.value
            guard let self, self.effectsGeneration == expected else { return }
            await self.reconcile(expected: expected)
        }
    }
    private func reconcile(expected: UUID) async {
        let scheduled = await center.pendingNotificationRequests()
        guard effectsGeneration == expected else { return }
        center.removePendingNotificationRequests(withIdentifiers: scheduled.filter { $0.identifier.hasPrefix(prefix) }.map(\.identifier))
        let delivered = await center.deliveredNotifications()
        guard effectsGeneration == expected else { return }
        let wanted = Set(entries.map { prefix + OfflineSessionSnapshot.digest(namespace + $0.id) })
        center.removeDeliveredNotifications(withIdentifiers: delivered.filter {
            $0.request.identifier.hasPrefix(prefix) && !wanted.contains($0.request.identifier)
        }.map { $0.request.identifier })
        let running = entries.filter { $0.endsAt != nil && $0.seconds() > 0 }.sorted { $0.endsAt! < $1.endsAt! }
        for timer in running {
            guard effectsGeneration == expected else { return }
            let content = UNMutableNotificationContent()
            content.title = "Timer fertig"; content.body = timer.label; content.sound = .default
            let request = UNNotificationRequest(identifier: prefix + OfflineSessionSnapshot.digest(namespace + timer.id), content: content,
                trigger: UNTimeIntervalNotificationTrigger(timeInterval: TimeInterval(max(1, timer.seconds())), repeats: false))
            try? await center.add(request)
            guard effectsGeneration == expected else {
                center.removePendingNotificationRequests(withIdentifiers: [request.identifier])
                return
            }
        }
        guard effectsGeneration == expected else { return }
        let timer = running.first
        let key = timer.map { OfflineSessionSnapshot.digest(namespace + $0.id) }
        for activity in Activity<KitchenTimerAttributes>.activities where activity.attributes.timerID != key {
            await activity.end(nil, dismissalPolicy: .immediate)
        }
        guard effectsGeneration == expected, let timer, let key, let end = timer.endsAt,
              ActivityAuthorizationInfo().areActivitiesEnabled else { return }
        let content = ActivityContent(state: KitchenTimerAttributes.ContentState(label: timer.label, endsAt: end), staleDate: end)
        if let activity = Activity<KitchenTimerAttributes>.activities.first(where: { $0.attributes.timerID == key }) {
            await activity.update(content)
        } else {
            do { _ = try Activity.request(attributes: KitchenTimerAttributes(timerID: key), content: content, pushType: nil) }
            catch { message = "Die Sperrbildschirmanzeige ist gerade nicht verfügbar. Der Timer bleibt gespeichert." }
        }
    }
}
