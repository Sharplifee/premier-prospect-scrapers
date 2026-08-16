# UtahRealEstate.com (WFRMLS) – RESO Web API Integration Proposal
**Premier Prospect | Real Estate Intelligence Platform**
**Prepared for: WFRMLS Data Licensing Review**
**Date: June 2026**

---

## 1. Executive Summary

Premier Prospect is a licensed real estate intelligence platform serving licensed agents and brokers operating within WFRMLS coverage areas. This document describes our proposed technical integration with the UtahRealEstate.com RESO Web API and requests authorization for IDX and/or data services access under a formal licensing agreement.

Our integration is built entirely on published RESO Data Dictionary 2.0 standards and OData v4.0. No non-standard field access is requested. All data handling will comply with WFRMLS Rules and Regulations, IDX rules, and applicable NAR policies.

---

## 2. About UtahRealEstate.com / WFRMLS

| Item | Detail |
|------|--------|
| MLS Name | Wasatch Front Regional Multiple Listing Service (WFRMLS) |
| Operator | UtahRealEstate.com |
| Coverage | ~96% of all REALTORS® in Utah |
| Vendor Portal | https://vendor.utahrealestate.com |
| API Docs | https://vendor.utahrealestate.com/webapi/docs |
| Certification | RESO Web API Certified + RESO Data Dictionary Certified |
| Protocol | OData v4.0 over HTTPS |
| Auth Standard | OAuth2 / OpenID Connect (Bearer Token delivery) |

---

## 3. API Architecture – RESO Web API (OData v4.0)

### 3.1 Base Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /reso/odata` | GET | Service document – lists available resources |
| `GET /reso/odata/$metadata` | GET | OData metadata document (all fields + types) |
| `GET /reso/odata/Property` | GET | Listing data (primary resource) |
| `GET /reso/odata/Property('{ListingKey}')` | GET | Single listing by key |
| `GET /reso/odata/Media` | GET | Photos, videos, floor plans |
| `GET /reso/odata/Member` | GET | Agent/member data |
| `GET /reso/odata/Office` | GET | Brokerage/office data |
| `GET /reso/odata/OpenHouse` | GET | Open house schedules |
| `GET /reso/odata/Lookup` | GET | Enumeration values (e.g. MlsStatus → StandardStatus mapping) |
| `GET /reso/odata/DataSystem` | GET | MLS system metadata |
| `GET /reso/odata/HistoryTransactional` | GET | Transaction history (if licensed) |

### 3.2 Authentication Flow

WFRMLS uses Bearer Token authentication. Tokens are issued after licensing agreement execution:

```
# Step 1 – Resource Owner Password Credentials (for server-to-server)
POST /oauth2/token
Content-Type: application/x-www-form-urlencoded

grant_type=password
&client_id={client_id}
&client_secret={client_secret}
&username={mls_username}
&password={mls_password}

# Response
{
  "access_token": "eyJ...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "refresh_token": "..."
}

# Step 2 – Use token on all requests
GET /reso/odata/Property
Authorization: Bearer {access_token}
Accept: application/json
```

> **Note:** Per vendor.utahrealestate.com docs, WFRMLS also supports simplified Bearer Token delivery where the token is issued directly via the vendor dashboard (Account Summary page). This is the recommended approach for production integrations.

### 3.3 OData Query Parameters

| Parameter | Example | Purpose |
|-----------|---------|---------|
| `$filter` | `StandardStatus eq Odata.Models.StandardStatus'Active'` | Filter results |
| `$select` | `$select=ListingKey,ListPrice,City` | Return only specified fields |
| `$orderby` | `$orderby=ModificationTimestamp desc` | Sort results |
| `$top` | `$top=200` | Page size (max typically 200) |
| `$skip` | `$skip=200` | Offset for pagination |
| `$expand` | `$expand=Media` | Expand related resources inline |
| `$count` | `$count=true` | Include total record count |

**Important:** Enumerated values must use fully-qualified OData enum syntax:
```
# CORRECT
$filter=StandardStatus eq Odata.Models.StandardStatus'Active'

# INCORRECT (returns 400)
$filter=StandardStatus eq 'Active'
```

### 3.4 Incremental Replication Pattern

RESO-standard incremental sync uses `ModificationTimestamp`:

```
GET /reso/odata/Property
  ?$filter=ModificationTimestamp gt 2026-06-01T00:00:00-06:00
  &$orderby=ModificationTimestamp asc
  &$top=200
Authorization: Bearer {token}
```

