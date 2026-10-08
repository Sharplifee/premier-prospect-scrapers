// UtahRealEstate.com (WFRMLS) RESO Web API Client
//
// API documentation: https://vendor.utahrealestate.com/webapi/docs
// Vendor registration: https://vendor.utahrealestate.com
//
// Authentication: Bearer token issued after signing the WFRMLS data licensing agreement.
// Base URL: https://replication.utahrealestate.com  (confirmed via vendor portal)
// OData root: GET /reso/odata
//
// IMPORTANT: Production access requires a signed licensing agreement with WFRMLS.
// Contact: vendor.utahrealestate.com to register as a data services vendor.

import Foundation

// MARK: - Configuration

struct WFRMLSConfiguration {
    let bearerToken: String
    // Confirmed from vendor.utahrealestate.com documentation
    let baseURL: URL

    static let productionBaseURL = URL(string: "https://replication.utahrealestate.com")!

    init(bearerToken: String, baseURL: URL = productionBaseURL) {
        self.bearerToken = bearerToken
        self.baseURL = baseURL
    }

    var odataBaseURL: URL {
        baseURL.appendingPathComponent("reso/odata")
    }
}

// MARK: - Errors

enum RESOAPIError: Error, LocalizedError {
    case invalidURL
    case unauthorized
    case forbidden
    case notFound
    case rateLimited(retryAfter: TimeInterval?)
    case serverError(statusCode: Int, body: String?)
    case decodingError(Error)
    case networkError(Error)

    var errorDescription: String? {
        switch self {
        case .unauthorized: return "Bearer token is missing or expired. Obtain a new token from the WFRMLS vendor portal."
        case .forbidden: return "Access denied. Ensure your data licensing agreement covers the requested resource."
        case .notFound: return "Resource not found."
        case .rateLimited(let retry): return "Rate limited. Retry after \(retry.map { "\(Int($0))s" } ?? "unknown")."
        case .serverError(let code, _): return "Server error \(code)."
        case .decodingError(let e): return "Decoding error: \(e.localizedDescription)"
        case .networkError(let e): return "Network error: \(e.localizedDescription)"
        case .invalidURL: return "Invalid URL constructed."
        }
    }
}

// MARK: - Paged result

struct PagedResult<T> {
    let value: [T]
    let nextLink: URL?
    let totalCount: Int?
}

// MARK: - API Client

