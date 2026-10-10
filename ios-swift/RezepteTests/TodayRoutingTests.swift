import Foundation
import XCTest
@testable import Rezepte

final class TodayRoutingTests: XCTestCase {
    func testWishesRouteKeepsCurrentWeekForDirectEntry() {
        let week = currentWeek()
        let identity = UUID()
        let route = TodayWishesRoute(identity: identity, day: "2026-10-18", cachedWeek: week)

        XCTAssertEqual(route.identity, identity)
        XCTAssertEqual(route.day, "2026-10-18")
        XCTAssertEqual(route.cachedWeek?.weekStart, "2026-10-12")
        XCTAssertEqual(route.cachedWeek?.days.count, 7)
    }

    func testWishesRouteRefetchesAfterSundayToMondayRollover() {
        let route = TodayWishesRoute(identity: UUID(), day: "2026-10-19", cachedWeek: currentWeek())

        XCTAssertEqual(route.day, "2026-10-19")
        XCTAssertNil(route.cachedWeek, "Never open the previous week's voting after the local date changes.")
    }

    func testWishesRouteCanLoadWithoutSuccessfulTodayMealRequest() {
        let route = TodayWishesRoute(identity: UUID(), day: "2026-10-12", cachedWeek: nil)

        XCTAssertEqual(route.day, "2026-10-12")
        XCTAssertNil(route.cachedWeek)
    }

    private func currentWeek() -> MealWeek {
        MealWeek(
            weekStart: "2026-10-12", weekEnd: "2026-10-18",
            previousWeek: "2026-10-05", nextWeek: "2026-10-19", isCurrentWeek: true,
            days: (12...18).map { day in
                MealDay(date: "2026-10-\(day)", label: "Tag", shortLabel: "Tag", dayNumber: day, isToday: false, items: [])
            },
            shoppingPreview: [], summary: MealSummary(plannedMeals: 0, plannedDays: 0, shoppingItems: 0)
        )
    }
}
