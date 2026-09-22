# Bank statement data: what is stored

Answers one question — does either database hold the 90 days of
retrieved bank statement data, and at what grain?

Measured 2026-09-22 from `discovery/adhoc/bankstatement_inventory.sql`
run against both databases. Metadata only: row counts from
`sys.partitions`, column names from `sys.columns`. No column value was
read.

---

## 1. The raw transactions are not stored. The derived metrics are.

There is no transaction-level table anywhere. Every row in `Overflow` is
now accounted for:

| Table | Rows | Size |
|---|---|---|
| `LeadApplicationCustomValues` | 67,841,415 | 2.8 GB |
| `LenderApplicationResults` | 31,185,133 | 2.8 GB |
| `CredfinStatus` | 5,211,330 | 1.0 GB |
| `LeadApplications` | 4,445,486 | 2.9 GB |
| `BankStatementSummaries` | 3,909,457 | **4.4 GB** |
| `CredfinLeadStatus` | 1,704,612 | 98 MB |
| `LeadApplicationAccepts` | 1,270,414 | 649 MB |
| `Leads` | 780,850 | 225 MB |
| `LeadScores` | 604,665 | 93 MB |
| `SellHistory` | 592,538 | 43 MB |
| `FundedLeads` | 146,000 | 15 MB |
| 19 smaller tables | ~12,000 | <5 MB |

That totals ~117.7M rows, matching the database's own figure. The 111M
rows previously unaccounted for are `LeadApplicationCustomValues` and
`LenderApplicationResults`, not a transaction ledger.

**This is a good outcome.** The most sensitive material — individual
transaction lines, which reveal health, gambling, relationships and
religion — was never retained. What was kept is the analysis of it.

## 2. Two overlapping affordability tables, not one

`BankStatementSummaries` is far more than the account summary the earlier
findings implied. It is the largest table in `Overflow` by storage.

| | `BankStatementSummaries` | `LeadMetrics` |
|---|---|---|
| Database | `Overflow` | `OverflowReporting` |
| Rows | 3,909,457 | 1,617,269 |
| Size | 4.4 GB | 795 MB |
| Columns | 92 | 82 |
| Grain | per **bank account** | per **application** |
| History | not yet measured | from 2023-01-24 |
| Direct identifiers | `AccountNumber`, `SortCode`, `AccountName`, `Employer` | none |
| Lender outcome | no | `Result`, `LenderTierName` |
| Join keys | `LeadId`, `LeadApplicationId`, `AffiliateId` | `LeadApplicationId`, `AffiliateId` |

They overlap heavily — `GamblingPercentageOfIncome`, `DaysSinceLastSalary`,
`IsInsolvencyExists`, `CentrelinkAsPercentIncomeECD` and others appear in
both. The 2.4:1 row ratio is consistent with roughly two accounts per
applicant.

`LeadMetrics` is the better reporting source: one row per application, no
identifiers to strip, and it already carries the lender outcome. Its only
disadvantage is starting in 2023.

## 3. Shortlist — 22 of the 82 `LeadMetrics` columns

Enough for lead quality and ideal-customer work without taking the rest.

**Keys and context**
`LeadApplicationId`, `AffiliateId`, `DateCreated`

**Applicant**
`Age`, `EmploymentStatus`, `TimeAtEmployer`, `LoanAmount`

**Income**
`TotalMonthlyIncome`, `WagesMonthly`, `CentrelinkAsPercentOfIncome`,
`DaysSinceLastSalary`, `IsLatestIncomePresent`

**Risk**
`GamblingPercentageOfIncome`,
`ConfirmedAndInferredGamblingAsPercentageOfIncome`, `Dishonours`,
`SaccDishonours`, `NumberofDishonours30Days`, `EstimatedActiveSACCLoans`,
`NumberofSaccProviders`, `IsInsolvencyExists`,
`DebtAsPercentageOfIncomeExcludingCashDeposits90Days`,
`IncomeSpentOnDayOfDeposit`

