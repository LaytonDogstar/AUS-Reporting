# AUS Reporting: scope and rationale

Prepared for technical review, 22 September 2026.

## Why you are reading this

We want to build reporting on the Australian lead data — lead flow,
affordability, lender outcomes and, in time, a view of what an ideal
customer looks like. Before designing anything we ran a read-only survey
of what is actually in the two databases, because the internal access
document turned out to describe something different from what is there.

This document sets out what we found, what we propose to build, and the
five questions only your team can answer. It does not ask for any change
to the existing databases.

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

Three points worth stating plainly, because this data includes
affordability information about real applicants.

**The raw bank statement transactions are not stored anywhere in the
estate.** We checked specifically. What exists is the derived analysis —
income, gambling as a percentage of income, dishonour counts and similar
indicators. The individual transaction lines, which are by far the most
sensitive material, were never retained. Nothing we build changes that.

**Banking details and credentials are blocked in code, with no
configuration option to override.** That covers
`BankStatementSummaries.AccountNumber` and `.SortCode`, and the
credential columns on `Affiliates`.

**Personal identifiers are excluded by default** and require an explicit,
reviewable entry to change — names, email, mobile, date of birth,
driver's licence and address. So are the online identifiers in the
custom values table (`CookieId`, `fbp`, `gclid`, `UserAgent`); campaign
analysis needs the campaign fields, not the device ones. Nothing is
currently approved, and a test enforces it.

Access is business owners only. No affiliate or client login sees any of
this.

## Five questions for your team

Everything above can proceed without these. No published figure should
proceed without the first three.

**1. What does "accept" mean?** Three tables appear to describe one
event, and they disagree: `LeadApplicationAccepts` holds 1,270,433 rows,
`SellHistory` holds 592,557, and the `Offer` stage has fired 356,000
times. A conversion rate built on the wrong one is wrong by up to 3.5x.
This is the single most important question here.

**2. Why did bank statement retrievals fall 55% from the 2023 peak?**
1.46M in 2023, 807k in 2024, 748k in 2025, and 2026 is tracking around
663k annualised. These are accounts rather than applications, so some
of it could be pulling fewer accounts per applicant — but a 45% drop
between 2023 and 2024 looks larger than that would explain. Until we
know whether it is volume or a journey change, no trend crossing 2023
can be trusted.

**3. Is `LeadMetrics.Age` trustworthy?** We would rather use it than
calculate age, because `LeadApplications.DateOfBirth` contains 1753
sentinels and dates as late as the year 2881.

**4. Credfin or Talefin?** The journey stages record `CredfinLanded` on
47% of applications, and there are `CredfinStatus` and
`CredfinLeadStatus` tables. There is no Talefin table, but
`Affiliates.TalefinClientSecret` and `Lenders.ShowTaleFinScore` both
exist. Our reading is that Credfin retrieves the statements and Talefin
scores them. If that is wrong, any metric labelled "bank statement
success rate" would be measuring the wrong step.

**5. A live bug.** 43,240 rows in `LeadApplicationCustomValues` have an
empty `Name`, all from a single affiliate, written continuously since
14 May 2025. Whatever field that was meant to be is being lost, and it
is still happening.

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
