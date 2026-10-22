// Incremental replication engine for WFRMLS → local store
//
// Pattern: timestamp-based incremental sync using ModificationTimestamp.
// Store the last-synced timestamp; on each run fetch only records modified after it.
// This is the RESO-recommended replication strategy.

import Foundation

protocol PropertyStore {
    func upsert(_ properties: [RESOProperty]) async throws
    func lastReplicationTimestamp() async throws -> Date?
    func saveReplicationTimestamp(_ date: Date) async throws
}

protocol MediaStore {
    func upsert(_ media: [RESOMedia]) async throws
}

struct ReplicationEngine {
    private let client: RESOAPIClient
    private let propertyStore: any PropertyStore
    private let mediaStore: any MediaStore

    init(client: RESOAPIClient, propertyStore: any PropertyStore, mediaStore: any MediaStore) {
        self.client = client
        self.propertyStore = propertyStore
        self.mediaStore = mediaStore
    }

    // MARK: - Full property sync

    func syncProperties(since: Date? = nil) async throws {
        let lastSync = try await since ?? propertyStore.lastReplicationTimestamp()
        // Fall back to 30 days if no prior sync timestamp
        let syncFrom = lastSync ?? Calendar.current.date(byAdding: .day, value: -30, to: Date())!
        var latestTimestamp = syncFrom

        try await client.replicateProperties(since: syncFrom) { page in
            try await propertyStore.upsert(page)
            if let newest = page.compactMap(\.modificationTimestamp).max(), newest > latestTimestamp {
                latestTimestamp = newest
            }
        }

        try await propertyStore.saveReplicationTimestamp(latestTimestamp)
    }

    // MARK: - Media sync for a specific listing

    func syncMedia(forListingKey key: String) async throws {
        let media = try await client.media(forListingKey: key)
        try await mediaStore.upsert(media)
    }

    // MARK: - IDX payload fields (minimal set for consumer display)
    // Per NAR IDX rules, these are the fields typically permitted for public display.

    static let idxSelectFields: [String] = [
        "ListingKey", "ListingId", "StandardStatus", "MlsStatus",
        "ListPrice", "OriginalListPrice", "ClosePrice",
        "UnparsedAddress", "StreetNumber", "StreetName", "StreetSuffix",
        "UnitNumber", "City", "StateOrProvince", "PostalCode", "County",
        "Latitude", "Longitude",
        "BedsTotal", "BedroomsTotal",
        "BathroomsTotalDecimal", "BathroomsFull", "BathroomsHalf",
        "LivingArea", "LivingAreaUnits",
        "LotSizeAcres", "LotSizeSquareFeet",
        "YearBuilt", "PropertyType", "PropertySubType",
        "PublicRemarks",
        "PhotosCount", "PhotosChangeTimestamp",
        "ListAgentFullName", "ListAgentMlsId", "ListAgentDirectPhone",
        "ListOfficeName", "ListOfficePhone",
        "OnMarketDate", "DaysOnMarket",
        "ModificationTimestamp",
        "SubdivisionName",
        "AssociationYN", "AssociationFee", "AssociationFeeFrequency",
        "InternetEntireListingDisplayYN", "InternetAddressDisplayYN"
    ]
}
