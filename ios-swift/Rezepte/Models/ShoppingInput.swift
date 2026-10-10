import Foundation

enum ShoppingInput {
    static let maxItems = 50
    static let maxTextLength = 10_000
    static let units = ["mg", "g", "kg", "ml", "cl", "dl", "l", "TL", "EL", "Stück", "Prise", "Zehe", "Bund",
                        "Scheibe", "Blatt", "Tasse", "Dose", "Pck", "Flasche", "Tüte", "Glas"]
    private static let aliases: [String: String] = [
        "gramm": "g", "gr": "g", "kilogramm": "kg", "kilo": "kg", "milligramm": "mg",
        "milliliter": "ml", "liter": "l", "ltr": "l", "centiliter": "cl", "zentiliter": "cl", "deziliter": "dl",
        "tl": "TL", "teelöffel": "TL", "tsp": "TL", "el": "EL", "esslöffel": "EL", "tbsp": "EL",
        "stück": "Stück", "stueck": "Stück", "stk": "Stück", "stk.": "Stück", "x": "Stück",
        "zehe": "Zehe", "zehen": "Zehe", "bund": "Bund", "scheibe": "Scheibe", "scheiben": "Scheibe",
        "blatt": "Blatt", "blätter": "Blatt", "prise": "Prise", "prisen": "Prise", "tasse": "Tasse", "tassen": "Tasse",
        "dose": "Dose", "dosen": "Dose", "pck": "Pck", "pck.": "Pck", "packung": "Pck", "packungen": "Pck",
        "flasche": "Flasche", "flaschen": "Flasche", "tüte": "Tüte", "tuete": "Tüte", "tueten": "Tüte", "tüten": "Tüte",
        "glas": "Glas", "gläser": "Glas"
    ]

    static func amount(_ text: String) throws -> Double? {
        let raw = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.isEmpty else { return nil }
        guard matches(#"^\d+(?:[.,]\d+)?$"#, raw) != nil,
              let value = Double(raw.replacingOccurrences(of: ",", with: ".")),
              value.isFinite, value > 0, value <= 1_000_000 else {
            throw APIError.server(0, "Bitte eine Menge größer als 0 und höchstens 1.000.000 eingeben.")
        }
        return value
    }

    static func validate(_ entry: ShoppingAddItem) throws -> ShoppingAddItem {
        var entry = entry
        entry.name = entry.name.trimmingCharacters(in: .whitespacesAndNewlines)
        entry.unit = entry.unit?.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty
        guard !entry.name.isEmpty, entry.name.utf16.count <= 200 else {
            throw APIError.server(0, "Bitte einen Artikelnamen mit höchstens 200 Zeichen eingeben.")
        }
        if let amount = entry.amount, !amount.isFinite || amount <= 0 || amount > 1_000_000 {
            throw APIError.server(0, "Bitte eine Menge größer als 0 und höchstens 1.000.000 eingeben.")
        }
        guard (entry.unit?.utf16.count ?? 0) <= 40, (entry.category?.utf16.count ?? 0) <= 100 else {
            throw APIError.server(0, "Die Einheit oder Kategorie ist zu lang.")
        }
        return entry
    }

    static func parse(_ text: String) throws -> [ShoppingAddItem] {
        guard text.utf16.count <= maxTextLength else { throw APIError.server(0, "Bitte höchstens 10.000 Zeichen einfügen.") }
        let chars = Array(text)
        var parts: [String] = []
        var part = ""
        for index in chars.indices {
            let char = chars[index]
            let decimal = char == "," && index > 0 && index + 1 < chars.count
                && chars[index - 1].isNumber && chars[index + 1].isNumber
            if char == "\n" || char == "\r" || char == ";" || (char == "," && !decimal) {
                if let trimmed = part.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty { parts.append(trimmed) }
                part = ""
            } else { part.append(char) }
        }
        if let trimmed = part.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty { parts.append(trimmed) }
        guard parts.count <= maxItems else { throw APIError.server(0, "Bitte höchstens 50 Artikel hinzufügen.") }
        let entries: [ShoppingAddItem] = try parts.compactMap { line in
            let raw = line.replacingOccurrences(of: #"^\s*(?:(?:[-*]\s+|•\s*)(?:\[[ xX]\]\s*)?|\[[ xX]\]\s*|\d+[.)]\s+)"#,
                with: "", options: .regularExpression).trimmingCharacters(in: .whitespacesAndNewlines)
            guard !raw.isEmpty else { return nil }
            var entry = ShoppingAddItem(name: raw)
            if let prefix = matches(#"^(\d+(?:[.,]\d+)?|[½¼¾])\s*"#, raw) {
                let rest = String(raw.dropFirst(prefix[0].count))
                let fractions = ["½": 0.5, "¼": 0.25, "¾": 0.75]
                let parsed = fractions[prefix[1]] ?? Double(prefix[1].replacingOccurrences(of: ",", with: "."))
                if !rest.isEmpty, matches(#"^[\d/.,\-–%]"#, rest) == nil,
                   matches(#"^\d{1,3}\.\d{3}$"#, prefix[1]) == nil,
                   let parsed, parsed.isFinite, parsed > 0, parsed <= 1_000_000 {
                    let token = matches(#"^([\p{L}.]+)(?:\s+|$)"#, rest)
                    let normalized = token.map { aliases[$0[1].lowercased()] ?? $0[1] } ?? ""
                    let knownUnit = units.contains(normalized)
                    let remaining = token.map { String(rest.dropFirst($0[0].count)).trimmingCharacters(in: .whitespacesAndNewlines) } ?? ""
                    if knownUnit, !remaining.isEmpty {
                        entry.amount = parsed
                        entry.unit = normalized
                        entry.name = remaining
                    } else if matches(#"^(?:x\s+|×\s*)\S"#, rest) != nil {
                        entry.amount = parsed
                        entry.unit = "Stück"
                        entry.name = rest.replacingOccurrences(of: #"^[x×]\s*"#, with: "", options: .regularExpression)
                    } else if prefix[0].last?.isWhitespace == true, !knownUnit {
                        entry.amount = parsed
                        entry.name = rest.trimmingCharacters(in: .whitespacesAndNewlines)
                    }
                }
            }
            return try validate(entry)
        }
        guard !entries.isEmpty else { throw APIError.server(0, "Bitte mindestens einen Artikel eingeben.") }
        return entries
    }

    private static func matches(_ pattern: String, _ value: String) -> [String]? {
        guard let regex = try? NSRegularExpression(pattern: pattern),
              let result = regex.firstMatch(in: value, range: NSRange(value.startIndex..., in: value)) else { return nil }
        return (0..<result.numberOfRanges).map { index in
            Range(result.range(at: index), in: value).map { String(value[$0]) } ?? ""
        }
    }
}
