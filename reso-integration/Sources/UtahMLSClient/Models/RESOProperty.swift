// RESO Data Dictionary 2.0 – Property Resource
// Standard field names per https://www.reso.org/data-dictionary
// Compatible with UtahRealEstate.com (WFRMLS) RESO Web API on OData v4.0
// Request licensed access via https://vendor.utahrealestate.com

import Foundation

// MARK: - OData wrapper

struct ODataResponse<T: Decodable>: Decodable {
    let context: String?
    let value: [T]
    let nextLink: String?

    enum CodingKeys: String, CodingKey {
        case context = "@odata.context"
        case value
        case nextLink = "@odata.nextLink"
    }
}

// MARK: - StandardStatus enumeration (RESO DD 2.0)

enum StandardStatus: String, Codable {
    case active = "Active"
    case activeUnderContract = "Active Under Contract"
    case canceled = "Canceled"
    case closed = "Closed"
    case expired = "Expired"
    case hold = "Hold"
    case pending = "Pending"
    case withdrawn = "Withdrawn"
    case delete = "Delete"
    case incomplete = "Incomplete"
    case comingSoon = "Coming Soon"
}

// MARK: - PropertyType enumeration (RESO DD 2.0)

enum PropertyType: String, Codable {
    case residential = "Residential"
    case residentialIncome = "Residential Income"
    case residentialLease = "Residential Lease"
    case land = "Land"
    case commercial = "Commercial Sale"
    case commercialLease = "Commercial Lease"
    case businessOpportunity = "Business Opportunity"
    case mobileHome = "Mobile Home"
    case farm = "Farm"
}

// MARK: - Property Resource (RESO Data Dictionary 2.0)

struct RESOProperty: Codable, Identifiable {
    // MARK: Listing Identifiers
    var id: String { listingKey }
    let listingKey: String
    let listingKeyNumeric: Int?
    let listingId: String?
    let mlsStatus: String?
    let standardStatus: StandardStatus?

    // MARK: Listing Dates & Timestamps
    let listingContractDate: Date?
    let onMarketDate: Date?
    let offMarketDate: Date?
    let closeDate: Date?
    let expirationDate: Date?
    let modificationTimestamp: Date?
    let statusChangeTimestamp: Date?
    let originalEntryTimestamp: Date?
    let priceChangeTimestamp: Date?

    // MARK: Price Fields
    let listPrice: Decimal?
    let closePrice: Decimal?
    let originalListPrice: Decimal?
    let previousListPrice: Decimal?
    let listPriceLow: Decimal?

    // MARK: Property Type
    let propertyType: PropertyType?
    let propertySubType: String?

    // MARK: Address Fields
    let streetNumber: String?
    let streetNumberNumeric: Int?
    let streetDirPrefix: String?
    let streetName: String?
    let streetSuffix: String?
    let streetDirSuffix: String?
    let unitNumber: String?
    let city: String?
    let stateOrProvince: String?
    let postalCode: String?
    let postalCodePlus4: String?
    let county: String?
    let country: String?
    let unparsedAddress: String?

    // MARK: Geographic Coordinates
    let latitude: Double?
    let longitude: Double?

    // MARK: Structure Details
    let bedsTotal: Int?
    let bedroomsTotal: Int?
    let bathroomsTotalDecimal: Decimal?
    let bathroomsTotalInteger: Int?
    let bathroomsFull: Int?
    let bathroomsHalf: Int?
    let bathroomsThreeQuarter: Int?
    let bathroomsPartial: Int?
    let roomsTotal: Int?
    let storiesTotal: Int?
    let levelsOrStories: String?

    // MARK: Square Footage
    let livingArea: Decimal?
    let livingAreaUnits: String?
    let aboveGradeFinishedArea: Decimal?
    let aboveGradeFinishedAreaUnits: String?
    let belowGradeFinishedArea: Decimal?
    let belowGradeFinishedAreaUnits: String?
    let belowGradeUnfinishedArea: Decimal?
    let buildingAreaTotal: Decimal?
    let buildingAreaUnits: String?

