# AUS Reporting: scope and rationale

Prepared for technical review, 23 September 2026.

## Why you are reading this

We want to build reporting on the Australian lead data — lead flow,
affordability, lender outcomes and, in time, a view of what an ideal
customer looks like. Before designing anything we ran a read-only survey
of what is actually in the two databases, because the internal access
document turned out to describe something different from what is there.

This document sets out what we found and what we propose to build. It
does not ask for any change to the existing databases.

## What was done

Metadata queries against the reporting replica
(`fw04-sqlreporting01`), never the live instance. Column names and row
counts from the system catalog views, plus a small number of date-range
and grouping queries. Everything ran under `READ UNCOMMITTED`. Nothing
was written, no schema was altered, and no query held a lock that could
block replication.

The heaviest query in the whole exercise read 22.6 million rows and
returned in 44 seconds. Most returned in under a second.

## Why a separate reporting layer is needed

**This is not a criticism of the databases.** `Overflow` and
`OverflowReporting` are replication subscribers serving a transactional
application, and they do that job. Reporting is a different workload,
and three properties that are entirely reasonable for an OLTP replica
make it awkward to serve reporting directly:

- **Cross-database joins are not supported.** Both databases are Azure
  SQL Database (EngineEdition 5), so a three-part name cannot reach
  across them without Elastic Query. Lead identity lives in `Overflow`
  and the affordability metrics live in `OverflowReporting`, so any
  report needing both cannot be a single query. We verified this from
  both directions.

- **There are no indexes on any column reporting would filter by.**
  Every user index in both databases is the clustered primary key.
  There is nothing on `LeadApplicationId` and nothing on any date
  column, so a query for "last 30 days" costs the same as a query for
  all five years.

- **There are no foreign keys**, in either database. The grain of each
  table has to be inferred rather than read, which is why several of
  the questions at the end of this document exist.

## What we are not doing

**Not building a data warehouse.** That was the original proposal, and
we dropped it on measurement. The core funnel query — daily counts by
affiliate and stage over 30 days — returns in **5.13 seconds from a cold
cache**. That does not justify a second Azure SQL database, its cost, or
its maintenance. The written architecture was amended to say so.

**Not using Power BI.** A custom dashboard is already built and working
against synthetic data.

## What we are proposing

A dedicated reporting application for Overflow. Deliberately modest: a
SQLite database and a small web service. No new platform, no new vendor,
no new database server, and nothing added to the Azure estate.

That is sized to the problem rather than under-specified. The dataset is
about 6 GB and the audience is a handful of business owners, so a
managed analytics platform would cost several thousand a year to answer
questions a single file and a scheduled job answer just as well. If that
changes — many concurrent analysts, or data sources beyond these two
databases — the decision is worth revisiting, and we have written down
the thresholds that would trigger it.

A small collector runs on **FLOWWEB4**, on a schedule. It reads a defined
list of columns, aggregates two very large tables on the way through, and
pushes the result out. It runs there because port 1433 is firewalled to
allow-listed addresses — correctly — so nothing in the cloud can reach
the database directly, and we are not asking for that to change.

The result is roughly 6 GB: a subset of columns from twelve tables, no
raw transaction data, and no personal identifiers.

## Data protection

Four points worth stating plainly, because this data includes
affordability information about real applicants.

**The raw bank statement transactions are not stored anywhere in the
estate.** We checked specifically. What exists is the derived analysis —
income, gambling as a percentage of income, dishonour counts and similar
indicators. The individual transaction lines, which are by far the most
sensitive material, were never retained. Nothing we build changes that.

**Banking details, credentials and personal identifiers are blocked in
code, with no configuration option to override.** That covers
`BankStatementSummaries.AccountNumber` and `.SortCode`, the credential
columns on `Affiliates`, and every name, email address, mobile number,
date of birth, driver's licence and street address in either database.

Nothing in the reporting model needs to know who anyone is. Affordability,
campaign and outcome analysis all work on attributes — age band, state,
income, employment, gambling as a percentage of income — and a name has
no predictive value. So the control is one that cannot be switched on
rather than one that merely is not switched on today, and a test asserts
that enabling an identifier is not something the configuration can
express.

**The online identifiers are a reviewable opt-in, and none is enabled.**
`CookieId`, `fbp`, `gclid` and `UserAgent` in the custom values table
identify a device rather than a person, and there is a conceivable reason
to want one, so they can be turned on by a visible edit rather than not
at all. Campaign analysis needs the campaign fields, not the device ones.

**Geography is the deliberate exception, and worth naming.** State, city
and postcode are extracted, because reporting needs them and none
identifies a person on its own. A full postcode together with age and
income can narrow to very few people, so that is handled where it
belongs — figures are banded and any group below about ten is
suppressed — rather than by refusing to hold the column.

Access is business owners only. No affiliate or client login sees any of
this.

## Two things that would help

**`MoneyspotLeadKey`** appears on 233,848 applications across 56
affiliates. A lender-side reference is a route to reconciling funded
data against a lender's own records — which is the gap that has made
commission and conversion reporting unreliable since `FundedLeads` stopped
being written in October 2024. Worth knowing whether that relationship
could be used that way.

**Two `Cost` columns** — on `BankStatementRetrievals` and
`LenderApplicationResults` — mean lead quality can be reported as margin
rather than as a conversion rate. Nothing currently uses either. If
those figures are reliable, they are probably the most commercially
useful columns in the estate.

---

What follows is the detailed scope: every table proposed, what is taken
from it, what is aggregated, and what is never extracted.
