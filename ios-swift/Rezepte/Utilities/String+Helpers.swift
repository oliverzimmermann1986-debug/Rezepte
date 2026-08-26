import Foundation

extension String {
    var nilIfEmpty: String? { isEmpty ? nil : self }
}

extension Notification.Name {
    static let recipesChanged = Notification.Name("recipesChanged")
}
