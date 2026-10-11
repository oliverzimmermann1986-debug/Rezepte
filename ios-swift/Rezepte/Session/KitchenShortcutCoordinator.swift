import Combine
import Foundation

enum KitchenShortcutError: LocalizedError {
    case signInRequired, unavailable, singleItemRequired
    var errorDescription: String? {
        switch self {
        case .signInRequired: return "Bitte melde dich zuerst in Quellenküche mit deinem Konto an und starte den Kurzbefehl erneut."
        case .unavailable: return "Quellenküche ist noch nicht bereit. Öffne die App und versuche es erneut."
        case .singleItemRequired: return "Bitte nenne genau einen Artikel, zum Beispiel 1 Liter Milch."
        }
    }
}

struct KitchenShortcutRoute: Identifiable {
    enum Destination { case shopping, today, recipe(Int) }
    let id = UUID()
    let identity: UUID
    let destination: Destination
}

/// Intents run in the foreground and use the very same journal as the shopping UI.
/// No second session, token copy or competing offline writer is created.
@MainActor
final class KitchenShortcutCoordinator: ObservableObject {
    static let shared = KitchenShortcutCoordinator()
    @Published private(set) var route: KitchenShortcutRoute?
    private weak var session: SessionStore?
    private weak var shopping: ShoppingSyncStore?
    private var shoppingIdentity: UUID?

    func bind(session: SessionStore) { self.session = session }

    func bind(shopping: ShoppingSyncStore, session: SessionStore) {
        self.session = session
        self.shopping = shopping
        shoppingIdentity = session.identity
    }

    func clear() { shopping = nil; shoppingIdentity = nil; route = nil }
    func consumeRoute(id: UUID) { if route?.id == id { route = nil } }

    static func shoppingEntry(_ text: String) throws -> ShoppingAddItem {
        let entries = try ShoppingInput.parse(text)
        guard entries.count == 1 else { throw KitchenShortcutError.singleItemRequired }
        return entries[0]
    }

    static func localDate(_ now: Date, timeZone: TimeZone) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = timeZone
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: now)
    }

    static func todayEntries(in week: MealWeek, now: Date = Date(), timeZone: TimeZone = .current) -> [MealEntry] {
        week.days.first(where: { $0.date == localDate(now, timeZone: timeZone) })?.items ?? []
    }

    func addShopping(_ text: String) async throws -> Bool {
        let entry = try Self.shoppingEntry(text)
        let active = try await readySession()
        let identity = active.identity
        for _ in 0..<80 {
            try Task.checkCancellation()
            guard active.identity == identity, !active.readOnly, !active.isEndingSession else { throw APIError.sessionChanged }
            if shoppingIdentity == identity, let shopping, let householdID = shopping.householdID {
                try shopping.addMany([entry], expectedHouseholdID: householdID)
                route = KitchenShortcutRoute(identity: identity, destination: .shopping)
                // The journal has already persisted the operation. Network failure
                // must report a queued success, never invite a duplicate retry.
                do { try await shopping.refresh() } catch { return false }
                guard active.identity == identity else { return false }
                return shopping.pendingCount == 0
            }
            try await Task.sleep(for: .milliseconds(250))
        }
        throw KitchenShortcutError.unavailable
    }

    func openToday(now: Date = Date(), timeZone: TimeZone = .current) async throws -> Int {
        let active = try await readySession()
        let identity = active.identity
        // Request the week containing the device's date. Server and device may
        // already be in different weeks around local midnight or while travelling.
        let week = try await active.api.mealWeek(start: Self.localDate(now, timeZone: timeZone))
        guard active.identity == identity, !active.readOnly else { throw APIError.sessionChanged }
        let entries = Self.todayEntries(in: week, now: now, timeZone: timeZone)
        route = KitchenShortcutRoute(identity: identity, destination: entries.count == 1 ? .recipe(entries[0].recipeId) : .today)
        return entries.count
    }

    private func readySession() async throws -> SessionStore {
        for _ in 0..<80 {
            try Task.checkCancellation()
            if let session {
                switch session.state {
                case .signedIn:
                    guard session.userID != nil, !session.readOnly, !session.isEndingSession else { throw KitchenShortcutError.signInRequired }
                    return session
                case .signedOut: throw KitchenShortcutError.signInRequired
                case .checking: break
                }
            }
            try await Task.sleep(for: .milliseconds(250))
        }
        throw KitchenShortcutError.unavailable
    }
}
