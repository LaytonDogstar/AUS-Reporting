# AUS Reporting

Reporting against the AUS (Australia) lead and application databases.

## Status

Early. Nothing is built yet — this repo currently contains the schema
discovery pack only. The first job is to find out what is actually in
the databases, because at the moment the only documented facts are two
database names and one example join.

## Background

Leads arrive via affiliates (online, offline and iframe-embedded), are
processed through an application journey, and are distributed to lenders
via a PingTree. Data lands in Azure.

Two databases are relevant:

| Database | Contents |
|---|---|
| `Overflow` | Raw application data — transactional values, lender and PingTree values, blacklists, affiliates |
| `OverflowReporting` | Lead-level reporting values — filters, errors, status metrics |

Both are reached through the `FLOWWEB4` jump box. There are two
instances: a live one, and a reporting replica (`fw04-sqlreporting01`)
that runs roughly two minutes behind with a reduced set of fields.

**All reporting work targets the replica.** Complex queries against live
risk affecting real users mid-application.

See `Accessing AUD Reporting Database.docx` (held outside this repo) for
access instructions. Note that document has open review comments against
it, including exposed connection details in its screenshots.

## Getting started

Run the discovery pack and share the output:

```powershell
cd discovery
.\run-discovery.ps1 -ServerName fw04-sqlreporting01.database.windows.net -Database OverflowReporting
```

See [`discovery/README.md`](discovery/README.md) for detail, safety notes
and what to send back.

## Open questions

These block design, not discovery. Answers wanted from whoever owns the
definitions:

**Funnel and metrics**
- What distinguishes a lead, an application and a funded customer?
- What counts as a conversion, and when is commission recognised?
- Which status and error codes matter, and what do they mean?
- How is PingTree structured — what is a ping, a post, and a stored value?

**Data semantics**
- Are datetimes stored UTC or Australian local time?
- Are monetary values stored in cents or dollars?
- Can one person appear as multiple leads, and how are unique applicants counted?
- What test or internal traffic must be excluded from every report?

**Retention and compliance**
- Exactly what does the 30-day transactional deletion remove? This caps
  how far back any report can ever look.
- Which fields are off-limits to reporting, and is that enforced by
  grants or by convention?
- May report outputs leave the Azure boundary? This decides the BI tool.

**Delivery**
- Who is the audience, and what questions must this answer?
- What grain and cadence — daily, hourly, near-real-time?
- What output surface — Power BI, Metabase, scheduled email, Excel?

## Conventions

- Credentials never live in this repo. Use Key Vault or environment
  variables; `.env` is gitignored.
- Discovery output is gitignored — it can contain schema detail and, if
  lookup tables are dumped, real data.
- Reporting connects with a dedicated read-only login, not a named
  person's account.