**Outcome**
`Result`, `LenderTierName`

State and postcode are not in `LeadMetrics`; they come from
`LeadApplications` on `LeadApplicationId`.

## 4. Three things worth knowing that were not being looked for

**Retrieval cost is recorded.** `OverflowReporting.BankStatementRetrievals`
holds 1,705,958 rows with `Cost`, `AffiliateId`, `DateCreated` and
`IsSecondTry`. Cost per retrieval by affiliate is directly answerable,
which turns lead quality from a conversion question into a margin one.

**Repeat applicants need no pseudonym scheme.** `Leads` (780,850) against
`LeadApplications` (4,445,486) is ~5.7 applications per lead, and
`LeadId` is on both `LeadApplications` and `BankStatementSummaries`.
Applications can be grouped per person using a key the system already
has, so the salted-hash design proposed earlier is unnecessary.

**Credfin, not Talefin, is the retrieval path.** `CredfinStatus` (5.2M)
and `CredfinLeadStatus` (1.7M) exist; there is no Talefin table. With
`CredfinLanded` on 47% of applications and `Affiliates.TalefinClientSecret`
also present, the likeliest reading is Credfin retrieves and Talefin
scores — but that is inference and needs confirming.

## 5. Personal data

Blocked, unchanged: `BankStatementSummaries.AccountNumber` and
`.SortCode`.

Newly identified:

- `BankStatementSummaries.AccountName` and `.Employer` — personal
  identifiers, restricted tier
- `BankstatementEmailQueue.Email` — a direct identifier in
  `OverflowReporting`, which the earlier finding that the reporting
  database holds no sensitive columns did not catch

`LeadMetrics` carries no direct identifiers in any of its 82 columns.

## 6. Open

1. **What is `LeadApplicationCustomValues`?** 67.8M rows, 58% of
   `Overflow`, never examined. Likely affiliate-supplied custom fields,
   which means it could contain anything, including personal data.
2. **How far back does `BankStatementSummaries` go?** If 2021, it is the
   only long-run affordability history, since `LeadMetrics` starts 2023.
3. **What is `LeadMetrics.Result`?** If it is the lender decision, lender
   acceptance by customer type is answerable from one table.
4. **Why does `LenderApplicationResults` differ between databases** —
   31.2M in `Overflow`, 98.9M in `OverflowReporting`?
5. **`LeadScores` (604,665 rows)** — not examined.

---

# Follow-up: the two open tables

Measured 2026-09-22 from `discovery/adhoc/custom_values_and_history.sql`.

## 7. `BankStatementSummaries` effectively starts March 2022

| Year | Rows | Range |
|---|---|---|
| 2021 | 875 | 01 Jul – 02 Dec |
| 2022 | 415,093 | 09 Mar – 31 Dec |
| 2023 | **1,456,983** | full year |
| 2024 | 806,871 | full year |
| 2025 | 747,869 | full year |
| 2026 | 481,792 | to 22 Sep |

The 875 rows in 2021 are a pilot, not history. Usable affordability data
runs from **March 2022** — about ten months more than `LeadMetrics`,
which starts 2023-01-24.

Rows are per bank account. Against `LeadMetrics` over the same period
(3,493,515 against 1,617,269) that is 2.16 accounts per application,
which supports reading one as per-account and the other as
per-application.

**Volume has fallen by more than half since 2023.** 1.46M in 2023, 807k
in 2024, 748k in 2025, and 2026 is tracking about 663k annualised — 55%
below the peak. This is bank statement retrievals, not applications, so
it could be a change in how many accounts are pulled per applicant rather
than a fall in business. Worth checking against application volume before
anyone reads it as a trend, but the 45% drop from 2023 to 2024 is too
large to be account-count drift alone.

## 8. `LeadApplicationCustomValues` is a key/value store

`Id`, `LeadApplicationId`, `AffiliateId`, `Name` (nvarchar 200),
`Value` (nvarchar(max)), `DateCreated`.