    // MARK: Lot Details
    let lotSizeAcres: Decimal?
    let lotSizeSquareFeet: Decimal?
    let lotSizeArea: Decimal?
    let lotSizeUnits: String?
    let lotFeatures: [String]?
    let frontageLength: String?
    let frontageType: [String]?

    // MARK: Year Built & Age
    let yearBuilt: Int?
    let yearBuiltEffective: Int?
    let yearBuiltDetails: String?

    // MARK: Garage & Parking
    let garageSpaces: Decimal?
    let garageYN: Bool?
    let attachedGarageYN: Bool?
    let openParkingSpaces: Decimal?
    let parkingTotal: Decimal?
    let parkingFeatures: [String]?

    // MARK: Interior Features
    let interiorFeatures: [String]?
    let appliances: [String]?
    let flooring: [String]?
    let fireplacesTotal: Int?
    let fireplaceYN: Bool?
    let fireplaceFeatures: [String]?
    let laundryFeatures: [String]?
    let basementYN: Bool?
    let basement: [String]?

    // MARK: Exterior & Lot
    let exteriorFeatures: [String]?
    let architecturalStyle: [String]?
    let constructionMaterials: [String]?
    let roofMaterials: [String]?
    let foundationDetails: [String]?
    let fencing: [String]?
    let otherStructures: [String]?

    // MARK: Utilities
    let heating: [String]?
    let heatingYN: Bool?
    let cooling: [String]?
    let coolingYN: Bool?
    let utilities: [String]?
    let sewer: [String]?
    let waterSource: [String]?
    let electricExpense: Decimal?
    let waterSewerExpense: Decimal?

    // MARK: Pool & Spa
    let poolYN: Bool?
    let poolFeatures: [String]?
    let spaYN: Bool?
    let spaFeatures: [String]?

    // MARK: HOA
    let associationYN: Bool?
    let associationName: String?
    let associationFee: Decimal?
    let associationFeeFrequency: String?
    let associationFee2: Decimal?
    let associationFee2Frequency: String?
    let associationAmenities: [String]?

    // MARK: School Information
    let elementarySchool: String?
    let elementarySchoolDistrict: String?
    let middleOrJuniorSchool: String?
    let middleOrJuniorSchoolDistrict: String?
    let highSchool: String?
    let highSchoolDistrict: String?

    // MARK: Community
    let communityFeatures: [String]?
    let seniorsOnlyHousing: Bool?

    // MARK: View & Waterfront
    let view: [String]?
    let viewYN: Bool?
    let waterfrontYN: Bool?
    let waterfrontFeatures: [String]?
    let waterBodyName: String?

    // MARK: Listing Agent/Office
    let listAgentKey: String?
    let listAgentKeyNumeric: Int?
    let listAgentMlsId: String?
    let listAgentFullName: String?
    let listAgentFirstName: String?
    let listAgentLastName: String?
    let listAgentEmail: String?
    let listAgentDirectPhone: String?
    let listOfficeKey: String?
    let listOfficeKeyNumeric: Int?
    let listOfficeMlsId: String?
    let listOfficeName: String?
    let listOfficePhone: String?

    // MARK: Buyer Agent/Office
    let buyerAgentKey: String?
    let buyerAgentFullName: String?
    let buyerAgentEmail: String?
    let buyerOfficeKey: String?
    let buyerOfficeName: String?

    // MARK: Co-Listing Agent
    let coListAgentKey: String?
    let coListAgentFullName: String?
    let coListOfficeKey: String?
    let coListOfficeName: String?

    // MARK: Showing & Access
    let showingInstructions: String?
    let lockBoxType: [String]?
    let lockBoxSerialNumber: String?
    let showingContactType: [String]?
    let showingContactName: String?
    let showingContactPhone: String?

