import Foundation

extension APIClient {
    func cookbookPDF(id: Int) async throws -> Data {
        let data = try await kitchenDownload("/api/cookbooks/\(id)/pdf", accept: "application/pdf")
        guard data.starts(with: Data("%PDF-".utf8)), data.count <= CookbookPDFDocument.maximumBytes else {
            throw APIError.invalidResponse("Kochbuch-PDF")
        }
        return data
    }
}
