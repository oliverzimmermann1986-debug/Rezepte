import CoreTransferable
import Foundation
import UniformTypeIdentifiers

/// A private, in-memory export; temporary share files are owned by the system.
struct PDFExportDocument: Transferable, Sendable {
    static let maximumBytes = 30 * 1024 * 1024
    let data: Data
    let filename: String

    init(data: Data, title: String) throws {
        guard data.starts(with: Data("%PDF-".utf8)), data.count <= Self.maximumBytes else {
            throw APIError.invalidResponse("PDF-Export")
        }
        self.data = data
        let cleaned = title.components(separatedBy: CharacterSet(charactersIn: "/\\:\n\r\t")
            .union(.controlCharacters)).joined(separator: "-")
            .trimmingCharacters(in: CharacterSet(charactersIn: ". "))
        filename = String((cleaned.isEmpty ? "Quellenküche" : cleaned).prefix(80)) + ".pdf"
    }

    static var transferRepresentation: some TransferRepresentation {
        DataRepresentation(exportedContentType: .pdf) { (document: PDFExportDocument) in document.data }
            .suggestedFileName { $0.filename }
    }
}
