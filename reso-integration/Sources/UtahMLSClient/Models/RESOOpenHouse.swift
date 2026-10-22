// RESO Data Dictionary 2.0 – OpenHouse Resource

import Foundation

struct RESOOpenHouse: Codable, Identifiable {
    var id: String { openHouseKey }

    let openHouseKey: String
    let openHouseKeyNumeric: Int?
    let listingKey: String?
    let listingKeyNumeric: Int?
    let openHouseDate: Date?
    let openHouseStartTime: Date?
    let openHouseEndTime: Date?
    let openHouseType: String?
    let openHouseRemarks: String?
    let appointmentRequiredYN: Bool?
    let virtualOpenHouseURL: URL?
    let showingAgentFirstName: String?
    let showingAgentLastName: String?
    let showingAgentMlsId: String?
    let showingAgentKey: String?
    let modificationTimestamp: Date?

    enum CodingKeys: String, CodingKey {
        case openHouseKey = "OpenHouseKey"
        case openHouseKeyNumeric = "OpenHouseKeyNumeric"
        case listingKey = "ListingKey"
        case listingKeyNumeric = "ListingKeyNumeric"
        case openHouseDate = "OpenHouseDate"
        case openHouseStartTime = "OpenHouseStartTime"
        case openHouseEndTime = "OpenHouseEndTime"
        case openHouseType = "OpenHouseType"
        case openHouseRemarks = "OpenHouseRemarks"
        case appointmentRequiredYN = "AppointmentRequiredYN"
        case virtualOpenHouseURL = "VirtualOpenHouseURL"
        case showingAgentFirstName = "ShowingAgentFirstName"
        case showingAgentLastName = "ShowingAgentLastName"
        case showingAgentMlsId = "ShowingAgentMlsId"
        case showingAgentKey = "ShowingAgentKey"
        case modificationTimestamp = "ModificationTimestamp"
    }
}
