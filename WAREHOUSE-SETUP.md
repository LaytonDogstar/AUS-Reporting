# Setting up the warehouse

One-off. About 30 minutes, most of it waiting for Azure.

At the end you will have a database you own, the model in it, and the
first load done.

---

## What you are creating, and why not reuse the existing server

A **new Azure SQL logical server** with **one database** on it.

Not on `fw04-sqlreporting01`, even though that would seem simpler:

- It is a **replication subscriber that something else owns.** Our load
  would compete for its compute budget.
- Cost and scaling stay separate and attributable.
- There is **no benefit to co-locating**, because Azure SQL Database
  cannot join across databases even on the same server. That is the
  constraint that makes this whole project necessary; putting the
  warehouse next door does not relax it.

Put the new server in the **same Azure region** as `fw04-sqlreporting01`
so the extract is not pulling data between regions. Check the source
region first: Azure Portal → search `fw04-sqlreporting01` → the
overview blade shows Location.

**Rough size:** the extract copies a subset of columns, so expect
**3–5 GB** once loaded, not the 80 GB the sources occupy.

**Rough cost:** serverless at this size, loading a few times a day, lands
in the low tens of pounds per month. The portal shows a live estimate as
you pick options — trust that over this paragraph, since pricing varies
by region and changes.

---

## Step 1 — Create the server and database

Azure Portal → **Create a resource** → search **SQL Database** →
**Create**.

**Basics tab**

| Field | Value |
|---|---|
| Resource group | Use the existing one, or create `aus-reporting` |
| Database name | `AusReporting` |
| Server | **Create new** (see below) |
| Want to use SQL elastic pool? | **No** |
| Workload environment | **Production** |

For the server, click *Create new*:

