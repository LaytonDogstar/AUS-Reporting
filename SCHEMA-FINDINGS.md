# Schema findings

From the discovery pack, run 21 Sep 2026 against
`fw04-sqlreporting01.database.windows.net` as login `LaytonB`.

Everything below is from the catalog views, not from assumption. Where
something is inference rather than fact, it says so.

---

## 1. The access doc's example query cannot run

The doc gives:

```sql
select * from Overflow.dbo.Leads L
join OverflowReporting.dbo.LeadMetrics M on M.ID = L.ID
```

This fails for three independent reasons, any one of which is fatal.

**a. Cross-database joins are not supported here.**
`SERVERPROPERTY('EngineEdition')` returns **5** — Azure SQL Database
(PaaS). Three-part naming across databases needs Elastic Query, which is
not configured. Probed from both sides, and both returned:

> Reference to database and/or server name in 'OverflowReporting.sys.tables'
> is not supported in this version of SQL Server.

**b. The join columns are different types.**

| Column | Type |
|---|---|
| `Overflow.dbo.Leads.Id` | `uniqueidentifier` |
| `OverflowReporting.dbo.LeadMetrics.Id` | `bigint IDENTITY` |

Comparing them raises a conversion error. It would not silently return
wrong rows — it would refuse to run.

**c. They are not related columns anyway.**
`LeadMetrics.Id` is that table's own identity column. Its link to the
rest of the model is `LeadApplicationId`, column 25, a
`uniqueidentifier`.

---

## 2. The real join key is `LeadApplicationId`

A `uniqueidentifier` carried across both databases. It, not `Id`, is the
grain almost everything hangs off.

| Database | Tables carrying `LeadApplicationId` | Tables carrying `LeadId` |
|---|---|---|
| `Overflow` | 7 | 7 |
| `OverflowReporting` | 18 | 2 |

The chain is:

```
Leads.Id  ->  LeadApplications.LeadId
              LeadApplications.Id  ->  <everything>.LeadApplicationId
```

One lead can have many applications: `Leads` holds 780,492 rows against
`LeadApplications` at 4,443,562. Whether that is genuine repeat
applications or an artefact of differing retention is **not yet
established** — see section 6.

---

## 3. The two databases are not what the doc describes

The doc says `OverflowReporting` is "a replication of the live data...
stripped down versions of the data... to reduce size and increase
performance."

The measurements say otherwise:

| | `Overflow` | `OverflowReporting` |
|---|---|---|
| Tables | 30 | 39 |
| Rows | 117,641,030 | 378,868,105 |
| Size | 14.7 GB | **65.5 GB** |

The reporting database is roughly **four times larger**, not stripped
down.

They are also not the same tables with fewer columns. Only two real
tables exist in both — `LeadApplicationAccepts` and
`LenderApplicationResults`. The other five shared names are replication
plumbing (`MSreplication_*`) and `SchemaVersions`. 23 tables are unique
to `Overflow`, 32 to `OverflowReporting`.

They are two different schemas serving different purposes:

- **`Overflow`** — transactional application data. `Leads`,
  `LeadApplications`, `Lenders`, `Affiliates`, `PingTrees`,
  `PingTreeItems`, `BankStatementSummaries`, `FundedLeads`.
- **`OverflowReporting`** — event, diagnostic and metric history.
  `FailedFiltersV2` (194.1M rows), `LenderApplicationResults` (98.9M),
  `FailedFilters` (37.0M), `LeadApplicationStages` (19.8M),
  `LeadMetrics`, `ApiErrors`, `LeadDiagnostics`, `LeadRedirects`.

**Both** carry `MSreplication_objects` and `MSsubscription_agents`, so
both are replication subscribers. The doc's framing of one live instance
and one replica does not match: these are two subscriber databases on the
same reporting server.

**Consequence for reporting:** because cross-database joins do not work,
any report needing lead identity from `Overflow` *and* metrics from
`OverflowReporting` cannot be a single query. It needs either a
staging/warehouse layer that lands both, or two queries joined in the
reporting tool.

---

## 4. There are no foreign keys at all

Zero declared foreign keys in either database. Every table's primary key
is its own `Id`.

All relationships are convention. Nothing stops an orphaned
`LeadApplicationId`, and the database will not tell us which join is
correct — it has to come from whoever built it, or from checking the
data.

**Correction.** An earlier version of this document said
`LeadApplicationId` was indexed on the tables that matter and that joins
on it should perform acceptably. That was wrong, and section 4a replaces
it. It was inferred before the index output had been examined.

---

## 4a. There are no reporting indexes either

Every user index in both databases is the clustered primary key on `Id`.
Nothing else exists.

| | `Overflow` | `OverflowReporting` |
|---|---|---|
| Total indexes | 33 | 43 |
| Non-PK indexes | 8 | 9 |
| Non-PK indexes that are not replication plumbing | 1 | 2 |