    // MARK: IDX & Public Remarks
    let publicRemarks: String?
    let privateRemarks: String?
    let syndicationRemarks: String?
    let internetAddressDisplayYN: Bool?
    let internetEntireListingDisplayYN: Bool?
    let internetConsumerCommentYN: Bool?
    let internetAutomatedValuationDisplayYN: Bool?

    // MARK: Days on Market
    let daysOnMarket: Int?
    let cumulativeDaysOnMarket: Int?

    // MARK: Financial
    let taxAnnualAmount: Decimal?
    let taxYear: Int?
    let taxLegalDescription: String?
    let taxParcelNumber: String?
    let taxBookNumber: String?

    // MARK: Green & Energy
    let greenBuildingVerificationType: [String]?
    let greenEnergyEfficient: [String]?
    let greenEnergyGeneration: [String]?
    let greenIndoorAirQuality: [String]?
    let greenSustainability: [String]?
    let greenWaterConservation: [String]?

    // MARK: Media Count
    let photosCount: Int?
    let photosChangeTimestamp: Date?
    let videosCount: Int?

    // MARK: Open House
    let openHouseCount: Int?

    // MARK: Subdivision & Map
    let subdivisionName: String?
    let mapCoordinate: String?
    let directions: String?
    let crossStreet: String?

    // MARK: Ownership
    let ownerName: String?
    let ownerPhone: String?
    let occupantType: String?
    let occupantName: String?
    let tenantPays: [String]?

    // MARK: MLS-Specific (WFRMLS local fields may appear here prefixed)
    let originatingSystemName: String?
    let originatingSystemKey: String?
    let sourceSystemName: String?
    let sourceSystemKey: String?