| Field | Value |
|---|---|
| Server name | something globally unique, e.g. `aus-reporting-sql` |
| Location | **the same region as `fw04-sqlreporting01`** |
| Authentication method | **Use SQL authentication** |
| Server admin login | `ausadmin` (or your preference — not a person's name) |
| Password | generate a strong one and **put it in your password manager now** |

> Microsoft Entra authentication is the better practice and avoids
> passwords entirely. SQL authentication is chosen here to match how the
> source is already accessed, and because it works without involving
> the Entra admins. Worth revisiting later.

**Compute + storage** — click *Configure database*:

| Field | Value |
|---|---|
| Service tier | **General Purpose — Serverless** |
| Hardware | Standard-series (Gen5) |
| Max vCores | 2 |
| Min vCores | 0.5 |
| Auto-pause delay | **1 hour** |
| Data max size | 32 GB |

Serverless suits this: loads are bursty, and the database can idle
cheaply between them.

> **The trade-off with auto-pause:** a paused database takes 30–60
> seconds to wake. If Power BI hits it on a schedule that is fine, but
> an interactive user may see a timeout on the first click. If that
> becomes annoying, either disable auto-pause or move to a provisioned
> tier.

**Additional settings tab**

| Field | Value |
|---|---|
| Use existing data | **None** |
| Collation | **`SQL_Latin1_General_CP1_CI_AS`** — leave the default |

That collation matches both source databases exactly. Do not change it:
a mismatch causes errors when comparing strings across them and is
tedious to undo.

**Networking tab**

| Field | Value |
|---|---|
| Connectivity method | Public endpoint |
| Allow Azure services to access this server | **No** |
| Add current client IP address | **Yes** |

Then **Review + create** → **Create**. Takes a few minutes.

---

## Step 2 — Let FLOWWEB4 reach it

The extract runs on FLOWWEB4, so the warehouse firewall has to allow
FLOWWEB4's outbound IP. Step 1 only allowed the machine you were
sitting at.

On **FLOWWEB4**, in PowerShell:

```powershell
(Invoke-WebRequest -Uri "https://ifconfig.me/ip" -UseBasicParsing).Content
```

That prints the public IP FLOWWEB4 goes out from. Then in the Azure
Portal:

**Your new SQL server** → **Networking** → **Firewall rules** → **Add a
firewall rule**:

| Field | Value |
|---|---|
| Rule name | `flowweb4` |
| Start IP | the address above |
| End IP | the same address |

**Save.**

> If the extract later moves to an Azure Function or Container App in
> the VNet, this rule is replaced by a private endpoint or a VNet rule.
> For a scheduled job on the VM, the IP rule is the right answer.

---

## Step 3 — Create the service login

The extract should not run as your own account.

In **SSMS**, connect to the **new server** as the admin login from
Step 1.

First, against the **`master`** database:

```sql
CREATE LOGIN svc_ausreporting
    WITH PASSWORD = '<generate a strong password>';
```

Then switch the database dropdown to **`AusReporting`** and run:

```sql
CREATE USER svc_ausreporting FOR LOGIN svc_ausreporting;

ALTER ROLE db_datareader ADD MEMBER svc_ausreporting;
ALTER ROLE db_datawriter ADD MEMBER svc_ausreporting;
```

You also need one narrower grant, which must run **after** Step 4
creates the `stg` schema:

```sql
GRANT ALTER ON SCHEMA::stg TO svc_ausreporting;
```

> **Why that extra grant:** snapshot loads use `TRUNCATE TABLE`, which
> needs `ALTER` on the table — `db_datawriter` does not include it.
> Without this, the six snapshot tables fail while the incremental one
> succeeds. Granting `ALTER` on the `stg` schema only is narrower than
> adding the login to `db_ddladmin`.

Keep both passwords in your password manager. They go in `.env` and
nowhere else.

---

## Step 4 — Run the DDL

Still in SSMS as the **admin** login (not the service login — these
create schemas and tables), against **`AusReporting`**, in this order:

```
warehouse/ddl/001_control.sql      -- watermark and run history
warehouse/ddl/002_staging.sql      -- staging tables and their indexes
warehouse/ddl/003_dim_stage.sql    -- the stage lookup
warehouse/ddl/004_dim_date.sql     -- AEST calendar, 2021-2032
warehouse/ddl/005_model_views.sql  -- dim / fct / agg views
```

Order matters — `003` and `005` reference tables that `002` creates.
All five are idempotent, so a re-run is harmless.

Then go back and run the `GRANT ALTER ON SCHEMA::stg` from Step 3, now
that the schema exists.

Check it worked:

```sql
SELECT s.name AS [schema], COUNT(*) AS objects
FROM sys.objects AS o
JOIN sys.schemas AS s ON s.schema_id = o.schema_id
WHERE s.name IN ('ctl','stg','dim','fct','agg')
  AND o.type IN ('U','V')
GROUP BY s.name ORDER BY s.name;
```

Expect `agg` 3, `ctl` 3, `dim` 6, `fct` 3, `stg` 7.

---

## Step 5 — Configure the extract

On FLOWWEB4, in the repo folder, copy `.env.example` to `.env` and fill
it in:

```
AUS_SOURCE_SERVER=fw04-sqlreporting01.database.windows.net
AUS_SOURCE_USERNAME=<your read-only source login>
AUS_SOURCE_PASSWORD=<its password>

AUS_WAREHOUSE_SERVER=aus-reporting-sql.database.windows.net
AUS_WAREHOUSE_DATABASE=AusReporting
AUS_WAREHOUSE_USERNAME=svc_ausreporting
AUS_WAREHOUSE_PASSWORD=<from step 3>
```

The source and warehouse logins are **different**, on different servers.

`.env` is gitignored. Keep it that way — and on a scheduled job, prefer
Key Vault or machine environment variables over a file on disk.

Check the config without connecting to anything:

```powershell
python -m aus_reporting --dry-run
```

---

## Step 6 — First load

Start with the smallest table, to prove the path end to end before
moving 19.8M rows:

```powershell
python -m aus_reporting --only Lenders
```

47 rows. If that works, connectivity, credentials, firewall and the
DDL are all correct.

Then the rest of the small ones:

```powershell
python -m aus_reporting --only AffiliateGroups --only Affiliates --only Leads
```

Then the big ones. `LeadApplicationStages` is 19.8M rows and will take a
while on the first run; afterwards it only reads what is new:

```powershell
python -m aus_reporting
```

---

## Step 7 — Validate

In SSMS against `AusReporting`:

```
warehouse/validate.sql
```

13 checks, each PASS or FAIL with the numbers behind it. **Read the
FAILs** — each one describes a way the numbers could be wrong while
still looking plausible.

Check 7 also answers an open question as a side effect: whether stages
12 and 13 are mutually exclusive, and so whether the funnel has a clean
sold / not-sold split.

---

## Step 8 — Schedule it

Once a manual run is clean, Task Scheduler on FLOWWEB4:

- **Action:** `python.exe`
- **Arguments:** `-m aus_reporting`
- **Start in:** the repo folder
- **Trigger:** hourly, or daily off-peak — the incremental design makes
  frequent runs cheap
- **Run whether the user is logged on or not**, under a service account

Confirm each run afterwards:

```sql
SELECT * FROM ctl.vwFreshness ORDER BY MinutesSinceLoad DESC;
```

---

## If something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| `Cannot open server ... requested by the login` | FLOWWEB4's IP is not allow-listed on the **warehouse** | Step 2 |
| `Login failed for user 'svc_ausreporting'` | User not created in the database, only the login in `master` | Step 3, second block |
| `Cannot find the object ... TRUNCATE permission denied` | The `GRANT ALTER ON SCHEMA::stg` was missed | End of Step 4 |
| `Invalid object name 'stg.Leads'` | DDL not run, or run against the wrong database | Step 4 |
| `Invalid object name 'dim.Stage'` | `003` skipped, or run before `002` | Step 4, in order |
| Snapshot tables load but the incremental one does not | Usually a timeout on the first 19.8M-row pass | Raise `AUS_BATCH_SIZE`, or run `--only LeadApplicationStages` alone |
| Everything times out after idling | Serverless auto-pause cold start | Re-run; consider disabling auto-pause |
| `pyodbc is not installed` | Dependencies or ODBC driver missing | `EXTRACT-RUNBOOK.md`, setup step 4 |

For anything else, the error text is the useful part — send it on.