The handful of non-PK indexes are `MSreplication_*` internals plus
`Affiliates(Id)`, `AffiliateInvoices(Id)` and `webpages_Roles(RoleName)`.

Two consequences, both serious:

- **No index contains `LeadApplicationId`.** Not one, in either
  database. Every join on the key that the whole model hangs off is a
  full scan of both sides.
- **No date column leads an index.** All 52 date columns checked in
  `OverflowReporting` are unindexed. Every "last 30 days" filter scans
  the entire table.

On `FailedFiltersV2` that means 194M rows and 26 GB read to answer a
question about yesterday.

This database is a replication target, not a reporting-tuned store.
Running dashboard queries directly against it will be slow and will load
a server that live-ish processes also use.

**This is the finding that decides the architecture.** Combined with
cross-database joins being unavailable, querying these databases
directly is not viable. The work needs an extract into a properly
modelled and indexed store, and reporting runs against that.

---

## 4b. History goes back to 2021, not 30 days

`05_date_coverage.sql`, run against `OverflowReporting`, read 48 of 52
date columns.

**Nothing here is subject to a 30-day deletion.** Most tables start at
exactly **2021-07-01** — a hard floor that looks like a platform launch
or migration cut-over — and run to the day of the run. Over five years
of history is available.

Dates worth knowing before promising any trend:

| Table | From | To | Note |
|---|---|---|---|
| `LeadApplicationStages` | 2021-07-01 | current | 19.8M rows, the fullest funnel history |
| `LeadMetadata`, `ApiErrors`, `LeadRedirects` | 2021-07-01 | current | |
| `LeadFundedStatuses`, `LeadApplicationAccepts` | 2021-07-01 | current | |
| **`LeadMetrics`** | **2023-01-24** | current | **no affordability metrics before 2023** |
| `OfferResults` | 2024-07-29 | current | |
| `AccountCategorisation` | 2025-05-06 | current | |
| `LeadSmsEmailRetry` | 2025-05-22 | current | |
| `SystemAlerts` | 2026-05-26 | current | very new |
| `AffiliateInvoices` | 2017-11-24 | current | pre-dates everything else |
| `OfflineConversions` | 2022-09-11 | **2023-11-12** | dead, nothing written since |

Two traps for anyone writing a query:

- **`AffiliateRawData` stops on 2025-11-28 and `AffiliateRawDataV2`
  starts the same day.** A clean cut-over. Any affiliate report spanning
  that date must union both tables or it will silently lose everything
  on one side of it.
- **`LeadMetrics` starts 2023-01-24.** Any metric built on it cannot be
  compared with 2021-22, and a year-on-year chart reaching back further
  will show a false zero.

`AccountCategorisation.NextExpectedIncomeDate` and
`AffiliateInvoices.DueDate` hold dates in the future, which is correct
for what they represent - worth remembering before anyone writes a
`WHERE date <= today` filter that quietly drops them.

Four columns were not read: `FailedFiltersV2`, `FailedFilters` and
`LenderApplicationResults` are large with unindexed date columns, so the
script avoided a full scan, and `MSsnapshotdeliveryprogress` is empty.
Their history is unknown, though the 2021-07-01 floor elsewhere makes a
similar range likely.

Replication is current: subscriber metadata timestamps and the data both
run to the day of the run.

---

## 4c. `Overflow` is not purged either, and `FundedLeads` is dead

`05` run against `Overflow`: 42 of 47 date columns read.

**Neither database is subject to a 30-day deletion.** `Leads.DateCreated`
and `LeadApplications.DateCreated` both run 2021-07-01 to the day of the
run, the same floor as `OverflowReporting`. Whatever the doc's 30-day
deletion refers to, it is a *third* store — consistent with its wording
about data "processed within a transactional database" — and it is not
on this server. Nothing reachable from here is being purged.

That also settles the lead-to-application ratio. `Leads` (780,508) and
`LeadApplications` (4,443,646) cover the *same* five-year window, so the
roughly 1:5.7 ratio is genuine repeat applications, not a retention
artefact.

### `FundedLeads` stopped being written in October 2024

| Column | Earliest | Latest |
|---|---|---|
| `FundedLeads.DateCreated` | 2022-05-11 | **2024-10-11** |
| `FundedLeads.DateFunded` | 2022-06-11 | **2024-10-03** |
| `FundedLeads.DateSold` | 2022-04-12 | **2024-10-01** |

146,000 rows, frozen for about eleven months while every other active
table runs to the current day.

**Any funding or commission report built on `FundedLeads` will be
correct up to October 2024 and then silently flat-line.** It will not
error — it will just show zero.

`OverflowReporting.LeadFundedStatuses` (1.27M rows, 2021-07-01 to
current) is the live equivalent and is almost certainly where funding
state moved to. Confirm that with whoever made the change before
building on it.