Follow `@odata.nextLink` in the response to paginate. Store the highest `ModificationTimestamp` seen as the checkpoint for the next run.

---

## 4. RESO Data Dictionary 2.0 – Field Reference

### 4.1 Property Resource – Key Fields

#### Identifiers
| RESO Field | Type | Notes |
|------------|------|-------|
| `ListingKey` | String | Primary key for the listing record |
| `ListingKeyNumeric` | Integer | Numeric form of ListingKey |
| `ListingId` | String | MLS-visible listing number (e.g. "2089421") |
| `MlsStatus` | String | Local MLS status (e.g. "Active - Under Contract") |
| `StandardStatus` | Enum | RESO standard status (see 4.2) |
| `OriginatingSystemName` | String | Source MLS name |

#### Timestamps
| RESO Field | Type | Notes |
|------------|------|-------|
| `ModificationTimestamp` | DateTime | Used for incremental replication |
| `OriginalEntryTimestamp` | DateTime | When first entered into MLS |
| `StatusChangeTimestamp` | DateTime | When status last changed |
| `OnMarketDate` | Date | Date first marketed |
| `CloseDate` | Date | Closing/settlement date |
| `OffMarketDate` | Date | Date removed from market |
| `ExpirationDate` | Date | Listing contract expiration |

#### Price
| RESO Field | Type | Notes |
|------------|------|-------|
| `ListPrice` | Decimal | Current list price |
| `OriginalListPrice` | Decimal | Price when first listed |
| `PreviousListPrice` | Decimal | Price before most recent change |
| `ClosePrice` | Decimal | Sold price |

#### Property Type
| RESO Field | Type | Notes |
|------------|------|-------|
| `PropertyType` | Enum | Residential, Land, Commercial Sale, etc. |
| `PropertySubType` | String | Single Family Residence, Condominium, etc. |

#### Address
| RESO Field | Type | Notes |
|------------|------|-------|
| `UnparsedAddress` | String | Full address as single string |
| `StreetNumber` | String | |
| `StreetName` | String | |
| `StreetSuffix` | String | St, Ave, Dr, etc. |
| `UnitNumber` | String | Apartment/unit number |
| `City` | String | |
| `StateOrProvince` | String | "UT" |
| `PostalCode` | String | 5-digit ZIP |
| `County` | String | Utah County, Salt Lake County, etc. |
| `Latitude` | Double | WGS84 |
| `Longitude` | Double | WGS84 |

#### Structure
| RESO Field | Type | Notes |
|------------|------|-------|
| `BedsTotal` | Integer | Total bedrooms |
| `BathroomsTotalDecimal` | Decimal | e.g. 2.5 |
| `BathroomsFull` | Integer | |
| `BathroomsHalf` | Integer | |
| `LivingArea` | Decimal | Above-grade finished sq ft |
| `LivingAreaUnits` | String | "Square Feet" |
| `BuildingAreaTotal` | Decimal | Total building area |
| `RoomsTotal` | Integer | |
| `StoriesTotal` | Integer | Number of stories |
| `YearBuilt` | Integer | |

#### Lot
| RESO Field | Type | Notes |
|------------|------|-------|
| `LotSizeAcres` | Decimal | |
| `LotSizeSquareFeet` | Decimal | |
| `LotFeatures` | String[] | Multi-select list |

#### Listing Agent/Office
| RESO Field | Type | Notes |
|------------|------|-------|
| `ListAgentKey` | String | Foreign key → Member resource |
| `ListAgentFullName` | String | |
| `ListAgentMlsId` | String | Agent's MLS ID |
| `ListAgentDirectPhone` | String | |
| `ListAgentEmail` | String | |
| `ListOfficeKey` | String | Foreign key → Office resource |
| `ListOfficeName` | String | |
| `ListOfficePhone` | String | |

#### IDX Display Flags
| RESO Field | Type | Notes |
|------------|------|-------|
| `InternetEntireListingDisplayYN` | Boolean | May display on public IDX site |
| `InternetAddressDisplayYN` | Boolean | May display address |
| `InternetConsumerCommentYN` | Boolean | Allow consumer reviews |
| `InternetAutomatedValuationDisplayYN` | Boolean | Allow AVM display |

#### Market Intelligence
| RESO Field | Type | Notes |
|------------|------|-------|
| `DaysOnMarket` | Integer | Current listing period DOM |
| `CumulativeDaysOnMarket` | Integer | Across all listing periods |
| `PriceChangeTimestamp` | DateTime | |
| `PhotosCount` | Integer | Number of photos |
| `OpenHouseCount` | Integer | |

