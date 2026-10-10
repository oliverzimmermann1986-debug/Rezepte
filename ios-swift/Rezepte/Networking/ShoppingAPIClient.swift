import Foundation

extension APIClient {
    func shoppingCart() async throws -> ShoppingCartResponse {
        try await kitchenRequest("/api/cart")
    }

    func shoppingSynchronize(_ body: ShoppingSyncPayload) async throws -> ShoppingCartResponse {
        try await kitchenRequest("/api/cart/sync", method: "POST", body: body)
    }
}
