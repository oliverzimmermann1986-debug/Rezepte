import Foundation
import UserNotifications

enum CookingTimerNotifications {
    static func identifier(account: OfflineAccount, identity: String) -> String {
        "cooking-\(account.id)-\(identity.prefix(36))-\(OfflineAccount.digest(identity))"
    }

    static func schedule(account: OfflineAccount, identity: String, label: String, deadline: Date, store: OfflineStore) async -> Bool {
        let center = UNUserNotificationCenter.current()
        do {
            let allowed = try await center.requestAuthorization(options: [.alert, .sound])
            guard allowed else { return false }
            guard let saved = try store.read(PersistentCookingTimer.self, key: "timer-\(identity)", account: account),
                  saved.deadline == deadline else { return false }
            let content = UNMutableNotificationContent()
            content.title = "Küchentimer fertig"
            content.body = label
            content.sound = .default
            let trigger = UNTimeIntervalNotificationTrigger(
                timeInterval: max(1, deadline.timeIntervalSinceNow), repeats: false
            )
            try await center.add(UNNotificationRequest(
                identifier: identifier(account: account, identity: identity), content: content, trigger: trigger
            ))
            guard let current = try store.read(PersistentCookingTimer.self, key: "timer-\(identity)", account: account),
                  current.deadline == deadline else {
                cancel(account: account, identity: identity)
                return false
            }
            return true
        } catch { return false }
    }

    static func cancel(account: OfflineAccount, identity: String) {
        let id = identifier(account: account, identity: identity)
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(withIdentifiers: [id])
        center.removeDeliveredNotifications(withIdentifiers: [id])
    }

    static func cancel(account: OfflineAccount) {
        cancel(prefix: "cooking-\(account.id)-")
    }

    static func cancel(account: OfflineAccount, runID: String) {
        cancel(prefix: "cooking-\(account.id)-\(runID)-")
    }

    private static func cancel(prefix: String) {
        let center = UNUserNotificationCenter.current()
        center.getPendingNotificationRequests { requests in
            center.removePendingNotificationRequests(withIdentifiers: requests.map(\.identifier)
                .filter { $0.hasPrefix(prefix) })
        }
        center.getDeliveredNotifications { notifications in
            center.removeDeliveredNotifications(withIdentifiers: notifications.map(\.request.identifier)
                .filter { $0.hasPrefix(prefix) })
        }
    }
}
