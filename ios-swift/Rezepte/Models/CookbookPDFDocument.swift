import CoreTransferable
import Foundation
import UniformTypeIdentifiers

/// Keep the private export in memory. The system owns temporary files for sharing.
struct CookbookPDFDocument: Transferable, Sendable {
    static let maximumBytes = 30 * 1024 * 1024
    let data: Data
    let filename: String

    init(data: Data, name: String) {
        self.data = data
        let cleaned = name.components(separatedBy: CharacterSet(charactersIn: "/\\:\n\r\t")
            .union(.controlCharacters)).joined(separator: "-")
            .trimmingCharacters(in: CharacterSet(charactersIn: ". "))
        filename = String((cleaned.isEmpty ? "Kochbuch" : cleaned).prefix(80)) + ".pdf"
    }

    static var transferRepresentation: some TransferRepresentation {
        DataRepresentation(exportedContentType: .pdf) { (document: CookbookPDFDocument) in
            document.data
        }
        .suggestedFileName { $0.filename }
    }
}
