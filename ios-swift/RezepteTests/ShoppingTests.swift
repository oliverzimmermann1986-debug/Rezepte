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
        var failErases = false
        var writes = 0
        var storage: ShoppingJournalStorage {
            ShoppingJournalStorage(read: { self.values[$0] }, write: { key, data in
                if self.failWrites { throw CocoaError(.fileWriteOutOfSpace) }
                self.values[key] = data
                self.writes += 1
            }, eraseNamespace: { digest in
                if self.failErases { throw CocoaError(.fileWriteNoPermission) }
                self.values = self.values.filter { !$0.key.hasPrefix(digest + "-") }
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
        XCTAssertEqual(store.waitingCount, 0)
        XCTAssertEqual(store.items, [original])
        try store.discardConflicts()
        XCTAssertEqual(store.pendingCount, 0)
        XCTAssertEqual(store.items, [original])
        XCTAssertNil(store.undoLabel)
    }

    func testConflictsAcrossBatchBoundaryDoNotRetryDescendantsOrDiscardUnrelatedWork() async throws {
        let memory = Memory()
        let store = ShoppingSyncStore(storage: memory.storage)
        let identity = UUID()
        let original = row()
        var calls = 0
        var acceptedItems = [original]
        var independentID: String?
        await store.activate(context: context(identity: identity, items: [original], sync: { payload in
            calls += 1
            if calls == 1 {
                XCTAssertEqual(payload.operations.count, 50)
                let receipts = payload.operations.enumerated().map { index, operation -> ShoppingReceipt in
                    if index == 0 {
                        return ShoppingReceipt(operationId: operation.operationId, status: "conflict", reason: "revision_changed")
                    }
                    acceptedItems.append(CartItem(id: 100 + index, name: operation.name ?? "", amount: nil,
                        unit: nil, checked: false, category: nil, icon: nil))
                    return ShoppingReceipt(operationId: operation.operationId, status: "applied", itemId: 100 + index)
                }
                return ShoppingCartResponse(householdId: 7, items: acceptedItems, results: receipts)
            }
            // The delete and restore depend on the rejected check. Only the
            // unrelated new item may be transmitted in the following batch.
            XCTAssertEqual(payload.operations.count, 1)
            let operation = try XCTUnwrap(payload.operations.first)
            XCTAssertEqual(operation.name, "Unabhängiger Artikel")
            if calls == 2 {
                independentID = operation.operationId
                throw URLError(.networkConnectionLost)
            }
            XCTAssertEqual(operation.operationId, independentID)
            acceptedItems.append(CartItem(id: 999, name: operation.name ?? "", amount: nil,
                unit: nil, checked: false, category: nil, icon: nil))
            return ShoppingCartResponse(householdId: 7, items: acceptedItems,
                results: [ShoppingReceipt(operationId: operation.operationId, status: "applied", itemId: 999)])
        }))
        try store.change(1, kind: "check")
        try store.addMany((1...49).map { ShoppingAddItem(name: "Artikel \($0)") })
        try store.change(1, kind: "delete")
        try store.undoLatest()
        try store.addMany([ShoppingAddItem(name: "Unabhängiger Artikel")])
        XCTAssertEqual(store.waitingCount, 53)
        do { try await store.refresh(); XCTFail("The unrelated item's lost response must stay pending") } catch {}
        XCTAssertEqual(calls, 2)
        XCTAssertEqual(store.pendingCount, 4)
        XCTAssertEqual(store.waitingCount, 1)
        XCTAssertEqual(store.conflictCount, 3)
        XCTAssertEqual(store.conflictOperationIDs.count, 3)
        XCTAssertEqual(store.items.first, original)
        XCTAssertThrowsError(try store.change(1, kind: "check"))

        let captured = store.conflictOperationIDs
        XCTAssertThrowsError(try store.discardConflicts(operationIDs: captured.union([UUID().uuidString])))
        XCTAssertThrowsError(try store.discardConflicts(operationIDs: captured, expectedHouseholdID: 9))
        XCTAssertThrowsError(try store.discardConflicts(operationIDs: captured, expectedIdentity: UUID()))
        memory.failWrites = true
        XCTAssertThrowsError(try store.discardConflicts(operationIDs: captured))
        XCTAssertEqual(store.pendingCount, 4)
        memory.failWrites = false
        try store.discardConflicts(operationIDs: captured, expectedHouseholdID: 7, expectedIdentity: identity)
        XCTAssertEqual(store.pendingCount, 1)
        XCTAssertEqual(store.waitingCount, 1)
        XCTAssertEqual(store.conflictCount, 0)
        XCTAssertEqual(memory.documents.first?.operations.first?.wire.operationId, independentID)
        try await store.refresh()
        XCTAssertEqual(calls, 3)
        XCTAssertEqual(store.pendingCount, 0)
        XCTAssertEqual(store.items.count, 51)
    }

    func testUndoRequiresCapturedGroupHouseholdAndAccountWithoutLosingEarlierHistory() async throws {
        let memory = Memory()
        let store = ShoppingSyncStore(storage: memory.storage)
        let identity = UUID()
        await store.activate(context: context(identity: identity, items: [row(1), row(2)]))
        try store.change(1, kind: "delete")
        let firstGroup = try XCTUnwrap(store.undoID)
        try store.change(2, kind: "delete")
        let secondGroup = try XCTUnwrap(store.undoID)
        try store.undoLatest(groupID: firstGroup, expectedHouseholdID: 7, expectedIdentity: identity)
        XCTAssertTrue(store.items.isEmpty, "A stale button must not undo a different, newer deletion")
        XCTAssertEqual(store.undoID, secondGroup)
        XCTAssertThrowsError(try store.undoLatest(groupID: secondGroup, expectedHouseholdID: 9, expectedIdentity: identity))
        XCTAssertThrowsError(try store.undoLatest(groupID: secondGroup, expectedHouseholdID: 7, expectedIdentity: UUID()))
        try store.undoLatest(groupID: secondGroup, expectedHouseholdID: 7, expectedIdentity: identity)
        XCTAssertEqual(store.items.map(\.name), ["Tomaten 2"])
        XCTAssertEqual(store.undoID, firstGroup)
        try store.undoLatest(groupID: firstGroup, expectedHouseholdID: 7, expectedIdentity: identity)
        XCTAssertEqual(Set(store.items.map(\.name)), ["Tomaten 1", "Tomaten 2"])
        XCTAssertNil(store.undoID)

        store.deactivate()
        XCTAssertNil(store.undoLabel)
        XCTAssertEqual(store.waitingCount, 0)
        XCTAssertTrue(store.conflictOperationIDs.isEmpty)
        await store.activate(context: context(items: [row(9)]))
        XCTAssertThrowsError(try store.undoLatest(groupID: firstGroup, expectedHouseholdID: 7, expectedIdentity: identity))
        XCTAssertEqual(store.items.map(\.id), [9])
        XCTAssertEqual(store.pendingCount, 0)
    }

    func testAmountEditingExplainsPendingWorkAndConflictsUntilResolved() async throws {
        let original = row()
        let store = ShoppingSyncStore(storage: Memory().storage)
        await store.activate(context: context(items: [original], sync: { payload in
            ShoppingCartResponse(householdId: 7, items: [original], results: payload.operations.map {
                ShoppingReceipt(operationId: $0.operationId, status: "conflict", reason: "revision_changed")
            })
        }))
        XCTAssertNil(store.amountEditingUnavailableReason(for: original))
        try store.change(1, kind: "check")
        XCTAssertEqual(store.amountEditingUnavailableReason(for: original), "Menge erst nach dem Abgleich bearbeitbar.")
        try await store.refresh()
        XCTAssertEqual(store.waitingCount, 0)
        XCTAssertEqual(store.amountEditingUnavailableReason(for: original), "Menge erst nach Klärung der Konflikte bearbeitbar.")
        try store.discardConflicts()
        XCTAssertNil(store.amountEditingUnavailableReason(for: original))
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

    func testAccountErasureRemovesFormerHouseholdsAndPreservesOtherAccountsAndServers() async throws {
        let memory = Memory()
        let suite = "ShoppingTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let own = "https://one.example/\u{0}12"
        let otherAccount = "https://one.example/\u{0}13"
        let otherServer = "https://two.example/\u{0}12"
        let store = ShoppingSyncStore(storage: memory.storage, defaults: defaults)
        await store.activate(context: context(namespace: own, items: [row()]))
        try store.addMany([ShoppingAddItem(name: "Früherer Haushalt")])
        try store.invalidateHousehold()
        await store.activate(context: ShoppingSyncContext(identity: UUID(), namespace: own, writable: true,
            isCurrent: { true }, fetch: { ShoppingCartResponse(householdId: 9, items: []) },
            synchronize: { _ in XCTFail("Former household must not upload"); throw URLError(.notConnectedToInternet) }))
        try store.addMany([ShoppingAddItem(name: "Aktueller Haushalt")])
        XCTAssertEqual(Set(memory.documents.map(\.householdId)), [7, 9])
        let ownKeys = Set(memory.values.keys)
        await store.activate(context: context(namespace: otherAccount, items: [row(2)]))
        await store.activate(context: context(namespace: otherServer, items: [row(3)]))
        let foreign = memory.values.filter { !ownKeys.contains($0.key) }
        XCTAssertFalse(foreign.isEmpty)
        try store.eraseAccountData(namespace: own)
        XCTAssertEqual(memory.values, foreign)
        XCTAssertEqual(store.items.map(\.id), [3], "Erasing a different namespace preserves the active account")
        store.deactivate()
        XCTAssertEqual(memory.values, foreign, "Ordinary sign-out preserves journals")
        await store.activate(context: context(namespace: otherAccount, items: [row(2)]))
        XCTAssertTrue(store.isReady)
        XCTAssertEqual(store.items.map(\.id), [2])
    }

    func testAccountErasureFailureBlocksReadsAndUploadsAfterRestartThenRetriesCleanup() async throws {
        let memory = Memory()
        let namespace = UUID().uuidString
        let suite = "ShoppingTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = ShoppingSyncStore(storage: memory.storage, defaults: defaults)
        await store.activate(context: context(namespace: namespace, items: [row()]))
        try store.addMany([ShoppingAddItem(name: "Privat")])
        let previous = memory.values
        memory.failErases = true
        XCTAssertThrowsError(try store.eraseAccountData(namespace: namespace)) { error in
            XCTAssertTrue(error.localizedDescription.contains("gesperrt"))
            XCTAssertFalse(error.localizedDescription.contains("NSCocoaErrorDomain"))
        }
        XCTAssertFalse(store.isReady)
        XCTAssertTrue(store.items.isEmpty)
        XCTAssertEqual(memory.values, previous)
        let protectedStorage = ShoppingJournalStorage(read: { _ in
            XCTFail("A deleted account's journal must never be read"); return nil
        }, write: { _, _ in XCTFail("A deleted account's journal must never be written") },
            eraseNamespace: memory.storage.eraseNamespace)
        let restarted = ShoppingSyncStore(storage: protectedStorage,
            defaults: try XCTUnwrap(UserDefaults(suiteName: suite)))
        let blocked = ShoppingSyncContext(identity: UUID(), namespace: namespace, writable: true,
            isCurrent: { true }, fetch: { XCTFail("Deleted namespace must not fetch"); throw URLError(.notConnectedToInternet) },
            synchronize: { _ in XCTFail("Deleted namespace must not upload"); throw URLError(.notConnectedToInternet) })
        await restarted.activate(context: blocked)
        XCTAssertFalse(restarted.isReady)
        XCTAssertTrue(restarted.errorMessage?.contains("gesperrt") == true)
        XCTAssertThrowsError(try restarted.addMany([ShoppingAddItem(name: "Unzulässig")]))
        memory.failErases = false
        await restarted.activate(context: blocked)
        XCTAssertTrue(memory.values.isEmpty)
        XCTAssertFalse(restarted.isReady)
        XCTAssertTrue(restarted.items.isEmpty)
    }

    func testSuccessfulLateSyncCannotRecreateErasedAccountJournal() async throws {
        let memory = Memory()
        let suite = "ShoppingTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = ShoppingSyncStore(storage: memory.storage, defaults: defaults)
        let namespace = UUID().uuidString
        let started = expectation(description: "sync is in flight")
        var continuation: CheckedContinuation<ShoppingCartResponse, Error>?
        var operationID: String?
        let active = context(namespace: namespace, sync: { payload in
            operationID = payload.operations.first?.operationId
            return try await withCheckedThrowingContinuation { continuation = $0; started.fulfill() }
        })
        await store.activate(context: active)
        try store.addMany([ShoppingAddItem(name: "Gelöscht")])
        let task = Task { try await store.refresh() }
        await fulfillment(of: [started], timeout: 2)
        try store.eraseAccountData(namespace: namespace)
        await store.activate(context: active)
        XCTAssertFalse(store.isReady)
        XCTAssertTrue(memory.values.isEmpty)
        let writes = memory.writes
        continuation?.resume(returning: ShoppingCartResponse(householdId: 7, items: [row(8)],
            results: [ShoppingReceipt(operationId: try XCTUnwrap(operationID), status: "applied", itemId: 8)]))
        do { try await task.value; XCTFail("A deleted account's response must be discarded") } catch {}
        XCTAssertEqual(memory.writes, writes)
        XCTAssertTrue(memory.values.isEmpty)
        XCTAssertTrue(store.items.isEmpty)
        XCTAssertFalse(store.isSyncing)
    }

    func testFileStorageErasureUsesExactDigestAndKeepsOtherNamespaces() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("ShoppingErasure-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: directory) }
        let storage = ShoppingJournalStorage.files(directory: directory)
        let own = String(repeating: "a", count: 64)
        let foreign = String(repeating: "b", count: 64)
        let privateData = Data("private".utf8)
        try storage.eraseNamespace(own) // An account without journals is already clean.
        for suffix in ["active", "household-7", "household-9"] {
            try storage.write("\(own)-\(suffix).json", privateData)
        }
        try storage.write("\(foreign)-active.json", Data("foreign".utf8))
        try storage.write("unrelated.json", Data("unrelated".utf8))
        XCTAssertThrowsError(try storage.eraseNamespace("../"))
        XCTAssertEqual(try storage.read("\(own)-active.json"), privateData)
        try storage.eraseNamespace(own)
        XCTAssertNil(try storage.read("\(own)-active.json"))
        XCTAssertNil(try storage.read("\(own)-household-7.json"))
        XCTAssertNil(try storage.read("\(own)-household-9.json"))
        XCTAssertEqual(try storage.read("\(foreign)-active.json"), Data("foreign".utf8))
        XCTAssertEqual(try storage.read("unrelated.json"), Data("unrelated".utf8))
    }

    func testMissingUserIDUsesOnlyMatchingActiveSessionOrReportsUnidentifiedData() async throws {
        let memory = Memory()
        let suite = "ShoppingTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let session = SessionStore(defaults: defaults)
        XCTAssertNil(session.userID)
        let store = ShoppingSyncStore(storage: memory.storage, defaults: defaults)
        await store.activate(context: context(identity: session.identity, items: [row()]))
        try store.eraseAccountData(session: session)
        XCTAssertTrue(memory.values.isEmpty)
        XCTAssertFalse(store.isReady)
        await store.activate(context: context(identity: UUID(), items: [row(2)]))
        let unrelated = memory.values
        XCTAssertThrowsError(try store.eraseAccountData(session: session)) { error in
            XCTAssertTrue(error.localizedDescription.contains("nicht sicher zugeordnet"))
        }
        XCTAssertFalse(store.isReady)
        XCTAssertEqual(memory.values, unrelated, "Unidentified data must not erase a different account")
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
