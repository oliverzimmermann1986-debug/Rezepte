import Foundation

enum SharedImportQueue {
    static let appGroup = "group.de.mausbaeren.rezepte"
    private static let key = "shared-import-urls"

    static func enqueue(_ url: String) {
        guard let defaults = UserDefaults(suiteName: appGroup) else { return }
        var items = defaults.stringArray(forKey: key) ?? []
        guard !items.contains(url) else { return }
        items.append(url)
        defaults.set(items, forKey: key)
    }

    static func all() -> [String] {
        UserDefaults(suiteName: appGroup)?.stringArray(forKey: key) ?? []
    }

    static func remove(_ url: String) {
        guard let defaults = UserDefaults(suiteName: appGroup) else { return }
        defaults.set(all().filter { $0 != url }, forKey: key)
    }
}
