# What goes into the reporting build

Decided from the measured inventory in `BANKSTATEMENT-DATA.md` and
`SCHEMA-FINDINGS.md`, on corrected row counts as at 2026-09-22.

Target is a dedicated Overflow reporting application — SQLite on a hosted
volume, fed by a collector running on FLOWWEB4, which is the only machine
with a route to the source server (port 1433 is firewalled; verified).

---

## Tier 1 — the spine

The funnel, affiliates and applicant attributes. Nothing here is new; it
is what the dashboard already needed.

| Source | Table | Rows | Take |
|---|---|---|---|
| `OverflowReporting` | `LeadApplicationStages` | 19,767,220 | all 5 columns |
| `Overflow` | `LeadApplications` | 4,445,535 | 19 of 36 |
| `Overflow` | `Leads` | 780,863 | 4 of 9 |
| `Overflow` | `LeadApplicationAccepts` | 1,270,433 | 6 of 7 |
| `Overflow` | `Affiliates` | 185 | 18 of 40 |
| `Overflow` | `Lenders` | 47 | 15 of 23 |
| `Overflow` | `LenderTiers` | **191** | all |
| `Overflow` | `AffiliateGroups` | 34 | 10 of 14 |

Roughly 1.2 GB in SQLite.

`Leads.Id` is the person-level key — 780,863 leads against 4,445,535
applications, 5.7 each. Repeat-applicant analysis needs no pseudonym
scheme; the system already has one.

## Tier 2 — what makes this worth building

The three things the discovery turned up that nothing previously designed
used.

### Affordability — `LeadMetrics`, 1,617,310 rows

22 of 82 columns, shortlisted in `BANKSTATEMENT-DATA.md` section 3. No
direct identifiers in any of them. Carries `Result` and `LenderTierName`,
so the lender outcome sits alongside the metrics.

Starts 2023-01-24. About 300 MB.

### Attribution — `LeadApplicationCustomValues`, 22,614,090 rows

**Pivot on extract, do not copy.** The table is key/value, one row per
field per application. Pivoted to one row per application with eight
columns it becomes ~1.91M rows and under 200 MB, instead of 22.6M rows of
name/value pairs that every query would have to unpivot again.

Take: `UtmSource`, `UtmMedium`, `UtmCampaign`, `OverflowSource`, `cmpid`,
`crtv`, `adgrp`, `kw`, `mt`, plus `InExcessiveDebt` and `IsDirectDeposit`.

Leave: `CookieId`, `fbp`, `gclid`, `UserAgent`, `dev`, `devmod`, `locms`,
`nw`, `trgt`, `plc`. These identify a device or browser, add nothing to
channel analysis, and are personal information.

Coverage is 43% of applications and 26 of 185 affiliates, so this
describes internal marketing rather than affiliate traffic. Say so on
any page that uses it.

### Cost — two columns, two tables

`OverflowReporting.BankStatementRetrievals` (1,705,973 rows) has `Cost`,
`AffiliateId` and `IsSecondTry`. `LenderApplicationResults` has `Cost` on
every row.

Together these make lead quality a margin question rather than a
conversion rate. Nothing built so far uses either.

### Lender outcomes — `LenderApplicationResults`, 98,941,245 rows

Take the `OverflowReporting` copy: it holds 3.2x the history of the
`Overflow` one (98.9M against 31.2M).

**Row level, six columns**: `LeadApplicationId`, `LenderId`,
`LenderTierId`, `StatusId`, `DateCreated`, `Cost`. Aggregating to daily
counts was the earlier plan and it cannot answer "what kind of customer
does this lender accept", because the customer is lost in the rollup.

Drop `Email` (a direct identifier), and `AffiliateName`, `LenderTierName`
and `LoanAmount`, all derivable by join.

Roughly 4 GB with indexes — the largest single object in the build, and
the one to watch against the 20 GB tripwire.

## Tier 3 — once the above is running

| Source | Table | Rows | Why |
|---|---|---|---|
| `Overflow` | `LeadScores` | 604,682 | An existing third-party `RiskGrade` to benchmark any new model against. Partial coverage, from 2024 |
| `Overflow` | `SellHistory` | 592,557 | Third candidate for "sold". From 2025-02-20 |
| `OverflowReporting` | `FailedFiltersLookup` | 1,347 | Decodes `FailedFiltersV2`. Tiny, and the only way to report why leads fail |
| `OverflowReporting` | `AccountCategorisation` | 1,464,117 | Primary income and next expected income date. From 2025-05-06 |
| `Overflow` | `BankStatementSummaries` | 3,909,498 | Only if the extra ten months (March 2022 against January 2023) is needed, or per-account detail is. Otherwise `LeadMetrics` covers it and is cleaner |

## Aggregated, never copied row for row

| Table | Rows | Treatment |
|---|---|---|
| `FailedFiltersV2` | 194,303,839 | Daily counts by filter and lender, decoded through `FailedFiltersLookup` |
| `FailedFilters` | 36,997,716 | Superseded; ignore unless history before the V2 cut-over is needed |

## Never extracted

Enforced in `aus_reporting/sensitive.py`, no override:

- `BankStatementSummaries.AccountNumber`, `.SortCode`
- `Affiliates.Password`, `.CredfinSecretKey`, `.TalefinClientSecret`

Restricted — excluded, and requiring an explicit approved entry to
change that:

- `BankStatementSummaries.AccountName`, `.Employer`
- `LenderApplicationResults.Email`, `BankstatementEmailQueue.Email`
- `LeadApplicationCustomValues` where `Name` is `CookieId`, `fbp`,
  `gclid` or `UserAgent`
- Everything on `Leads` and `LeadApplications` already in the restricted
  tier: names, email, mobile, date of birth, driver's licence, address

## Size

About 6 GB. The tripwire agreed earlier stands: revisit the choice of
SQLite if it passes 20 GB, if a warm query passes 3 seconds, or if more
than about 20 concurrent users are needed.

## Blocked before any figure is published

1. **What "accept" means.** Three numbers for what looks like one event:
   `LeadApplicationAccepts` 1,270,433, `SellHistory` 592,557, and the
   `Offer` stage 356,000. A conversion rate built on the wrong one is
   wrong by up to 3.5x. This is a question for the dev team, not a query.
2. **Why bank statement retrievals fell 55% from the 2023 peak.**
   1.46M in 2023 against roughly 663k annualised in 2026. Until it is
   known whether that is volume or a journey change, no trend line
   crossing 2023 can be trusted.
3. **Whether `LeadMetrics.Age` is trustworthy**, given
   `LeadApplications.DateOfBirth` runs to the year 2881.

## Outstanding, unrelated to scope

- The repository is public and holds detailed schema information about a
  database of affordability data. It should be private.
- The Static Web Apps deployment token was pasted into a chat and should
  be rotated.
- 43,240 rows in `LeadApplicationCustomValues` have an empty field name,
  one affiliate, written continuously since 2025-05-14. Still happening.
