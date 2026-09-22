# What goes in the warehouse

For review. This is the full list of what would be copied out of the
source databases, and what deliberately would not.

**Phase 1 total: about 3.8 GB**, against 80 GB across the two sources.
A 32 GB database allocation leaves years of headroom.

---

## Phase 1 — the spine

Enough for lead volumes, the funnel, affiliate performance and
conversion. Seven tables.

| Source | Table | Mode | Cols | Rows | Est. size |
|---|---|---|---|---|---|
| `Overflow` | `LeadApplications` | snapshot | 19 of 36 | 4.44M | 2,109 MB |
| `OverflowReporting` | `LeadApplicationStages` | incremental | 5 of 5 | 19.76M | 1,447 MB |
| `Overflow` | `Leads` | snapshot | 4 of 9 | 780k | 181 MB |
| `Overflow` | `LeadApplicationAccepts` | snapshot | 6 of 7 | 1.27M | 139 MB |
| `Overflow` | `Affiliates` | snapshot | 18 of 40 | 185 | <1 MB |
| `Overflow` | `Lenders` | snapshot | 15 of 23 | 47 | <1 MB |
| `Overflow` | `AffiliateGroups` | snapshot | 10 of 14 | 34 | <1 MB |

Sizes include index overhead, estimated at 60%.

**Mode** matters for load cost:

- **incremental** — reads only rows added since the last run, using the
  table's auto-incrementing key. A clustered index seek, so it costs the
  same against 19.76M rows as against 1M. Used wherever the key allows.
- **snapshot** — replaced in full each run. Used where the key is a GUID
  (no usable watermark) or the table is small and changes in place.

### Columns taken

**`LeadApplications`** — 19 of 36
`Id`, `LeadId`, `AffiliateId`, `ReferenceId`, `StateCode`, `PostCode`,
`ResidentialStatus`, `EmploymentStatus`, `EmploymentDuration`,
`PaymentFrequency`, `MonthlyIncome`, `LoanAmount`, `LoanPurpose`,
`SecuredLoans`, `MarketingConsent`, `TermsAgreed`,
`IsAustralianResident`, `AntiHawking`, `DateCreated`

**`Leads`** — 4 of 9
`Id`, `DateCreated`, `City`, `StateCode`

**`LeadApplicationStages`** — all 5
`Id`, `LeadApplicationId`, `AffiliateId`, `StageId`, `DateCreated`

**`LeadApplicationAccepts`** — 6 of 7
`Id`, `LeadId`, `AffiliateId`, `LeadApplicationId`, `LenderTierId`,
`DateCreated`

**`Affiliates`** — 18 of 40
`Id`, `AffiliateGroupId`, `Name`, `DisplayName`, `Commission`,
`CommissionTypeId`, `PingTreeId`, `JourneyVersion`,
`IsImmediateResponse`, `MarketingEnabled`, `IsInternalSource`,
`MinimumLoanAmount`, `DefaultLoanAmount`, `SentLimit`, `IsActive`,
`IsDeleted`, `DateCreated`, `DateModified`

**`Lenders`** — 15 of 23
`Id`, `Name`, `XeroName`, `SentLimit`, `DeclineLimit`, `BsType`,
`BankstatementAlias`, `ShowTaleFinScore`, `PaymentCycle`,
`LastBillingDate`, `NextBillingDate`, `IsActive`, `IsDeleted`,
`DateCreated`, `DateModified`

**`AffiliateGroups`** — 10 of 14
`Id`, `Name`, `XeroName`, `PaymentCycle`, `LastPaymentDate`,
`NextPaymentDate`, `IsActive`, `IsDeleted`, `DateCreated`,
`DateModified`

---

## What is deliberately not copied

### Banking detail — never, with no override

- `BankStatementSummaries.AccountNumber`
- `BankStatementSummaries.SortCode`

The extract tooling refuses to accept these or any similarly-named
column. It is not a configuration setting that could be changed by
mistake: a run naming one stops before connecting to anything.

### Credentials found in the source

`Overflow.Affiliates` holds `Password`, `CredfinSecretKey`,
`TalefinClientSecret` and `CredfinIdentifier`. Same treatment — blocked
outright.

### Personal identifiers — allowed but not used

Names, email, mobile, date of birth, driver's licence and street
address can be extracted only when a spec names the column explicitly
for review. **Nothing in the current configuration does**, and an
automated test fails the build if that changes without being noticed.

So `dim.Lead` holds a lead's state, city and creation date, and nothing
that identifies a person.

### Other exclusions, for quality rather than privacy

- `LeadApplications.MoveInDate` — holds values up to 5687-12-01
- `LeadApplications.DateOfBirth` — 1753 sentinels and future dates; any
  age calculation from it would be wrong
- `LeadApplications.EmployerName`, `JobTitle` — free text that can
  identify a person
- `LeadApplicationAccepts.Url` — query strings can carry personal data

---

## Phase 2 — likely, not yet configured

Added when someone asks a question that needs them.

| Source | Table | Rows | Why |
|---|---|---|---|
| `OverflowReporting` | `LeadMetrics` | 1.62M | 82 affordability and risk indicators, no identifiers. The richest analytical material available, and among the safest. Note: starts 2023-01-24 |
| `OverflowReporting` | `LeadRedirects` | 2.87M | Journey drop-off analysis |
| `OverflowReporting` | `LeadFundedStatuses` | 1.27M | One row per accept. Statuses are created but almost never transition, so of limited value while lenders do not report back |
| `Overflow` | `SellHistory` | 592k | Only starts 2025-02-20 |
| `OverflowReporting` | `ApiErrors` | 4.01M | Operational rather than commercial |

Adding roughly 1–2 GB.

## Aggregated on extract, not copied row for row

| Table | Rows | Size | Treatment |
|---|---|---|---|
| `FailedFiltersV2` | 194.1M | 26.9 GB | Daily counts by filter and lender |
| `LenderApplicationResults` | 98.9M | 20.7 GB | Daily counts by lender and outcome |

These two are 60% of both source databases. The questions they answer —
why leads fail, how lenders respond — are asked at daily-count level, so
copying them row for row would multiply the storage bill to support a
query nobody writes. Raw extraction stays possible if a genuine case
appears.

---

## Growth

History runs from 2021-07-01, so roughly 5.2 years is already in the
figures above. Current rate of accumulation:

- `LeadApplicationStages` ≈ 3.8M rows/year ≈ 290 MB/year
- `LeadApplications` ≈ 850k rows/year ≈ 420 MB/year
- everything else ≈ 100 MB/year

**About 800 MB a year.** A 32 GB allocation is not a constraint in any
realistic timeframe, and the tier can be raised without downtime.

---

## Load pattern

- Incremental tables read only what is new, so a run after the first is
  cheap regardless of history.
- The first load of `LeadApplicationStages` reads all 19.76M rows once.
- Snapshot tables are replaced whole each run; the largest is
  `LeadApplications` at 4.44M rows.
- Hourly is affordable. Daily off-peak is likely enough, given the
  source itself runs about two minutes behind live and nothing here is
  real-time.

## Access required

- **Source:** `db_datareader` on `Overflow` and `OverflowReporting`.
  Read-only, which is what the current login already has.
- **Warehouse:** read/write plus `ALTER` on the `stg` schema, because
  snapshot loads use `TRUNCATE TABLE`.

No write access to the source is needed or wanted.