Affiliate-supplied custom fields, one row per field per application.
`Value` being unbounded text means it can hold anything an affiliate
chose to send, personal data included, so it stays unread until the field
names are known. `custom_value_names.sql` counts the names without
touching the values.

## 9. `LenderApplicationResults` differs between the two databases

| | `Overflow` | `OverflowReporting` |
|---|---|---|
| Rows | 31,185,133 | 98,939,438 |
| Columns | 9 | 13 |
| Extra | — | `AffiliateName`, `LenderTierName`, `LoanAmount`, **`Email`** |

The reporting copy is denormalised and holds 3.2× the history, so it is
the one to use — but it carries `Email`, a direct identifier.

**Both carry `Cost`.** Combined with
`BankStatementRetrievals.Cost` (1.7M rows), the cost side of every lead
is recorded: what it cost to pull statements and what it cost to submit
to each lender. Lead quality can be measured as margin, not just
conversion rate. Nothing in the reporting built so far uses this.

## 10. A risk model already exists

`Overflow.LeadScores` — 604,665 rows.

`Score`, `RiskGrade`, `ModelId`, `ScoreId`, `ScoreDateTime`,
`TotalRequestTime`. A third-party scoring service, with response time
recorded per call.

| Year | Scores |
|---|---|
| 2024 | 46,999 |
| 2025 | 342,010 |
| 2026 | 215,668 |

Coverage is partial — 604k scores against 4.44M applications, and nothing
before 2024 — so it cannot carry a long-run trend. But an existing
`RiskGrade` is a ready-made benchmark to test any new customer model
against, and `TotalRequestTime` makes the provider's latency measurable.

## 11. `SellHistory` and `FailedFiltersLookup`

`SellHistory` (592,538 rows, from 2025-02-20): `LeadId`,
`LeadApplicationId`, `AffiliateId`, `LenderId`, `LenderTierId`,
`DateCreated`. No personal data. A third candidate for "sold", alongside
`LeadApplicationAccepts` (1.27M) and the `Offer` stage (356k) — three
different numbers for what looks like one event, which is the
accept-definition question still outstanding.

`FailedFiltersLookup` (1,347 rows): `Id`, `Message`. The decode table for
`FailedFiltersV2`'s 194M rows. Tiny, safe, and the only way to report on
why leads fail.

## 12. Personal data, further amended

A third direct identifier in `OverflowReporting`:
`LenderApplicationResults.Email`, on 98.9M rows.

That database was originally recorded as holding no sensitive columns.
It holds at least two email columns. The name-based scan in `06` looked
for banking terms and identity words on tables whose names suggested
personal data, and did not flag an email column on an event table.

**The lesson is about method, not these two columns.** Name-based
classification finds what it is told to look for. Any table going into
reporting needs its columns read individually, which is what these
follow-up queries have been doing.

---

# `LeadApplicationCustomValues` is marketing attribution

Measured 2026-09-22 from `discovery/adhoc/custom_value_names.sql`.
31 distinct field names, 22,614,058 rows. Field names only; no value was
read.

## 13. What is in it

**Web session and campaign** — 26 affiliates, ~1.91M rows each, from
March 2022 (`UserAgent` from November 2021):

`UtmSource`, `UtmMedium`, `UtmCampaign`, `OverflowSource`, `CookieId`,
`UserAgent`

**Google Ads ValueTrack** — 4 to 6 affiliates, from July 2022:

`gclid`, `kw` (keyword), `crtv` (creative), `adgrp`, `cmpid`, `mt` (match
type), `nw` (network), `dev` and `devmod` (device), `trgt`, `locms`
(location), `plc`

**Facebook** — `fbp`, 1.10M rows, 9 affiliates

**Partner and lender references** — `MoneyspotLeadKey` (233,848 rows
across 56 affiliates, the widest-used field in the table),
`CredfinApplicationId` (441,905), `2eziLeadId`, `Credit24ApplicationId`,
`QuickzyLeadId`, `StayFinanceLeadId`, `FasterFinancialLeadId`,
`LenderCode`

