# Runbook: the extract

How to stand up the warehouse and run the extract. Read
[`ARCHITECTURE.md`](ARCHITECTURE.md) first if you want to know why any of
it is shaped this way.

---

## What it does

Copies a defined set of columns from `Overflow` and `OverflowReporting`
into a warehouse we own and index. It never writes to the source.

Two modes, per table, set in [`tables.yml`](tables.yml):

- **incremental** — `WHERE Id > <last loaded>`, in batches. Only used
  where the key is an `int`/`bigint` IDENTITY, which makes the read a
  clustered index seek. Costs the same on 194M rows as on 1M.
- **snapshot** — full replace. For `uniqueidentifier`-keyed tables,
  which have no usable watermark, and for small config tables.

## What it will not copy

Banking columns and secrets **cannot be configured at all**. Listing
`AccountNumber`, `SortCode`, `Password`, `CredfinSecretKey` or similar
fails before anything connects, with no override:

```
config error: Overflow.dbo.Affiliates: these columns may never be
extracted: CredfinSecretKey. They are banking details or credentials;
there is no override.
```

Personal identifiers — name, email, phone, date of birth, licence,
street address — are allowed but only when the spec names them under
`restricted_approved`. That makes every instance a reviewable line in
`tables.yml` rather than an accident. **Nothing in the committed config
approves any**, and a test enforces that.

---

## One-off setup

### 1. Create the warehouse database

A new Azure SQL Database. Basic or S0 is plenty to start. Note the server
name and database name.

### 2. Create a service login

Do not use a named person's account. On the **source** server it needs
`db_datareader` on both databases and nothing else. On the **warehouse**
it needs to read and write.

### 3. Run the DDL

In SSMS, against the **warehouse**, in order:

```
warehouse/ddl/001_control.sql     -- watermark and run-history tables
warehouse/ddl/002_staging.sql     -- staging tables and their indexes
```

Both are idempotent — safe to re-run.

### 4. Install Python and the dependencies

Python 3.11 or later, then:

```powershell
python -m pip install -r requirements.txt
```

`pyodbc` also needs the **Microsoft ODBC Driver 18 for SQL Server**,
which is a separate install and usually already present on a machine
with SSMS.

### 5. Set the environment

Copy `.env.example` to `.env` and fill it in. On a server, set these from
Key Vault instead of a file.

`.env` is gitignored and must stay that way.

---

## Running it

Validate the config without connecting to anything:

```powershell
python -m aus_reporting --dry-run
```

This prints the exact SQL each spec will run and confirms no disallowed
column is configured. It needs no credentials and touches nothing. **Do
this after every change to `tables.yml`.**

Then, for real:

```powershell
python -m aus_reporting
```

One table at a time:

```powershell
python -m aus_reporting --only LeadApplicationStages
```

Exit codes: `0` success, `1` at least one table failed, `2` bad config.

### What you should see

```
7 table(s) across 2 database(s)
table                                        status         rows  batches
--------------------------------------------------------------------------
Overflow.dbo.Leads                           ok          780,508        1
...
7 table(s), 8,432,117 row(s), 0 failure(s)
```

A failure on one table does not stop the others. Failures are listed at
the end and the exit code is non-zero.

---

## Checking it worked

In SSMS against the warehouse:

```sql
SELECT * FROM ctl.vwFreshness ORDER BY MinutesSinceLoad DESC;
```

One row per incremental table, showing the last value loaded and how
long ago. Snapshot tables do not appear — they are replaced whole, so
check `MAX(_LoadedUtc)` on the table itself.

Remember the source runs about **two minutes behind live**, so anything
built on this should say "data as at" rather than implying it is current.

---

## Adding a table

1. Add a spec to `tables.yml`. Copy an existing one; the comments at the
   top explain each field.
2. `python -m aus_reporting --dry-run` — confirms the config is valid
   and the columns are allowed.
3. Add the staging table to `warehouse/ddl/002_staging.sql` and run it.
4. `python -m aus_reporting --only <target>`.

Choosing the mode: **incremental** if the source `Id` is `int` or
`bigint` IDENTITY *and* rows are only ever inserted. **snapshot**
otherwise. Never watermark on a date column — no date column is indexed
in either source database, so it would scan the whole table every run.

---

## If something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| `config error: ... may never be extracted` | A banking or secret column is configured | Remove it. There is no override |
| `config error: ... need to be listed under restricted_approved` | A personal identifier is configured | Remove it, or approve it deliberately with a reason |
| `config error: missing required environment variable(s)` | `.env` not loaded or incomplete | Check against `.env.example` |
| `pyodbc is not installed` | Dependencies or ODBC driver missing | See setup steps 4 |
| `Login failed for user` | Wrong credentials | Test them in SSMS first |
| `Cannot open server ... requested by the login` | Firewall — IP not allow-listed | Run from FLOWWEB4 |
| `Invalid object name 'stg....'` | DDL not run, or run against the wrong database | Re-run `002_staging.sql` on the warehouse |
| A table reloads rows it already had | Expected after an interrupted run | Harmless. The batch is committed before the watermark moves, so a crash re-reads rather than loses. The model layer de-duplicates on `Id` |

### Resetting a table

To force a full re-read of an incremental table:

```sql
DELETE FROM ctl.ExtractWatermark WHERE TargetTable = 'LeadApplicationStages';
TRUNCATE TABLE stg.LeadApplicationStages;
```

Then run the extract for that target.

---

## Tests

```powershell
python -m unittest discover -s tests
```

60 tests, no database required. They cover the generated SQL, the
batching and watermark behaviour, the config loader, and the
sensitive-column guards. Run them before committing a change to
`aus_reporting/`.
