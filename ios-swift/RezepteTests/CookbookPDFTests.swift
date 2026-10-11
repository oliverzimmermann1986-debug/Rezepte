import Foundation
import XCTest
@testable import Rezepte

final class CookbookPDFTests: XCTestCase {
    func testCookbookPDFUsesAuthenticatedEndpointAndRejectsNonPDF() async throws {
        let client = APIClient(session: MockURLProtocol.makeSession())
        try await client.configure(server: "https://example.de", token: "book-token")
        MockURLProtocol.respond(body: "%PDF-1.4\nsynthetic", headers: ["Content-Type": "application/pdf"])
        let data = try await client.cookbookPDF(id: 42)
        XCTAssertTrue(data.starts(with: Data("%PDF-".utf8)))
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/cookbooks/42/pdf")
        XCTAssertEqual(MockURLProtocol.lastHeader("Authorization"), "Bearer book-token")
        XCTAssertEqual(MockURLProtocol.lastHeader("Accept"), "application/pdf")
        MockURLProtocol.respond(json: #"{"unexpected":"json"}"#)
        do {
            _ = try await client.cookbookPDF(id: 42)
            XCTFail("A successful JSON response must never be shared as PDF")
        } catch APIError.invalidResponse { }
    }

    func testFilenameStaysOneReadablePDFName() {
        XCTAssertEqual(CookbookPDFDocument(data: Data(), name: "Für Gäste").filename, "Für Gäste.pdf")
        let filename = CookbookPDFDocument(data: Data(), name: "../../Privat/Geheim\n").filename
        XCTAssertFalse(filename.contains("/"))
        XCTAssertFalse(filename.contains("\n"))
        XCTAssertTrue(filename.hasSuffix(".pdf"))
        XCTAssertEqual(CookbookPDFDocument(data: Data(), name: " ... ").filename, "Kochbuch.pdf")
    }
}
