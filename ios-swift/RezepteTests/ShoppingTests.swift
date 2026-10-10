import Foundation
import XCTest
@testable import Rezepte

final class ShoppingInputTests: XCTestCase {
    func testDecimalCommasCompactUnitsAndBulletLists() throws {
        let items = try ShoppingInput.parse("- [ ] 500g Tomaten\n• 0,5 l Milch; 2 Eier, Brot\n1. ½ kg Mehl\n2x Dosen")
        XCTAssertEqual(items, [ShoppingAddItem(name: "Tomaten", amount: 500, unit: "g"),
            ShoppingAddItem(name: "Milch", amount: 0.5, unit: "l"), ShoppingAddItem(name: "Eier", amount: 2),
            ShoppingAddItem(name: "Brot"), ShoppingAddItem(name: "Mehl", amount: 0.5, unit: "kg"),
            ShoppingAddItem(name: "Dosen", amount: 2, unit: "Stück")])
    }

    func testAmbiguousRangesFractionsAndThousandsStayVerbatim() throws {
        let originals = ["1/2 Zitrone", "2-3 Eier", "1.000 g Mehl", "7up", "2 kg", "3,5% Milch"]
        XCTAssertEqual(try ShoppingInput.parse(originals.joined(separator: "\n")), originals.map { ShoppingAddItem(name: $0) })
        XCTAssertEqual(try ShoppingInput.parse("2 xylitol").first?.name, "xylitol")
    }

    func testValidationDoesNotSilentlyTruncateOrPartiallyAcceptInput() {
        XCTAssertThrowsError(try ShoppingInput.parse(Array(repeating: "Brot", count: 51).joined(separator: "\n")))
        XCTAssertThrowsError(try ShoppingInput.parse(String(repeating: "x", count: 10_001)))
        XCTAssertThrowsError(try ShoppingInput.parse("Brot\n" + String(repeating: "x", count: 201)))
        XCTAssertThrowsError(try ShoppingInput.parse(" \n - [ ] "))
        for value in ["0", "-1", "NaN", "Infinity", "1000001", "1e3"] {
            XCTAssertThrowsError(try ShoppingInput.amount(value))
        }
        XCTAssertEqual(try? ShoppingInput.amount("0,5"), 0.5)
        XCTAssertNil(try? ShoppingInput.amount(""))
    }
}

@MainActor
final class ShoppingSyncTests: XCTestCase {
    private final class Memory {
        var values: [String: Data] = [:]
        var failWrites = false
        var writes = 0
        var storage: ShoppingJournalStorage {
            ShoppingJournalStorage(read: { self.values[$0] }, write: { key, data in
                if self.failWrites { throw CocoaError(.fileWriteOutOfSpace) }
                self.values[key] = data
                self.writes += 1
            })
        }
        var documents: [ShoppingDocument] {
            values.filter { $0.key.contains("-household-") }.compactMap { try? JSONDecoder().decode(ShoppingDocument.self, from: $0.value) }
        }
    }

    private func row(_ id: Int = 1, checked: Bool = false) -> CartItem {
        CartItem(id: id, name: "Tomaten \(id)", amount: 500, unit: "g", checked: checked, category: "Gemüse", icon: "🍅",
            amountBase: 500, unitBase: "g", syncRevision: String(repeating: "a", count: 64),
            sourceContributions: [ShoppingContribution(recipeId: 42, recipeName: "Pasta", amount: 500, unit: "g")])
    }

    private func context(namespace: String = UUID().uuidString, identity: UUID = UUID(), items: [CartItem] = [],
                         current: @escaping @MainActor () -> Bool = { true },
                         sync: @escaping @MainActor (ShoppingSyncPayload) async throws -> ShoppingCartResponse = { _ in
                             throw URLError(.notConnectedToInternet)
                         }) -> ShoppingSyncContext {
        ShoppingSyncContext(identity: identity, namespace: namespace, writable: true, isCurrent: current,
            fetch: { ShoppingCartResponse(householdId: 7, items: items) }, synchronize: sync)
    }

    func testAtomicBulkWriteFailurePublishesNoPartialRows() async throws {
        let memory = Memory()
        let store = ShoppingSyncStore(storage: memory.storage)
        await store.activate(context: context())
        let before = memory.writes
        memory.failWrites = true
        XCTAssertThrowsError(try store.addMany([ShoppingAddItem(name: "Brot"), ShoppingAddItem(name: "Milch")]))
        XCTAssertTrue(store.items.isEmpty)
        XCTAssertEqual(store.pendingCount, 0)
        memory.failWrites = false
        try store.addMany([ShoppingAddItem(name: "Brot"), ShoppingAddItem(name: "Milch", amount: 0.5, unit: "l")])
        XCTAssertEqual(memory.writes, before + 1)
        XCTAssertEqual(store.items.count, 2)
        XCTAssertEqual(store.items.last?.amount, 0.5)
        XCTAssertEqual(store.pendingCount, 2)
    }