actor RESOAPIClient {
    private let config: WFRMLSConfiguration
    private let session: URLSession
    private let decoder: JSONDecoder

    init(config: WFRMLSConfiguration, session: URLSession = .shared) {
        self.config = config
        self.session = session

        let dec = JSONDecoder()
        // RESO timestamps are ISO8601 with timezone offsets e.g. "2024-05-30T20:42:02.96-07:00"
        dec.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let string = try container.decode(String.self)

            let formatters: [ISO8601DateFormatter] = [
                {
                    let f = ISO8601DateFormatter()
                    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
                    return f
                }(),
                {
                    let f = ISO8601DateFormatter()
                    f.formatOptions = [.withInternetDateTime]
                    return f
                }(),
                {
                    let f = ISO8601DateFormatter()
                    f.formatOptions = [.withFullDate]
                    return f
                }()
            ]

            for formatter in formatters {
                if let date = formatter.date(from: string) { return date }
            }
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Cannot parse date: \(string)")
        }
        self.decoder = dec
    }

    // MARK: - Service Document

    /// Returns the list of available resources (Property, Member, Office, Media, etc.)
    func serviceDocument() async throws -> [String: String] {
        let url = config.odataBaseURL
        let data = try await perform(url: url)
        let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        let value = json?["value"] as? [[String: String]] ?? []
        return Dictionary(uniqueKeysWithValues: value.compactMap { dict in
            guard let name = dict["name"], let resourceURL = dict["url"] else { return nil }
            return (name, resourceURL)
        })
    }

    // MARK: - Metadata

    /// Returns raw OData $metadata XML describing all resources and fields
    func metadata() async throws -> Data {
        let url = config.odataBaseURL.appendingPathComponent("$metadata")
        return try await perform(url: url)
    }

    // MARK: - Property Resource

    func properties(query: ODataQuery) async throws -> PagedResult<RESOProperty> {
        guard let url = query.url else { throw RESOAPIError.invalidURL }
        return try await fetchPaged(url: url)
    }

    func property(listingKey: String) async throws -> RESOProperty {
        let url = config.odataBaseURL
            .appendingPathComponent("Property")
            .appendingPathComponent("('\(listingKey)')")
        let data = try await perform(url: url)
        return try decoder.decode(RESOProperty.self, from: data)
    }

    /// Incremental replication: fetch all properties modified since a given timestamp.
    /// Paginates automatically until exhausted. Use for background sync.
    func replicateProperties(
        since: Date,
        pageSize: Int = 200,
        onPage: @escaping ([RESOProperty]) async throws -> Void
    ) async throws {
        let baseQuery = ODataQuery(baseURL: config.odataBaseURL, resource: "Property")
            .filter(ODataQuery.modifiedSince(since))
            .orderBy("ModificationTimestamp")
            .top(pageSize)

        var nextURL: URL? = baseQuery.url
        while let url = nextURL {
            let result: PagedResult<RESOProperty> = try await fetchPaged(url: url)
            try await onPage(result.value)
            nextURL = result.nextLink
        }
    }

    // MARK: - Media Resource

    func media(forListingKey listingKey: String) async throws -> [RESOMedia] {
        let query = ODataQuery(baseURL: config.odataBaseURL, resource: "Media")
            .filter("ListingKey eq '\(listingKey)'")
            .orderBy("Order")
        guard let url = query.url else { throw RESOAPIError.invalidURL }
        let result: PagedResult<RESOMedia> = try await fetchPaged(url: url)
        return result.value
    }

    // MARK: - OpenHouse Resource

    func openHouses(forListingKey listingKey: String) async throws -> [RESOOpenHouse] {
        let query = ODataQuery(baseURL: config.odataBaseURL, resource: "OpenHouse")
            .filter("ListingKey eq '\(listingKey)'")
        guard let url = query.url else { throw RESOAPIError.invalidURL }
        let result: PagedResult<RESOOpenHouse> = try await fetchPaged(url: url)
        return result.value
    }

    // MARK: - Member Resource

    func member(memberKey: String) async throws -> RESOmember {
        let url = config.odataBaseURL
            .appendingPathComponent("Member")
            .appendingPathComponent("('\(memberKey)')")
        let data = try await perform(url: url)
        return try decoder.decode(RESOmember.self, from: data)
    }

    // MARK: - Office Resource

    func office(officeKey: String) async throws -> RESOOffice {
        let url = config.odataBaseURL
            .appendingPathComponent("Office")
            .appendingPathComponent("('\(officeKey)')")
        let data = try await perform(url: url)
        return try decoder.decode(RESOOffice.self, from: data)
    }

    // MARK: - Lookup Resource

    /// Returns enumeration values for a given lookup (e.g. "MlsStatus", "StandardStatus")
    func lookupValues(for lookupName: String) async throws -> [RESOLookup] {
        let query = ODataQuery(baseURL: config.odataBaseURL, resource: "Lookup")
            .filter("LookupName eq '\(lookupName)'")
        guard let url = query.url else { throw RESOAPIError.invalidURL }
        let result: PagedResult<RESOLookup> = try await fetchPaged(url: url)
        return result.value
    }

    // MARK: - Private

    private func fetchPaged<T: Decodable>(url: URL) async throws -> PagedResult<T> {
        let data = try await perform(url: url)
        let response = try decoder.decode(ODataResponse<T>.self, from: data)
        let nextLink = response.nextLink.flatMap { URL(string: $0) }
        return PagedResult(value: response.value, nextLink: nextLink, totalCount: nil)
    }

    private func perform(url: URL) async throws -> Data {
        var request = URLRequest(url: url)
        request.setValue("Bearer \(config.bearerToken)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw RESOAPIError.networkError(error)
        }

        guard let http = response as? HTTPURLResponse else {
            throw RESOAPIError.networkError(URLError(.badServerResponse))
        }

        switch http.statusCode {
        case 200...299:
            return data
        case 401:
            throw RESOAPIError.unauthorized
        case 403:
            throw RESOAPIError.forbidden
        case 404:
            throw RESOAPIError.notFound
        case 429:
            let retryAfter = http.value(forHTTPHeaderField: "Retry-After").flatMap { TimeInterval($0) }
            throw RESOAPIError.rateLimited(retryAfter: retryAfter)
        default:
            let body = String(data: data, encoding: .utf8)
            throw RESOAPIError.serverError(statusCode: http.statusCode, body: body)
        }
    }
}

// MARK: - Lookup model

struct RESOLookup: Codable, Identifiable {
    var id: String { lookupKey }

    let lookupKey: String
    let lookupName: String?
    let lookupValue: String?
    let standardLookupValue: String?
    let modificationTimestamp: Date?

    enum CodingKeys: String, CodingKey {
        case lookupKey = "LookupKey"
        case lookupName = "LookupName"
        case lookupValue = "LookupValue"
        case standardLookupValue = "StandardLookupValue"
        case modificationTimestamp = "ModificationTimestamp"
    }
}
