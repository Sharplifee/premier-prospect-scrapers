// OData v4.0 query builder for RESO Web API
// Generates valid query strings for UtahRealEstate.com endpoints

import Foundation

struct ODataQuery {
    private var components: URLComponents

    init(baseURL: URL, resource: String) {
        var comps = URLComponents(url: baseURL, resolvingAgainstBaseURL: false)!
        comps.path = (comps.path as NSString).appendingPathComponent(resource)
        self.components = comps
    }

    // MARK: - Query options

    func filter(_ expression: String) -> ODataQuery {
        return setQueryItem(name: "$filter", value: expression)
    }

    func select(_ fields: [String]) -> ODataQuery {
        return setQueryItem(name: "$select", value: fields.joined(separator: ","))
    }

    func orderBy(_ field: String, descending: Bool = false) -> ODataQuery {
        let direction = descending ? "desc" : "asc"
        return setQueryItem(name: "$orderby", value: "\(field) \(direction)")
    }

    func top(_ count: Int) -> ODataQuery {
        return setQueryItem(name: "$top", value: String(count))
    }

    func skip(_ count: Int) -> ODataQuery {
        return setQueryItem(name: "$skip", value: String(count))
    }

    func expand(_ resources: [String]) -> ODataQuery {
        return setQueryItem(name: "$expand", value: resources.joined(separator: ","))
    }

    func count(_ include: Bool = true) -> ODataQuery {
        return setQueryItem(name: "$count", value: include ? "true" : "false")
    }

    var url: URL? {
        components.url
    }

    private func setQueryItem(name: String, value: String) -> ODataQuery {
        var copy = self
        var items = copy.components.queryItems ?? []
        items.removeAll { $0.name == name }
        items.append(URLQueryItem(name: name, value: value))
        copy.components.queryItems = items
        return copy
    }
}

// MARK: - Typed filter helpers

extension ODataQuery {

    // StandardStatus eq StandardStatus'Active'
    static func statusFilter(_ status: StandardStatus) -> String {
        "StandardStatus eq Odata.Models.StandardStatus'\(status.rawValue)'"
    }

    // Price range: ListPrice ge 300000 and ListPrice le 800000
    static func priceRangeFilter(min: Decimal? = nil, max: Decimal? = nil) -> String {
        var parts: [String] = []
        if let min { parts.append("ListPrice ge \(min)") }
        if let max { parts.append("ListPrice le \(max)") }
        return parts.joined(separator: " and ")
    }

    // Beds: BedsTotal ge 3
    static func bedsFilter(min: Int) -> String {
        "BedsTotal ge \(min)"
    }

    // Modified after timestamp for incremental replication
    static func modifiedSince(_ date: Date) -> String {
        let iso = ISO8601DateFormatter().string(from: date)
        return "ModificationTimestamp gt \(iso)"
    }

    // City filter
    static func cityFilter(_ city: String) -> String {
        "City eq '\(city)'"
    }

    // Postal code
    static func postalCodeFilter(_ zip: String) -> String {
        "PostalCode eq '\(zip)'"
    }

    // Combine filters with AND
    static func and(_ filters: String...) -> String {
        filters.filter { !$0.isEmpty }.joined(separator: " and ")
    }

    // Combine filters with OR
    static func or(_ filters: String...) -> String {
        "(\(filters.filter { !$0.isEmpty }.joined(separator: " or ")))"
    }
}
