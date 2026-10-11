import Foundation
import XCTest
@testable import Rezepte

@MainActor
final class RecipeDeletionTests: XCTestCase {
    private func target(_ visibility: String?, canEdit: Bool? = true) -> RecipeDeletionTarget {
        RecipeDeletionTarget(id: 42, name: "Pasta", visibility: visibility, canEdit: canEdit)
    }

    func testDeletionRequiresWritableHouseholdOrGlobalAdminPermission() {
        XCTAssertTrue(RecipeDeletionCoordinator.isAllowed(target("private"), role: .user, readOnly: false, isOffline: false))
        XCTAssertFalse(RecipeDeletionCoordinator.isAllowed(target("global"), role: .user, readOnly: false, isOffline: false))
        XCTAssertFalse(RecipeDeletionCoordinator.isAllowed(target("private"), role: .guest, readOnly: false, isOffline: false))
        XCTAssertFalse(RecipeDeletionCoordinator.isAllowed(target("private", canEdit: nil), role: .user, readOnly: false, isOffline: false))
        XCTAssertTrue(RecipeDeletionCoordinator.isAllowed(target(nil, canEdit: nil), role: .admin, readOnly: false, isOffline: false))
        XCTAssertFalse(RecipeDeletionCoordinator.isAllowed(target("private", canEdit: false), role: .admin, readOnly: false, isOffline: false))
        XCTAssertFalse(RecipeDeletionCoordinator.isAllowed(target("private"), role: .user, readOnly: true, isOffline: false))
        XCTAssertFalse(RecipeDeletionCoordinator.isAllowed(target("private"), role: .user, readOnly: false, isOffline: true))
    }

    func testSuccessfulDeletionCleansCacheOnlyAfterAcknowledgement() async {
        var events: [String] = []
        let coordinator = RecipeDeletionCoordinator()
        let succeeded = await coordinator.delete(id: 42, context: RecipeDeletionContext(
            isCurrent: { true }, send: { id in events.append("server:\(id)"); return true },
            removeOffline: { events.append("offline:\($0)") },
            notifyDeleted: { events.append("notify:\($0)") },
            handleError: { _ in events.append("error") }
        ))
        XCTAssertTrue(succeeded)
        XCTAssertEqual(events, ["server:42", "offline:42", "notify:42"])
        XCTAssertFalse(coordinator.isDeleting)
    }

    func testRejectedOrUnacknowledgedDeletePreservesCacheAndVisibleRecipe() async {
        for throwsError in [false, true] {
            var cleaned = false, notified = false, reported = false
            let coordinator = RecipeDeletionCoordinator()
            let succeeded = await coordinator.delete(id: 42, context: RecipeDeletionContext(
                isCurrent: { true }, send: { _ in
                    if throwsError { throw APIError.server(403, "Keine Berechtigung") }
                    return false
                }, removeOffline: { _ in cleaned = true }, notifyDeleted: { _ in notified = true },
                handleError: { _ in reported = true }
            ))
            XCTAssertFalse(succeeded)
            XCTAssertFalse(cleaned)
            XCTAssertFalse(notified)
            XCTAssertTrue(reported)
        }
    }

    func testCleanupFailureReportsWarningWithoutRepeatingConfirmedDeletion() async {
        var requests = 0, notifications = 0, reports = 0
        let coordinator = RecipeDeletionCoordinator()
        let succeeded = await coordinator.delete(id: 42, context: RecipeDeletionContext(
            isCurrent: { true }, send: { _ in requests += 1; return true },
            removeOffline: { _ in throw CocoaError(.fileWriteNoPermission) },
            notifyDeleted: { _ in notifications += 1 }, handleError: { _ in reports += 1 }
        ))
        XCTAssertTrue(succeeded, "The server deletion already succeeded; dismiss the deleted recipe")
        XCTAssertEqual(requests, 1)
        XCTAssertEqual(notifications, 1)
        XCTAssertEqual(reports, 1)
    }

    func testLateAcknowledgementCleansOriginalNamespaceWithoutChangingNewSessionUI() async {
        var current = true, oldAccountCleaned = false, newSessionNotified = false, reported = false
        let coordinator = RecipeDeletionCoordinator()
        let succeeded = await coordinator.delete(id: 42, context: RecipeDeletionContext(
            isCurrent: { current }, send: { _ in current = false; return true },
            removeOffline: { _ in oldAccountCleaned = true },
            notifyDeleted: { _ in newSessionNotified = true }, handleError: { _ in reported = true }
        ))
        XCTAssertFalse(succeeded)
        XCTAssertTrue(oldAccountCleaned)
        XCTAssertFalse(newSessionNotified)
        XCTAssertFalse(reported)
    }

    func testInactiveContextDoesNotSendDeletion() async {
        var sent = false
        let succeeded = await RecipeDeletionCoordinator().delete(id: 42, context: RecipeDeletionContext(
            isCurrent: { false }, send: { _ in sent = true; return true },
            removeOffline: { _ in XCTFail("No cleanup before acknowledgement") },
            notifyDeleted: { _ in XCTFail("No notification before acknowledgement") },
            handleError: { _ in XCTFail("Inactive caller must stay silent") }
        ))
        XCTAssertFalse(succeeded)
        XCTAssertFalse(sent)
    }

    func testRepeatedTapCannotSendConcurrentDeleteRequests() async {
        let started = expectation(description: "Delete request started")
        var continuation: CheckedContinuation<Bool, Never>?
        var requests = 0
        let coordinator = RecipeDeletionCoordinator()
        let context = RecipeDeletionContext(isCurrent: { true }, send: { _ in
            requests += 1
            return await withCheckedContinuation { continuation = $0; started.fulfill() }
        }, removeOffline: { _ in }, notifyDeleted: { _ in }, handleError: { _ in XCTFail("Unexpected error") })
        let first = Task { await coordinator.delete(id: 42, context: context) }
        await fulfillment(of: [started], timeout: 2)
        let repeated = await coordinator.delete(id: 42, context: context)
        XCTAssertFalse(repeated)
        XCTAssertEqual(requests, 1)
        continuation?.resume(returning: true)
        let completed = await first.value
        XCTAssertTrue(completed)
    }
}