    func testInvalidBulkRowRejectsWholeBatch() async throws {
        let store = ShoppingSyncStore(storage: Memory().storage)
        await store.activate(context: context())
        XCTAssertThrowsError(try store.addMany([ShoppingAddItem(name: "Brot"), ShoppingAddItem(name: "", amount: 2)]))
        XCTAssertTrue(store.items.isEmpty)
        XCTAssertThrowsError(try store.addMany(Array(repeating: ShoppingAddItem(name: "Brot"), count: 51)))
    }

    func testFirstConnectionRequiredAndGuestCannotWrite() async {
        let store = ShoppingSyncStore(storage: Memory().storage)
        let offline = ShoppingSyncContext(identity: UUID(), namespace: UUID().uuidString, writable: true,
            isCurrent: { true }, fetch: { throw URLError(.notConnectedToInternet) },
            synchronize: { _ in XCTFail("No journal means no upload"); throw URLError(.notConnectedToInternet) })
        await store.activate(context: offline)
        XCTAssertFalse(store.isReady)
        XCTAssertThrowsError(try store.addMany([ShoppingAddItem(name: "Brot")]))
        let guest = ShoppingSyncContext(identity: UUID(), namespace: "guest", writable: false,
            isCurrent: { true }, fetch: { XCTFail("Guest must not fetch household data"); return ShoppingCartResponse(householdId: 7, items: []) },
            synchronize: { _ in throw URLError(.notConnectedToInternet) })
        await store.activate(context: guest)
        XCTAssertThrowsError(try store.addMany([ShoppingAddItem(name: "Brot")]))
    }

    func testOfflineRestartRetainsStableOperationsAndUndo() async throws {
        let memory = Memory()
        let namespace = UUID().uuidString
        let first = ShoppingSyncStore(storage: memory.storage)
        await first.activate(context: context(namespace: namespace, items: [row(checked: true)]))
        try first.clearChecked(first.items)
        let originalID = try XCTUnwrap(memory.documents.first?.operations.first?.wire.operationId)
        let offline = ShoppingSyncContext(identity: UUID(), namespace: namespace, writable: true,
            isCurrent: { true }, fetch: { throw URLError(.notConnectedToInternet) },
            synchronize: { payload in
                XCTAssertEqual(payload.operations.first?.operationId, originalID)
                throw URLError(.notConnectedToInternet)
            })
        let restarted = ShoppingSyncStore(storage: memory.storage)
        await restarted.activate(context: offline)
        XCTAssertTrue(restarted.isReady)
        XCTAssertTrue(restarted.items.isEmpty)
        XCTAssertEqual(restarted.pendingCount, 1)
        XCTAssertNotNil(restarted.undoLabel)
        try restarted.undoLatest()
        XCTAssertEqual(restarted.pendingCount, 2)
        XCTAssertEqual(restarted.items.first?.sourceContributions?.first?.amount, 500)
        XCTAssertEqual(restarted.items.first?.checked, true)
        let restore = try XCTUnwrap(memory.documents.first?.operations.last)
        XCTAssertEqual(restore.wire.targetOperationId, originalID)
        XCTAssertEqual(restore.wire.afterOperationId, originalID)
        XCTAssertLessThan(restore.localId, 0)
    }

    func testLostResponseRetryKeepsIdempotencyKey() async throws {
        let memory = Memory()
        let store = ShoppingSyncStore(storage: memory.storage)
        var attempts: [String] = []
        await store.activate(context: context(sync: { payload in
            let operation = try XCTUnwrap(payload.operations.first)
            attempts.append(operation.operationId)
            if attempts.count == 1 { throw URLError(.networkConnectionLost) }
            return ShoppingCartResponse(householdId: 7,
                items: [CartItem(id: 19, name: "Brot", amount: nil, unit: nil, checked: false, category: nil, icon: nil)],
                results: [ShoppingReceipt(operationId: operation.operationId, status: "applied", itemId: 19)])
        }))
        try store.addMany([ShoppingAddItem(name: "Brot")])
        do { try await store.refresh(); XCTFail("Lost response must remain pending") } catch {}
        XCTAssertEqual(store.pendingCount, 1)
        try await store.refresh()
        XCTAssertEqual(attempts.count, 2)
        XCTAssertEqual(attempts.first, attempts.last)
        XCTAssertEqual(store.pendingCount, 0)
        XCTAssertEqual(store.items.map(\.id), [19])
    }

