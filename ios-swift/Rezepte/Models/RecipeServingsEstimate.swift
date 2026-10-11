import Foundation

enum ServingsEstimateConfidence: String, Decodable {
    case low, medium, high

    var title: String {
        switch self {
        case .low: return "Geringe Sicherheit"
        case .medium: return "Mittlere Sicherheit"
        case .high: return "Hohe Sicherheit"
        }
    }
}

struct RecipeServingsEstimate: Decodable {
    let currentServings: Int?
    let suggestedServings: Int?
    let minServings: Int?
    let maxServings: Int?
    let confidence: ServingsEstimateConfidence
    let rationale: String
    let warnings: [String]
    let fingerprint: String

    var rangeText: String? {
        guard let minServings, let maxServings else { return nil }
        return minServings == maxServings ? "Etwa \(minServings) Portionen" : "Plausibler Bereich: \(minServings)–\(maxServings) Portionen"
    }

    static func isValidFingerprint(_ value: String) -> Bool {
        value.utf8.count == 64 && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }

    func validated() throws -> Self {
        guard Self.isValidFingerprint(fingerprint), !rationale.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              [suggestedServings, minServings, maxServings].compactMap({ $0 }).allSatisfy({ (1...50).contains($0) }),
              (minServings == nil) == (maxServings == nil) else {
            throw APIError.invalidResponse("Portionsschätzung")
        }
        if let suggestedServings {
            guard let minimum = minServings, let maximum = maxServings,
                  minimum <= maximum, (minimum...maximum).contains(suggestedServings) else {
                throw APIError.invalidResponse("Portionsschätzung")
            }
        } else if minServings != nil || maxServings != nil || confidence != .low {
            throw APIError.invalidResponse("Portionsschätzung")
        }
        return self
    }
}

struct RecipeServingsApplyResponse: Decodable {
    let ok: Bool
    let servings: Int
}

struct RecipeServingsEstimateTarget: Identifiable {
    let id: Int
    let name: String
    let currentServings: Int?
    let identity: UUID
}
