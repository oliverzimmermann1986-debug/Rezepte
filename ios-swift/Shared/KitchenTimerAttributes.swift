import ActivityKit
import Foundation

struct KitchenTimerAttributes: ActivityAttributes {
    struct ContentState: Codable, Hashable {
        var label: String
        var endsAt: Date
    }
    var timerID: String
}
