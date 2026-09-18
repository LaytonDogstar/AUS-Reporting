# Schema discovery pack

A set of read-only scripts that describe what is actually inside the
`Overflow` and `OverflowReporting` databases. Run once, hand back the
output, and we can start designing reporting against facts rather than
assumptions.

## What this is for

Right now the only documented facts about these databases are two names
and one example join. That is not enough to build against. This pack
answers:

- what engine these databases actually run on, and whether cross-database
  joins work at all
- every table and column, with types, keys and indexes
- how far back the data goes, and what is still being written to
- which columns hold personal or banking data, so reporting can avoid them
- whether the access doc's `LeadMetrics.ID = Leads.ID` join is correct

## Safety

- **Every script is read-only.** No DDL, no DML, no writes of any kind.
- **Eight of the nine read metadata only** — system catalog views. They
  never touch application data.
- **`05_date_coverage.sql` is the exception.** It reads date columns to
  get MIN/MAX. It reads no names, no contact details, no bank details.
- All scripts run under `READ UNCOMMITTED` so they cannot block anything.
- **Run against the reporting replica**, `fw04-sqlreporting01`, not live.

## Running it

### Option A — PowerShell (recommended)

From FLOWWEB4, or any machine that can reach the instance:

```powershell
cd discovery
.\run-discovery.ps1 -ServerName fw04-sqlreporting01.database.windows.net -Database OverflowReporting
.\run-discovery.ps1 -ServerName fw04-sqlreporting01.database.windows.net -Database Overflow
```

It prompts for the login and password, runs everything, and writes one
CSV per result set into `discovery/output/<database>-<timestamp>/`.

Uses ADO.NET, which is built into Windows — nothing to install, and it
does not need the `SqlServer` PowerShell module or `sqlcmd`.

Run it in **Windows PowerShell 5.1** (the blue console, or
`powershell.exe`). PowerShell 7 does not load `System.Data.SqlClient` by
default; the script detects this and tells you rather than failing
obscurely.

First pass, skip the slow one:

```powershell
.\run-discovery.ps1 -ServerName ... -Database OverflowReporting -SkipDateCoverage
```

### Option B — SSMS by hand

Open each `.sql` file in SSMS and run it. Use **Results → Results to
Grid**, then right-click the grid → *Save Results As* to export each set
to CSV. Run the whole pack once per database.

Azure SQL Database does not allow `USE <database>`, so you need a
separate connection for `Overflow` and `OverflowReporting`. Change the
database in the SSMS dropdown or reconnect.

## The scripts

| Script | Reads | What it answers |
|---|---|---|
| `00_server_context.sql` | metadata | Engine edition, collation, server clock vs UTC, this login's permissions, whether cross-database joins work |
| `01_tables_and_views.sql` | metadata | Tables with row counts and size; views; stored procs and functions |
| `02_columns.sql` | metadata | Every column, type, nullability, defaults |
| `03_keys_and_relationships.sql` | metadata | Primary keys, foreign keys, unique constraints |
| `04_indexes.sql` | metadata | Indexes with key and included columns — what is cheap to query |
| `05_date_coverage.sql` | **date values** | History depth and freshness per date column |
| `06_sensitive_columns.sql` | metadata | Columns whose names suggest PII or banking data |
| `07_join_keys.sql` | metadata | Identifier columns, lead-table shape, the `M.ID = L.ID` question |
| `08_lookup_candidates.sql` | metadata | Small tables that probably decode status codes, and status-like columns |

`08` deliberately *generates* `SELECT` statements rather than running
them, so a human decides what gets dumped.

## What to send back

The whole output folder, or if that is awkward, these four first — they
carry most of the value:

1. `00_server_context_*.csv`
2. `02_columns_*.csv`
3. `03_keys_and_relationships_*.csv`
4. `06_sensitive_columns_*.csv`

**Before sending:** open the `06` output and sanity-check that nothing in
the set contains actual customer data. The scripts are written so it
should not, but it costs thirty seconds to confirm.

## Credentials

Do not put credentials in these files, in the repo, or in chat. The
PowerShell runner prompts at runtime and holds the password as a
`SecureString`. For anything automated later, use Key Vault or
environment variables.

Ideally this runs as a **dedicated read-only login**, not a named
person's account — see `00_server_context.sql`, which reports what
permissions the connecting login actually has.
