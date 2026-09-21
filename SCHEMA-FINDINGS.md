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

Indexes: 43 in `Overflow`, and `LeadApplicationId` is indexed on the
tables that matter, so joins on it should perform acceptably.

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

`05_date_coverage.sql` was skipped on this run, so history depth is
unknown. Specifically unresolved:

- What the doc's "deleted after 30 days" actually removes. The row
  counts hint that `Overflow` is purged while `OverflowReporting`
  retains history, which would make **the reporting database the only
  source of anything historical** — but that is inference from row
  counts, not measurement.
- Whether `Leads` at 780k versus `LeadApplications` at 4.4M reflects
  genuine repeat applications or different retention windows.

Running `05` answers both. It reads date columns only.

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

1. **Run `05_date_coverage.sql`** to settle retention and history depth.
   Nothing about trend reporting can be designed without it.
2. **Confirm the `Leads` → `LeadApplications` → metrics grain** with
   whoever built the system, since no foreign key documents it.
3. **Decide the architecture** given cross-database joins are
   impossible: a staging layer, or joins performed in the BI tool.
4. **Confirm what `SortCode` holds.**
5. **Establish which datetime columns are UTC and which are local.**
