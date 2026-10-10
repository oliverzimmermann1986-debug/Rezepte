import Foundation

struct ShoppingContribution: Codable, Hashable {
    let recipeId: Int?
    let recipeName: String
    let amount: Double?
    let unit: String?

    var quantityText: String {
        guard let amount else { return "Mengenanteil unbekannt" }
        return [amount.formatted(.number.precision(.fractionLength(0...3))), unit]
            .compactMap { $0 }.joined(separator: " ")
    }
}

struct ShoppingAddItem: Codable, Equatable {
    var name: String
    var amount: Double? = nil
    var unit: String? = nil
    var category: String? = nil
}

/// Only these fields leave the device. Local undo snapshots are never trusted
/// by the backend; a restore refers to its original server deletion receipt.
struct ShoppingWireOperation: Codable {
    var operationId: String = UUID().uuidString
    var kind: String
    var name: String? = nil
    var amount: Double? = nil
    var unit: String? = nil
    var category: String? = nil
    var itemId: Int? = nil
    var targetOperationId: String? = nil
    var afterOperationId: String? = nil
    var expectedChecked: Bool? = nil
    var expectedRevision: String? = nil
    var checked: Bool? = nil
}

struct ShoppingSyncPayload: Encodable {
    let householdId: Int
    let operations: [ShoppingWireOperation]
}

struct ShoppingReceipt: Codable {
    let operationId: String
    let status: String
    var itemId: Int? = nil
    var reason: String? = nil
}

struct ShoppingCartResponse: Codable {
    let householdId: Int
    let items: [CartItem]
    var results: [ShoppingReceipt]? = nil
}

struct ShoppingLocalOperation: Codable {
    var wire: ShoppingWireOperation
    var localId: Int
    var snapshot: CartItem? = nil
    var conflict: String? = nil
    var label: String? = nil
}

struct ShoppingUndoDeletion: Codable {
    let operationId: String
    let snapshot: CartItem
}

struct ShoppingUndoGroup: Codable {
    let id: String
    let label: String
    var deletions: [ShoppingUndoDeletion]
}

struct ShoppingBinding: Codable {
    let itemId: Int
    let operationId: String
}

struct ShoppingDocument: Codable {
    var version = 1
    let householdId: Int
    var items: [CartItem] = []
    var operations: [ShoppingLocalOperation] = []
    var bindings: [String: ShoppingBinding] = [:]
    var undo: [ShoppingUndoGroup] = []

    var projectedItems: [CartItem] {
        var result = items
        var rejected = Set(operations.filter { $0.conflict != nil }.map { $0.wire.operationId })
        for operation in operations {
            let wire = operation.wire
            if operation.conflict != nil || wire.targetOperationId.map(rejected.contains) == true
                || wire.afterOperationId.map(rejected.contains) == true {
                rejected.insert(wire.operationId)
                continue
            }
            let target = bindings[String(operation.localId)]?.itemId ?? operation.localId
            switch wire.kind {
            case "add":
                result.append(CartItem(id: operation.localId, name: wire.name ?? "", amount: wire.amount,
                    unit: wire.unit, checked: false, category: wire.category, icon: nil))
            case "restore":
                if let snapshot = operation.snapshot {
                    result.append(CartItem(id: operation.localId, name: snapshot.name, amount: snapshot.amount,
                        unit: snapshot.unit, checked: snapshot.checked, category: snapshot.category, icon: snapshot.icon,
                        amountBase: snapshot.amountBase, unitBase: snapshot.unitBase,
                        syncRevision: snapshot.syncRevision, sourceContributions: snapshot.sourceContributions))
                }
            case "check":
                if let index = result.firstIndex(where: { $0.id == target }), let checked = wire.checked {
                    result[index].checked = checked
                }
            case "delete": result.removeAll { $0.id == target }
            default: break
            }
        }
        return result
    }
}
