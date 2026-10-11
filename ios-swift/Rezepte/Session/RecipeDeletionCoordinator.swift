import Combine
import Foundation

extension Notification.Name {
    static let recipeDeleted = Notification.Name("KitchenRecipeDeleted")
}

struct RecipeDeletionTarget: Identifiable {
    let id: Int
    let name: String
    let visibility: String?
    let canEdit: Bool?

    init(id: Int, name: String, visibility: String?, canEdit: Bool?) {
        self.id = id; self.name = name; self.visibility = visibility; self.canEdit = canEdit
    }

    init(_ recipe: Recipe) {
        self.init(id: recipe.id, name: recipe.name, visibility: recipe.visibility, canEdit: recipe.canEdit)
    }

    init(_ recipe: RecipeSummary) {
        self.init(id: recipe.id, name: recipe.name, visibility: recipe.visibility, canEdit: recipe.canEdit)
    }

    var confirmationMessage: String {
        let scope = visibility == "private" ? "für deinen Haushalt" : "für alle Haushalte"
        return "„\(name)“ wird \(scope) in den Papierkorb verschoben und kann innerhalb von 30 Tagen wiederhergestellt werden. Der Offline-Download auf diesem Gerät wird ebenfalls entfernt."
    }
}

/// Keep the server acknowledgement separate from local cleanup: a failed disk
/// write must not invite a second destructive request after deletion succeeded.
struct RecipeDeletionContext {
    let isCurrent: () -> Bool
    let send: (Int) async throws -> Bool
    let removeOffline: (Int) throws -> Void
    let notifyDeleted: (Int) -> Void
    let handleError: (Error) -> Void
}

@MainActor
final class RecipeDeletionCoordinator: ObservableObject {
    @Published private(set) var isDeleting = false

    static func isAllowed(_ target: RecipeDeletionTarget, role: AccountRole,
                          readOnly: Bool, isOffline: Bool) -> Bool {
        guard !readOnly, !isOffline, role != .guest, target.id > 0 else { return false }
        let admin = role == .admin
        return (target.canEdit ?? admin) && (target.visibility == "private" || admin)
    }

    func canDelete(_ target: RecipeDeletionTarget, session: SessionStore) -> Bool {
        guard case .signedIn = session.state else { return false }
        return Self.isAllowed(target, role: session.role, readOnly: session.readOnly,
                              isOffline: session.isOfflineSession)
    }

    func delete(_ target: RecipeDeletionTarget, session: SessionStore, offline: OfflineRecipeStore) async -> Bool {
        guard canDelete(target, session: session) else { return false }
        let identity = session.identity
        let namespace = OfflineRecipeStore.namespace(session)
        return await delete(id: target.id, context: RecipeDeletionContext(
            isCurrent: { session.identity == identity && !session.readOnly },
            send: { id in
                let result = try await session.api.deleteRecipe(id: id)
                return result.ok == true
            },
            removeOffline: { id in
                if let namespace { try offline.recordDeletedRecipe(id: id, namespace: namespace) }
            },
            notifyDeleted: { id in
                NotificationCenter.default.post(name: .recipeDeleted, object: id)
                NotificationCenter.default.post(name: .recipesChanged, object: id)
            },
            handleError: { session.handle($0) }
        ))
    }

    func delete(id: Int, context: RecipeDeletionContext) async -> Bool {
        guard id > 0, !isDeleting, context.isCurrent(), !Task.isCancelled else { return false }
        isDeleting = true
        defer { isDeleting = false }
        do {
            let acknowledged = try await context.send(id)
            guard acknowledged else { throw APIError.invalidResponse("Rezept löschen") }
            // The cleanup is bound to the original account, even if its screen
            // disappeared while the server was confirming the deletion.
            do { try context.removeOffline(id) }
            catch { if context.isCurrent(), !Task.isCancelled { context.handleError(error) } }
            guard context.isCurrent(), !Task.isCancelled else { return false }
            context.notifyDeleted(id)
            return true
        } catch {
            guard context.isCurrent(), !Task.isCancelled else { return false }
            context.handleError(error)
            return false
        }
    }
}