### 4.2 StandardStatus Enumeration Values

| Value | Meaning |
|-------|---------|
| `Active` | Listed and available |
| `Active Under Contract` | Under contract, accepting backup offers |
| `Pending` | Under contract, no longer accepting offers |
| `Closed` | Sold / transaction complete |
| `Expired` | Listing expired without sale |
| `Withdrawn` | Removed by seller before expiration |
| `Hold` | Temporarily off market |
| `Canceled` | Listing canceled |
| `Coming Soon` | Pre-market |
| `Incomplete` | Draft / not yet published |

### 4.3 Media Resource – Key Fields

| RESO Field | Type | Notes |
|------------|------|-------|
| `MediaKey` | String | Primary key |
| `ListingKey` | String | Parent listing |
| `MediaCategory` | Enum | Photo, Video, Virtual Tour, Floor Plan |
| `MediaURL` | URL | Full-resolution image URL |
| `MediaThumbnailURL` | URL | Thumbnail URL |
| `Order` | Integer | Display order (0-based) |
| `ImageWidth` | Integer | Pixels |
| `ImageHeight` | Integer | Pixels |
| `ShortDescription` | String | Caption |
| `ModificationTimestamp` | DateTime | |

---

## 5. Requested Data Access Level

| Product | Feed Type | Fields Requested |
|---------|-----------|-----------------|
| Premier Prospect IDX Search | IDX Feed | All IDX-permitted fields per WFRMLS IDX Rules |
| Signal Intelligence Engine | Broker/Agent Feed | Price changes, DOM, status changes, new listings |
| Prospect Scoring | Broker Back-Office | Full property history, tax fields, DOM analytics |

Premier Prospect will start with the **IDX Feed** to establish compliance and technical validation, then apply for expanded access as the platform scales.

---

## 6. Technical Implementation Summary

### 6.1 Client Architecture

- **Language:** Swift 5.9+ (iOS 17 / macOS 14)
- **Networking:** URLSession (async/await)
- **Protocol:** OData v4.0 over HTTPS
- **Auth:** Bearer Token (stored in Keychain)
- **Pagination:** `@odata.nextLink` traversal
- **Sync Strategy:** Incremental via `ModificationTimestamp`

### 6.2 Data Flow

```
WFRMLS API (/reso/odata/Property)
    │
    ▼ Incremental replication (every 15 min)
Local Sync Engine (ReplicationEngine.swift)
    │
    ▼ Upsert by ListingKey
Supabase (PostgreSQL)
    │
    ├──▶ Premier Prospect iOS App (read-optimized views)
    ├──▶ Signal Scoring Engine (price drops, DOM spikes, status changes)
    └──▶ Agent Dashboard (prospect matching, market alerts)
```

### 6.3 IDX Compliance Controls

- `InternetEntireListingDisplayYN = true` filter applied on all public-facing queries
- Attribution ("Data provided by UtahRealEstate.com") displayed on all listing views
- Sold listings removed from IDX display per WFRMLS IDX rules
- Listing agent contact info displayed on each listing
- No co-mingling of non-WFRMLS data in IDX display

### 6.4 Rate Limiting & Etiquette

- Maximum replication page size: 200 records
- Replication interval: every 15 minutes during business hours
- Off-hours replication: 1 full sweep per hour (midnight–6 AM)
- Retry with exponential backoff on 429 responses
- `Retry-After` header respected

---

## 7. Vendor Registration Steps

1. Register at **https://vendor.utahrealestate.com**
2. Execute the **Data Services Licensing Agreement**
3. Receive `client_id`, `client_secret`, and Bearer Token via vendor dashboard
4. Configure base URL from Account Summary (production endpoint)
5. Run `GET /reso/odata/$metadata` to confirm resource access
6. Begin incremental replication with `ModificationTimestamp` checkpoint

---

## 8. Contact for Licensing

**WFRMLS Data Services**
- Vendor Portal: https://vendor.utahrealestate.com
- Documentation: https://vendor.utahrealestate.com/webapi/docs
- NAR requirement: All NAR-affiliated MLSs must provide RESO Web API access to licensed vendors

**Premier Prospect Contact**
- Platform: Premier Prospect
- Developer: cwsharp23@gmail.com
- Licensed Agent: Yes (Utah)