    enum CodingKeys: String, CodingKey {
        case listingKey = "ListingKey"
        case listingKeyNumeric = "ListingKeyNumeric"
        case listingId = "ListingId"
        case mlsStatus = "MlsStatus"
        case standardStatus = "StandardStatus"
        case listingContractDate = "ListingContractDate"
        case onMarketDate = "OnMarketDate"
        case offMarketDate = "OffMarketDate"
        case closeDate = "CloseDate"
        case expirationDate = "ExpirationDate"
        case modificationTimestamp = "ModificationTimestamp"
        case statusChangeTimestamp = "StatusChangeTimestamp"
        case originalEntryTimestamp = "OriginalEntryTimestamp"
        case priceChangeTimestamp = "PriceChangeTimestamp"
        case listPrice = "ListPrice"
        case closePrice = "ClosePrice"
        case originalListPrice = "OriginalListPrice"
        case previousListPrice = "PreviousListPrice"
        case listPriceLow = "ListPriceLow"
        case propertyType = "PropertyType"
        case propertySubType = "PropertySubType"
        case streetNumber = "StreetNumber"
        case streetNumberNumeric = "StreetNumberNumeric"
        case streetDirPrefix = "StreetDirPrefix"
        case streetName = "StreetName"
        case streetSuffix = "StreetSuffix"
        case streetDirSuffix = "StreetDirSuffix"
        case unitNumber = "UnitNumber"
        case city = "City"
        case stateOrProvince = "StateOrProvince"
        case postalCode = "PostalCode"
        case postalCodePlus4 = "PostalCodePlus4"
        case county = "County"
        case country = "Country"
        case unparsedAddress = "UnparsedAddress"
        case latitude = "Latitude"
        case longitude = "Longitude"
        case bedsTotal = "BedsTotal"
        case bedroomsTotal = "BedroomsTotal"
        case bathroomsTotalDecimal = "BathroomsTotalDecimal"
        case bathroomsTotalInteger = "BathroomsTotalInteger"
        case bathroomsFull = "BathroomsFull"
        case bathroomsHalf = "BathroomsHalf"
        case bathroomsThreeQuarter = "BathroomsThreeQuarter"
        case bathroomsPartial = "BathroomsPartial"
        case roomsTotal = "RoomsTotal"
        case storiesTotal = "StoriesTotal"
        case levelsOrStories = "LevelsOrStories"
        case livingArea = "LivingArea"
        case livingAreaUnits = "LivingAreaUnits"
        case aboveGradeFinishedArea = "AboveGradeFinishedArea"
        case aboveGradeFinishedAreaUnits = "AboveGradeFinishedAreaUnits"
        case belowGradeFinishedArea = "BelowGradeFinishedArea"
        case belowGradeFinishedAreaUnits = "BelowGradeFinishedAreaUnits"
        case belowGradeUnfinishedArea = "BelowGradeUnfinishedArea"
        case buildingAreaTotal = "BuildingAreaTotal"
        case buildingAreaUnits = "BuildingAreaUnits"
        case lotSizeAcres = "LotSizeAcres"
        case lotSizeSquareFeet = "LotSizeSquareFeet"
        case lotSizeArea = "LotSizeArea"
        case lotSizeUnits = "LotSizeUnits"
        case lotFeatures = "LotFeatures"
        case frontageLength = "FrontageLength"
        case frontageType = "FrontageType"
        case yearBuilt = "YearBuilt"
        case yearBuiltEffective = "YearBuiltEffective"
        case yearBuiltDetails = "YearBuiltDetails"
        case garageSpaces = "GarageSpaces"
        case garageYN = "GarageYN"
        case attachedGarageYN = "AttachedGarageYN"
        case openParkingSpaces = "OpenParkingSpaces"
        case parkingTotal = "ParkingTotal"
        case parkingFeatures = "ParkingFeatures"
        case interiorFeatures = "InteriorFeatures"
        case appliances = "Appliances"
        case flooring = "Flooring"
        case fireplacesTotal = "FireplacesTotal"
        case fireplaceYN = "FireplaceYN"
        case fireplaceFeatures = "FireplaceFeatures"
        case laundryFeatures = "LaundryFeatures"
        case basementYN = "BasementYN"
        case basement = "Basement"
        case exteriorFeatures = "ExteriorFeatures"
        case architecturalStyle = "ArchitecturalStyle"
        case constructionMaterials = "ConstructionMaterials"
        case roofMaterials = "RoofMaterials" // Note: DD uses "Roof" but some MLSs use "RoofMaterials"
        case foundationDetails = "FoundationDetails"
        case fencing = "Fencing"
        case otherStructures = "OtherStructures"
        case heating = "Heating"
        case heatingYN = "HeatingYN"
        case cooling = "Cooling"
        case coolingYN = "CoolingYN"
        case utilities = "Utilities"
        case sewer = "Sewer"
        case waterSource = "WaterSource"
        case electricExpense = "ElectricExpense"
        case waterSewerExpense = "WaterSewerExpense"
        case poolYN = "PoolYN"
        case poolFeatures = "PoolFeatures"
        case spaYN = "SpaYN"
        case spaFeatures = "SpaFeatures"
        case associationYN = "AssociationYN"
        case associationName = "AssociationName"
        case associationFee = "AssociationFee"
        case associationFeeFrequency = "AssociationFeeFrequency"
        case associationFee2 = "AssociationFee2"
        case associationFee2Frequency = "AssociationFee2Frequency"
        case associationAmenities = "AssociationAmenities"
        case elementarySchool = "ElementarySchool"
        case elementarySchoolDistrict = "ElementarySchoolDistrict"
        case middleOrJuniorSchool = "MiddleOrJuniorSchool"
        case middleOrJuniorSchoolDistrict = "MiddleOrJuniorSchoolDistrict"
        case highSchool = "HighSchool"
        case highSchoolDistrict = "HighSchoolDistrict"
        case communityFeatures = "CommunityFeatures"
        case seniorsOnlyHousing = "SeniorCommunityYN"
        case view = "View"
        case viewYN = "ViewYN"
        case waterfrontYN = "WaterfrontYN"
        case waterfrontFeatures = "WaterfrontFeatures"
        case waterBodyName = "WaterBodyName"
        case listAgentKey = "ListAgentKey"
        case listAgentKeyNumeric = "ListAgentKeyNumeric"
        case listAgentMlsId = "ListAgentMlsId"
        case listAgentFullName = "ListAgentFullName"
        case listAgentFirstName = "ListAgentFirstName"
        case listAgentLastName = "ListAgentLastName"
        case listAgentEmail = "ListAgentEmail"
        case listAgentDirectPhone = "ListAgentDirectPhone"
        case listOfficeKey = "ListOfficeKey"
        case listOfficeKeyNumeric = "ListOfficeKeyNumeric"
        case listOfficeMlsId = "ListOfficeMlsId"
        case listOfficeName = "ListOfficeName"
        case listOfficePhone = "ListOfficePhone"
        case buyerAgentKey = "BuyerAgentKey"
        case buyerAgentFullName = "BuyerAgentFullName"
        case buyerAgentEmail = "BuyerAgentEmail"
        case buyerOfficeKey = "BuyerOfficeKey"
        case buyerOfficeName = "BuyerOfficeName"
        case coListAgentKey = "CoListAgentKey"
        case coListAgentFullName = "CoListAgentFullName"
        case coListOfficeKey = "CoListOfficeKey"
        case coListOfficeName = "CoListOfficeName"
        case showingInstructions = "ShowingInstructions"
        case lockBoxType = "LockBoxType"
        case lockBoxSerialNumber = "LockBoxSerialNumber"
        case showingContactType = "ShowingContactType"
        case showingContactName = "ShowingContactName"
        case showingContactPhone = "ShowingContactPhone"
        case publicRemarks = "PublicRemarks"
        case privateRemarks = "PrivateRemarks"
        case syndicationRemarks = "SyndicationRemarks"
        case internetAddressDisplayYN = "InternetAddressDisplayYN"
        case internetEntireListingDisplayYN = "InternetEntireListingDisplayYN"
        case internetConsumerCommentYN = "InternetConsumerCommentYN"
        case internetAutomatedValuationDisplayYN = "InternetAutomatedValuationDisplayYN"
        case daysOnMarket = "DaysOnMarket"
        case cumulativeDaysOnMarket = "CumulativeDaysOnMarket"
        case taxAnnualAmount = "TaxAnnualAmount"
        case taxYear = "TaxYear"
        case taxLegalDescription = "TaxLegalDescription"
        case taxParcelNumber = "TaxParcelNumber"
        case taxBookNumber = "TaxBookNumber"
        case greenBuildingVerificationType = "GreenBuildingVerificationType"
        case greenEnergyEfficient = "GreenEnergyEfficient"
        case greenEnergyGeneration = "GreenEnergyGeneration"
        case greenIndoorAirQuality = "GreenIndoorAirQuality"
        case greenSustainability = "GreenSustainability"
        case greenWaterConservation = "GreenWaterConservation"
        case photosCount = "PhotosCount"
        case photosChangeTimestamp = "PhotosChangeTimestamp"
        case videosCount = "VideosCount"
        case openHouseCount = "OpenHouseCount"
        case subdivisionName = "SubdivisionName"
        case mapCoordinate = "MapCoordinate"
        case directions = "Directions"
        case crossStreet = "CrossStreet"
        case ownerName = "OwnerName"
        case ownerPhone = "OwnerPhone"
        case occupantType = "OccupantType"
        case occupantName = "OccupantName"
        case tenantPays = "TenantPays"
        case originatingSystemName = "OriginatingSystemName"
        case originatingSystemKey = "OriginatingSystemKey"
        case sourceSystemName = "SourceSystemName"
        case sourceSystemKey = "SourceSystemKey"
    }
}
