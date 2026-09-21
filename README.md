# AUS Reporting

Reporting against the AUS (Australia) lead and application databases.

## Status

Discovery is complete and the extract scaffolding is in place. Nothing
is loading yet — the warehouse database has not been created.

| | |
|---|---|
| [`discovery/`](discovery/) | Read-only scripts that mapped the source databases |
| [`SCHEMA-FINDINGS.md`](SCHEMA-FINDINGS.md) | What they found. Read this before writing any query |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | The design, and the constraints that forced it |
| [`aus_reporting/`](aus_reporting/) | The extract |
| [`tables.yml`](tables.yml) | What gets copied, and how |
| [`warehouse/ddl/`](warehouse/ddl/) | Warehouse schema and the dim/fct model |
| [`warehouse/validate.sql`](warehouse/validate.sql) | 13 post-load correctness checks |
| [`STAGES.md`](STAGES.md) | The funnel: `StageId` order, evidence, open labels |
| [`WAREHOUSE-SETUP.md`](WAREHOUSE-SETUP.md) | **Start here:** creating the warehouse, step by step |
| [`EXTRACT-RUNBOOK.md`](EXTRACT-RUNBOOK.md) | Running and maintaining the extract |

Next step is [`WAREHOUSE-SETUP.md`](WAREHOUSE-SETUP.md): create the
database, run the DDL, load the spine. About 30 minutes.

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

Validate the extract config without connecting to anything:

```powershell
python -m aus_reporting --dry-run
```

Run the tests (no database needed):

```powershell
python -m unittest discover -s tests
```

To actually load data, follow [`EXTRACT-RUNBOOK.md`](EXTRACT-RUNBOOK.md).

To re-run schema discovery, see [`discovery/RUNBOOK.md`](discovery/RUNBOOK.md).

## Open questions

These block design, not discovery. Answers wanted from whoever owns the
definitions:

**Answered by discovery** — see `SCHEMA-FINDINGS.md`
- Retention: neither database is purged; history runs from 2021-07-01.
- Datetimes: the server is UTC. Local time is application-written.
- Funded outcomes: lenders do not report them. The funnel ends at the
  sale, so conversion means the accept, not funding.

**Still open**
- **What timezone defines a reporting day?** Australia spans three, and
  not every state observes daylight saving. Blocks correct daily numbers.
- **What does `StageId` mean?** It drives the funnel and has no lookup
  table in either database.
- **Which metrics matter, and who owns their definitions?** Conversion
  rate, cost per sale, affiliate quality — each needs an owner.
- Are monetary values in cents or dollars?
- What test or internal traffic must be excluded from every report?
  (`Affiliates.IsInternalSource` looks relevant.)
- May report outputs leave the Azure boundary? Decides the BI tool.
- What does `SortCode` hold? A UK term in an Australian system.

## Conventions

- Credentials never live in this repo. Use Key Vault or environment
  variables; `.env` is gitignored.
- Discovery output is gitignored — it can contain schema detail and, if
  lookup tables are dumped, real data.
- Reporting connects with a dedicated read-only login, not a named
  person's account.
- **Banking columns and secrets cannot be extracted.** Configuring one
  fails the run; there is no override. Personal identifiers require
  explicit per-column approval in `tables.yml`. See
  `aus_reporting/sensitive.py`.
- Columns are always listed explicitly. No `SELECT *`, so a column added
  upstream never arrives unnoticed.
- Run `python -m unittest discover -s tests` before committing changes
  to `aus_reporting/`.
