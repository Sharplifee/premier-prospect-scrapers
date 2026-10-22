// RESO Data Dictionary 2.0 – Media Resource
// Photos, virtual tours, floor plans, and documents attached to a listing.

import Foundation

enum MediaCategory: String, Codable {
    case photo = "Photo"
    case video = "Video"
    case virtualTour = "Virtual Tour"
    case floorPlan = "Floor Plan"
    case document = "Document"
    case threeD = "3D Tour"
    case aerialPhoto = "Aerial Photo"
    case plat = "Plat"
}

struct RESOMedia: Codable, Identifiable {
    var id: String { mediaKey }

    let mediaKey: String
    let mediaKeyNumeric: Int?
    let listingKey: String?
    let listingKeyNumeric: Int?
    let mediaCategory: MediaCategory?
    let mediaType: String?
    let mediaURL: URL?
    let mediaThumbnailURL: URL?
    let order: Int?
    let imageWidth: Int?
    let imageHeight: Int?
    let imageSizeDescription: String?
    let mediaObjectID: String?
    let shortDescription: String?
    let longDescription: String?
    let mediaStatus: String?
    let modificationTimestamp: Date?
    let resourceName: String?

    enum CodingKeys: String, CodingKey {
        case mediaKey = "MediaKey"
        case mediaKeyNumeric = "MediaKeyNumeric"
        case listingKey = "ListingKey"
        case listingKeyNumeric = "ListingKeyNumeric"
        case mediaCategory = "MediaCategory"
        case mediaType = "MediaType"
        case mediaURL = "MediaURL"
        case mediaThumbnailURL = "MediaThumbnailURL"
        case order = "Order"
        case imageWidth = "ImageWidth"
        case imageHeight = "ImageHeight"
        case imageSizeDescription = "ImageSizeDescription"
        case mediaObjectID = "MediaObjectID"
        case shortDescription = "ShortDescription"
        case longDescription = "LongDescription"
        case mediaStatus = "MediaStatus"
        case modificationTimestamp = "ModificationTimestamp"
        case resourceName = "ResourceName"
    }
}