    func testReceiptPersistenceFailureRetriesOriginalOperation() async throws {
        let memory = Memory()
        let store = ShoppingSyncStore(storage: memory.storage)
        var sent: [String] = []
        await store.activate(context: context(sync: { payload in
            let operation = try XCTUnwrap(payload.operations.first)
            sent.append(operation.operationId)
            return ShoppingCartResponse(householdId: 7, items: [],
                results: [ShoppingReceipt(operationId: operation.operationId, status: "applied", itemId: 29)])
        }))
        try store.addMany([ShoppingAddItem(name: "Brot")])
        memory.failWrites = true
        do { try await store.refresh(); XCTFail("Disk failure must not clear pending work") } catch {}
        XCTAssertEqual(store.pendingCount, 1)
        memory.failWrites = false
        try await store.refresh()
        XCTAssertEqual(sent.first, sent.last)
        XCTAssertEqual(store.pendingCount, 0)
    }

    func testIncompleteReceiptsPreserveEveryPendingOperation() async throws {
        let store = ShoppingSyncStore(storage: Memory().storage)
        await store.activate(context: context(sync: { _ in ShoppingCartResponse(householdId: 7, items: [], results: []) }))
        try store.addMany([ShoppingAddItem(name: "Brot"), ShoppingAddItem(name: "Milch")])
        do { try await store.refresh(); XCTFail("Missing receipts must fail") } catch {}
        XCTAssertEqual(store.pendingCount, 2)
        XCTAssertEqual(store.items.count, 2)
    }

    func testRejectedParentAndDescendantCannotBecomePhantomEdits() async throws {
        let original = row()
        let store = ShoppingSyncStore(storage: Memory().storage)
        await store.activate(context: context(items: [original], sync: { payload in
            ShoppingCartResponse(householdId: 7, items: [original], results: payload.operations.map {
                ShoppingReceipt(operationId: $0.operationId, status: "conflict", reason: "revision_changed")
            })
        }))
        try store.change(1, kind: "check")
        try store.change(1, kind: "delete")
        XCTAssertTrue(store.items.isEmpty)
        try await store.refresh()
        XCTAssertEqual(store.conflictCount, 2)
        XCTAssertEqual(store.items, [original])
        try store.discardConflicts()
        XCTAssertEqual(store.pendingCount, 0)
        XCTAssertEqual(store.items, [original])
        XCTAssertNil(store.undoLabel)
    }

    func testFullOfflineClearReservesIndependentUndoCapacity() async throws {
        let store = ShoppingSyncStore(storage: Memory().storage)
        await store.activate(context: context(items: (1...500).map { row($0, checked: true) }))
        try store.clearChecked(store.items)
        XCTAssertEqual(store.pendingCount, 500)
        XCTAssertThrowsError(try store.addMany([ShoppingAddItem(name: "Über Kapazität")]))
        try store.undoLatest()
        XCTAssertEqual(store.pendingCount, 1000)
        XCTAssertEqual(store.items.count, 500)
    }

    func testOnlyTwentyUndoGroupsAreRetained() async throws {
        let store = ShoppingSyncStore(storage: Memory().storage)
        await store.activate(context: context(items: (1...21).map { row($0) }))
        for id in 1...21 { try store.change(id, kind: "delete") }
        for _ in 0..<20 { try store.undoLatest() }
        XCTAssertNil(store.undoLabel)
        XCTAssertEqual(store.items.count, 20)
        XCTAssertFalse(store.items.contains { $0.name == "Tomaten 1" })
    }

    func testStaleCheckedSelectionCannotDeleteChangedRows() async throws {
        let store = ShoppingSyncStore(storage: Memory().storage)
        await store.activate(context: context(items: [row(checked: true)]))
        let selection = store.items
        try store.change(1, kind: "check")
        XCTAssertThrowsError(try store.clearChecked(selection))
        XCTAssertEqual(store.items.first?.checked, false)
        XCTAssertEqual(store.pendingCount, 1)
    }

    func testHouseholdConflictSwitchesJournalWithoutReplayingIt() async throws {
        let memory = Memory()
        let store = ShoppingSyncStore(storage: memory.storage)
        var household = 7
        var uploads: [Int] = []
        let context = ShoppingSyncContext(identity: UUID(), namespace: UUID().uuidString, writable: true,
            isCurrent: { true }, fetch: { ShoppingCartResponse(householdId: household, items: []) },
            synchronize: { payload in
                uploads.append(payload.householdId)
                household = 9
                throw APIError.server(409, "Haushalt geändert")
            })
        await store.activate(context: context)
        try store.addMany([ShoppingAddItem(name: "Alter Haushalt")])
        try await store.refresh()
        XCTAssertEqual(store.householdID, 9)
        XCTAssertEqual(store.pendingCount, 0)
        XCTAssertEqual(uploads, [7])
        XCTAssertEqual(memory.documents.first(where: { $0.householdId == 7 })?.operations.count, 1)
        XCTAssertTrue(store.items.isEmpty)
    }

