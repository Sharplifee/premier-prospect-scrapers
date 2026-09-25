// RESO Data Dictionary 2.0 – Office Resource (Brokerages)

import Foundation

struct RESOOffice: Codable, Identifiable {
    var id: String { officeKey }

    let officeKey: String
    let officeKeyNumeric: Int?
    let officeMlsId: String?
    let officeNationalAssociationId: String?
    let officeName: String?
    let officeBranchType: String?
    let officeAddress1: String?
    let officeAddress2: String?
    let officeCity: String?
    let officeStateOrProvince: String?
    let officePostalCode: String?
    let officeCounty: String?
    let officeCountry: String?
    let officePhone: String?
    let officeFax: String?
    let officeEmail: String?
    let officeURL: URL?
    let officeStatus: String?
    let officeAOR: String?
    let officeAORMlsId: String?
    let mainOfficeKey: String?
    let mainOfficeMlsId: String?
    let mainOfficeName: String?
    let modificationTimestamp: Date?
    let originalEntryTimestamp: Date?

    enum CodingKeys: String, CodingKey {
        case officeKey = "OfficeKey"
        case officeKeyNumeric = "OfficeKeyNumeric"
        case officeMlsId = "OfficeMlsId"
        case officeNationalAssociationId = "OfficeNationalAssociationId"
        case officeName = "OfficeName"
        case officeBranchType = "OfficeBranchType"
        case officeAddress1 = "OfficeAddress1"
        case officeAddress2 = "OfficeAddress2"
        case officeCity = "OfficeCity"
        case officeStateOrProvince = "OfficeStateOrProvince"
        case officePostalCode = "OfficePostalCode"
        case officeCounty = "OfficeCounty"
        case officeCountry = "OfficeCountry"
        case officePhone = "OfficePhone"
        case officeFax = "OfficeFax"
        case officeEmail = "OfficeEmail"
        case officeURL = "OfficeURL"
        case officeStatus = "OfficeStatus"
        case officeAOR = "OfficeAOR"
        case officeAORMlsId = "OfficeAORMlsId"
        case mainOfficeKey = "MainOfficeKey"
        case mainOfficeMlsId = "MainOfficeMlsId"
        case mainOfficeName = "MainOfficeName"
        case modificationTimestamp = "ModificationTimestamp"
        case originalEntryTimestamp = "OriginalEntryTimestamp"
    }
}