**Business flags** — `InExcessiveDebt` (210,635, 20 affiliates, from
March 2022), `IsDirectDeposit` (181,204, 15 affiliates, from November
2025), `min_price`

## 14. Why this matters

Campaign attribution can be joined to affordability metrics and lender
outcomes on `LeadApplicationId`. That makes the real question answerable:
not just which affiliates send good leads, but **which campaign, keyword
and creative produce applicants who pass affordability and get accepted**.
Nothing built so far has had a channel dimension at all.

**Coverage is the limit.** 1.91M applications carry UTM data against
4,445,486 total — 43%, and only 26 of 185 affiliates. The Google Ads
fields cover 4 to 6 affiliates, almost certainly the internal sources
(`Affiliates.IsInternalSource` exists). So this supports channel analysis
of your own marketing, not of affiliate traffic.

**`MoneyspotLeadKey` may matter more than it looks.** A lender-side
reference on 233,848 applications across 56 affiliates is a way to
reconcile against a lender's own records — which is the problem that
killed funded-data reporting when `FundedLeads` froze in October 2024.
Worth raising with whoever manages that relationship.

## 15. Personal data in this table

`CookieId`, `fbp`, `gclid` and `UserAgent` are online identifiers. They
are not names, but they identify a device or a browser and are treated as
personal information under Australian privacy guidance. They belong in
the restricted tier.

None of them are needed for analysis. `UtmSource`, `UtmMedium`,
`UtmCampaign`, `kw`, `crtv` and `adgrp` describe the campaign, not the
person, and answer every channel question worth asking. Take those and
leave the tracking identifiers where they are.

## 16. A bug worth reporting

**43,240 rows have an empty field name**, all from one affiliate, written
continuously since 14 May 2025. Something is writing custom values with
no name attached. Whatever that field was meant to be is being lost, and
it is still happening.

## 17. Correction: the row counts were inflated

`LeadApplicationCustomValues` holds **22,614,058 rows, not 67,841,415**.
The count in section 1 was exactly 3x too high.

The inventory query counted rows while joined to
`sys.allocation_units`. A table gets one allocation unit per storage
type, so a table with an `nvarchar(max)` column has three — `IN_ROW_DATA`,
`LOB_DATA` and `ROW_OVERFLOW_DATA` — and its row count is tripled.

`01_tables_and_views.sql` contained the same join, so **every row count
in the original discovery has this flaw**, and any table with a large
object column reads high. Both scripts are now fixed: rows come from
`sys.partitions` alone, sizes still come from the allocation units.

Confirmed unaffected: `BankStatementSummaries`, whose per-year counts sum
to 3,909,483 against a reported 3,909,457. Tables with no large object
column have a single allocation unit and were always right.

### Scope, now measured

The fixed query was re-run against `OverflowReporting` on 2026-09-22.
**Only three tables moved, all of them small:**

| Table | Was | Is |
|---|---|---|
| `SystemAlerts` | 18,507 | 6,169 |
| `OverflowUsers` | 312 | 104 |
| `OfflineOffers` | 18 | 6 |

Every other count changed only by the growth expected between two runs.
The headline figures I was most worried about are **unaffected**:
`FailedFiltersV2` at 194.3M, `LenderApplicationResults` at 98.9M,
`FailedFilters` at 37.0M and `LeadApplicationStages` at 19.77M all hold.
They have no large object columns, so they always had one allocation unit
and were always right.

So the flaw was real but narrow: it only ever touched tables with an
unbounded text column. Everything the architecture and sizing work was
based on stands.

`Overflow` has not yet been re-measured. `CredfinStatus` has a
`RefreshInputs nvarchar(max)` column and is expected to fall from 5.21M
to roughly 1.74M.

**`Overflow` is therefore materially smaller than reported.** Correcting
this one table alone removes 45.2M rows from the total.