    func testHouseholdInvalidationSurvivesDiskFailureAndOfflineRestart() async throws {
        let memory = Memory()
        let namespace = UUID().uuidString
        let suite = "ShoppingTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let first = ShoppingSyncStore(storage: memory.storage, defaults: defaults)
        await first.activate(context: context(namespace: namespace, items: [row()]))
        try first.addMany([ShoppingAddItem(name: "Alt")])
        memory.failWrites = true
        XCTAssertThrowsError(try first.invalidateHousehold())
        XCTAssertFalse(first.isReady)
        let offline = ShoppingSyncContext(identity: UUID(), namespace: namespace, writable: true,
            isCurrent: { true }, fetch: { throw URLError(.notConnectedToInternet) }, synchronize: { _ in
                XCTFail("Old household queue must never be uploaded before household discovery")
                throw URLError(.notConnectedToInternet)
            })
        let restarted = ShoppingSyncStore(storage: memory.storage, defaults: defaults)
        await restarted.activate(context: offline)
        XCTAssertFalse(restarted.isReady)
        XCTAssertTrue(restarted.items.isEmpty)
        XCTAssertEqual(memory.documents.first?.operations.count, 1)
    }

    func testLateNetworkResultCannotPublishIntoNewSession() async throws {
        let store = ShoppingSyncStore(storage: Memory().storage)
        var current = true
        await store.activate(context: context(current: { current }, sync: { _ in
            current = false
            return ShoppingCartResponse(householdId: 7, items: [])
        }))
        try store.addMany([ShoppingAddItem(name: "Alt")])
        do { try await store.refresh(); XCTFail("Old identity response must be rejected") } catch {}
        XCTAssertEqual(store.pendingCount, 1)
        XCTAssertThrowsError(try store.addMany([ShoppingAddItem(name: "Unzulässig")]))
        store.deactivate()
        XCTAssertTrue(store.items.isEmpty)
    }
}

final class ShoppingAPITests: XCTestCase {
    func testRestoreWirePayloadDoesNotSendClientSnapshot() async throws {
        let api = APIClient(session: MockURLProtocol.makeSession())
        try await api.configure(server: "https://example.de", token: "token")
        MockURLProtocol.respond(json: #"{"household_id":7,"items":[],"results":[]}"#)
        let operation = ShoppingWireOperation(kind: "restore", targetOperationId: "original-delete-operation",
            afterOperationId: "original-delete-operation")
        _ = try await api.shoppingSynchronize(ShoppingSyncPayload(householdId: 7, operations: [operation]))
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/cart/sync")
        XCTAssertEqual(MockURLProtocol.lastMethod(), "POST")
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: MockURLProtocol.lastBody()) as? [String: Any])
        XCTAssertEqual(body["household_id"] as? Int, 7)
        let operations = try XCTUnwrap(body["operations"] as? [[String: Any]])
        XCTAssertEqual(operations.first?["target_operation_id"] as? String, "original-delete-operation")
        XCTAssertNil(operations.first?["snapshot"])
        XCTAssertNil(operations.first?["local_id"])
    }

    func testProvenancePreservesHistoricalQuantitiesAndUnknownSources() async throws {
        let api = APIClient(session: MockURLProtocol.makeSession())
        try await api.configure(server: "https://example.de", token: "token")
        MockURLProtocol.respond(json: #"{"household_id":7,"items":[{"id":1,"name":"Tomaten","amount":500,"unit":"g","checked":false,"sync_revision":"revision","source_contributions":[{"recipe_id":42,"recipe_name":"Pasta","amount":300,"unit":"g"},{"recipe_id":null,"recipe_name":"Frühere Herkunft","amount":null,"unit":null}]}]}"#)
        let result = try await api.shoppingCart()
        XCTAssertEqual(result.items.first?.syncRevision, "revision")
        XCTAssertEqual(result.items.first?.sourceContributions?.first?.amount, 300)
        XCTAssertNil(result.items.first?.sourceContributions?.last?.recipeId)
        XCTAssertEqual(result.items.first?.sourceContributions?.last?.quantityText, "Mengenanteil unbekannt")
    }
}