Two other tables to check the same way: `SellHistory` only starts
2025-02-20 and `LeadScores` only starts 2024-09-24, so neither supports
a multi-year trend.

### Date columns containing impossible values

| Column | Minimum | Maximum |
|---|---|---|
| `LeadApplications.DateOfBirth` | 1753-01-01 | **2881-06-15** |
| `LeadApplications.MoveInDate` | 1900-01-01 | **5687-12-01** |
| `Leads.DateOfBirth` | 1753-01-01 | 2026-09-29 |

1753-01-01 is the SQL Server `datetime` minimum, so it is a sentinel for
"not supplied" rather than a real date. The maxima are data-entry
garbage, and `Leads.DateOfBirth` includes a date in the future.

**Any age calculation off these columns produces nonsense unless the
range is filtered first.** `LeadMetrics.Age` may be the cleaner source;
it is worth comparing the two before any age banding is published.

### Configuration tables share a migration date

`PingTrees`, `PingTreeItems`, `Lenders`, `LenderTiers`, `AffiliateGroups`,
`ConditionalFilters`, `LenderBlacklisting`, `CommissionOverrides` and
`LenderTierBaseFilters` all carry `DateCreated` of **2025-03-04**, and
several have a `DateModified` *earlier* than their `DateCreated`
(`PingTreeItems` is modified from 2020-09-24 but created 2025-03-04).

So `DateCreated` on the configuration tables records a bulk migration on
that date, not when the record really came into being. It cannot be used
to date a lender or affiliate relationship. `Affiliates` is the
exception, running back to 2015-06-05.

Five columns were not read: `LeadApplicationCustomValues` (22.6M) and
`LenderApplicationResults` (31.2M) are large with unindexed date
columns, and three are empty or all-NULL.

---

## 5. Access, time and collation

- **Read-only is enforced.** `LaytonB` holds `db_datareader` with
  `CONNECT` and `SELECT` only. No write is possible with this login.
- **The server runs UTC.** Local time and UTC are identical, offset
  zero. Any Australian local time in the data was written by the
  application, so every datetime column needs its meaning confirmed
  before it is used in a daily report.
- **Collation is `SQL_Latin1_General_CP1_CI_AS` — case insensitive.**
  So the `OverFlow` vs `Overflow` inconsistency in the doc is cosmetic,
  not functional. Worth tidying in the doc, but it breaks nothing.

---

## 6. Not yet established

Date coverage has now been measured for both databases. What remains
needs a person, not a query:

- **Where funding data moved after October 2024**, and whether
  `LeadFundedStatuses` is the full replacement for `FundedLeads`.
- **What the doc's "deleted after 30 days" refers to.** Neither database
  here is purged, so it is a third store not on this server. Worth
  knowing whether anything needed for reporting lives in it.
- **Whether `LeadMetrics.Age` is trustworthy** where
  `LeadApplications.DateOfBirth` is not.
- **What `SortCode` actually holds** (section 7).
- **Which datetime columns are UTC and which are local** (section 5).
- **The history of the largest tables** — `FailedFiltersV2`,
  `LenderApplicationResults`, `FailedFilters`,
  `LeadApplicationCustomValues` — skipped as unindexed scans.

---

## 7. Personal and banking data

| Category | `Overflow` | `OverflowReporting` |
|---|---|---|
| Contact / PII | 18 | 17 |
| Credential-ish | 6 | 11 |
| Identity | 3 | 0 |
| **Banking** | **2** | **0** |

The only account-level banking columns found are in `Overflow`:

- `BankStatementSummaries.AccountNumber`
- `BankStatementSummaries.SortCode`

**`OverflowReporting` holds no banking columns at all.** That is a
meaningful argument for building reporting against it wherever possible:
the sensitive material largely is not there.

`Overflow.Leads` carries `FirstName`, `LastName`, `Email`,
`MobileNumber`, `DateOfBirth`. `LeadApplications` carries all of that
plus `DriversLicense` and full address.

`LeadMetrics` is the interesting one for reporting — 82 columns of
derived affordability and risk indicators (income, gambling as a
percentage of income, dishonours, Centrelink proportion, SACC loan
indicators) with **no direct identifiers** beyond
`LeadApplicationId`. Good reporting material.

> **`SortCode` is a UK banking term; the Australian equivalent is BSB.**
> Either the column is misnamed, or this schema has UK origins and the
> field is reused. Worth confirming what it actually contains — it
> changes how it should be treated.

---

## 8. Recommended next steps

1. **Establish where funding data lives after October 2024.** Nothing
   about commission or conversion reporting can be trusted until this is
   answered.
2. **Confirm the `Leads` → `LeadApplications` → metrics grain** with
   whoever built the system, since no foreign key documents it.
3. **Decide the architecture** given cross-database joins are
   impossible: a staging layer, or joins performed in the BI tool.
4. **Confirm what `SortCode` holds.**
5. **Establish which datetime columns are UTC and which are local.**
