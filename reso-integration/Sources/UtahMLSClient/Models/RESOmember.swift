// RESO Data Dictionary 2.0 – Member Resource (Agents)

import Foundation

enum MemberStatus: String, Codable {
    case active = "Active"
    case inactive = "Inactive"
    case deleted = "Deleted"
}

struct RESOmember: Codable, Identifiable {
    var id: String { memberKey }

    let memberKey: String
    let memberKeyNumeric: Int?
    let memberMlsId: String?
    let memberNationalAssociationId: String?
    let memberStateLicenseNumber: String?
    let memberStateLicenseState: String?
    let memberFirstName: String?
    let memberLastName: String?
    let memberFullName: String?
    let memberEmail: String?
    let memberDirectPhone: String?
    let memberOfficePhone: String?
    let memberMobilePhone: String?
    let memberFax: String?
    let memberURL: URL?
    let memberType: String?
    let memberStatus: MemberStatus?
    let officeKey: String?
    let officeKeyNumeric: Int?
    let officeMlsId: String?
    let officeName: String?
    let memberDesignation: [String]?
    let memberAOR: String?
    let memberAORMlsId: String?
    let modificationTimestamp: Date?
    let originalEntryTimestamp: Date?

    enum CodingKeys: String, CodingKey {
        case memberKey = "MemberKey"
        case memberKeyNumeric = "MemberKeyNumeric"
        case memberMlsId = "MemberMlsId"
        case memberNationalAssociationId = "MemberNationalAssociationId"
        case memberStateLicenseNumber = "MemberStateLicenseNumber"
        case memberStateLicenseState = "MemberStateLicenseState"
        case memberFirstName = "MemberFirstName"
        case memberLastName = "MemberLastName"
        case memberFullName = "MemberFullName"
        case memberEmail = "MemberEmail"
        case memberDirectPhone = "MemberDirectPhone"
        case memberOfficePhone = "MemberOfficePhone"
        case memberMobilePhone = "MemberMobilePhone"
        case memberFax = "MemberFax"
        case memberURL = "MemberURL"
        case memberType = "MemberType"
        case memberStatus = "MemberStatus"
        case officeKey = "OfficeKey"
        case officeKeyNumeric = "OfficeKeyNumeric"
        case officeMlsId = "OfficeMlsId"
        case officeName = "OfficeName"
        case memberDesignation = "MemberDesignation"
        case memberAOR = "MemberAOR"
        case memberAORMlsId = "MemberAORMlsId"
        case modificationTimestamp = "ModificationTimestamp"
        case originalEntryTimestamp = "OriginalEntryTimestamp"
    }
}
