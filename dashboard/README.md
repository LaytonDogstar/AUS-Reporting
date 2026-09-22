# The dashboard

## Updating your copy

One line, from any PowerShell window:

```powershell
iwr -useb https://raw.githubusercontent.com/LaytonDogstar/AUS-Reporting/claude/code-review-tsyca6/dashboard/Update-Pack.ps1 | iex
```

Downloads the current scripts, extracts them, clears the
downloaded-from-the-internet flag, allows scripts in that window, and
leaves you in the dashboard folder. It tells you how many files changed,
or that nothing did.

Safe to run from inside the folder it replaces.


A single HTML file. No server, no port, no login, no database of our
own — a scheduled script runs the queries and writes a page.

Open [`demo.html`](demo.html) to see it. That one is built from
synthetic data shaped like the real thing; no real figures are in it.

## Why a file rather than a web app

The timing test on 22 Sep 2026 settled it: the core funnel query returns
in **5.13 seconds cold** against the source. Fast enough that nothing
needs to be pre-built or warehoused — the page is rebuilt on a schedule
and read from disk.

That removes almost everything that usually needs deciding:

| Not needed | Why |
|---|---|
| A warehouse | The source answers fast enough |
| A web server | It is a file |
| Authentication | File permissions already do it |
| A firewall rule | Nothing listens on a port |
| An internet connection to view it | Nothing is fetched when it opens |

It also means the page can be emailed, put on a share, or opened from a
USB stick, and still work.

## Building it

Two builders, same job. **FLOWWEB4 has no Python, and installing it is a
change to a machine someone else owns — so PowerShell is the one to
use there.**

### PowerShell (nothing to install)

```powershell
.\Build-Dashboard.ps1 -Days 90 -Out C:\reports\dashboard.html
```

Prompts for the SQL login and password. Windows PowerShell 5.1, the
blue console — the same one the discovery scripts ran in.

For Task Scheduler, set the credentials in the environment instead and
it runs unattended:

```powershell
$env:AUS_SOURCE_USERNAME = "svc_reporting"
$env:AUS_SOURCE_PASSWORD = "..."
.\Build-Dashboard.ps1 -Out C:\reports\dashboard.html
```

### Python (where Python exists)

```powershell
python -m dashboard.build --days 90 --out C:\reports\dashboard.html
```

Needs `pyodbc` and the `.env` described in the repository root. Only the
`AUS_SOURCE_*` values are read; nothing writes anywhere.

### They cannot drift apart

Both read the same files:

| File | Holds |
|---|---|
| `sql/*.sql` | Every query. Neither builder contains SQL of its own |
| `stages.json` | Stage labels, funnel order, which database each query uses, the timezone shift |

A test asserts the PowerShell builder contains no inline `SELECT`, so a
query can only ever come from a shared file.

Two values are substituted rather than bound — the timezone shift and
the look-back window — because `pyodbc` uses `?` placeholders and
SqlClient uses `@name`, and no single syntax works for both. Both
builders type them as integers first, and a non-integer is refused
rather than sanitised.

Takes about 15 seconds either way: four queries across two databases,
then the file is written. `-Days 90` sets how much history is embedded;
90 days of six affiliates is roughly 600 KB.

Schedule it hourly in Task Scheduler. The page shows its own build time,
so nobody has to guess how current it is.

## What it shows

**Stat tiles** — applications, sold, offers, declines for the period.

**Daily volume** — applications and sales per day, with a crosshair and
tooltip.

**Funnel** — applications reaching each stage, in journey order. Colour
is reserved for the two outcomes: `Offer` and `Decline`. Every other
stage is one hue, because position and label already say which stage it
is.

**Affiliates** — volume, sales and rates per affiliate, with the
selected one emphasised.

**Data tables** — the numbers behind every chart, for reading or
copying.

## The rule the page enforces

**Funnel percentages appear only when a single affiliate is selected.**

Affiliates run different journeys — only 7 of 19 emit `AcceptedTC` — so
an affiliate that never emits a stage has not dropped out at it. A
combined percentage would read a missing step as a loss, which is simply
wrong.

With "All affiliates" selected the funnel shows counts and says why. It
is the one rule in this project that cannot be left to whoever is
reading the chart.

## What it deliberately does not say

Nothing here is a **funded** figure. Lenders do not report funded
outcomes back — confirmed by `OfferAccepted` never having been emitted
in five years — so the furthest the data sees is the sale to a lender.
The page says "Sold", never "Funded", for that reason.

## Changing it

| To change | Edit |
|---|---|
| The SQL | `sql/*.sql` |
| Stage labels or funnel order | `stages.json` |
| The reporting timezone | `aestShiftHours` in `stages.json` |
| Layout, charts, colours | `template.html` |

Each of those is read by both builders, so a change lands in both.

Colours come from a validated palette: the two series pass colour-vision
separation checks in both light and dark mode, and the funnel uses a
single hue plus reserved status colours rather than a ramp. Changing
them means re-validating.

## Tests

```powershell
python -m unittest discover -s tests
```

No database needed. They check that every query is read-only, that no
banking, credential or personal column is selected, that day grains are
shifted to AEST, that never-emitted and error stages stay out of the
funnel, and that the page embeds its data safely. One test writes
`demo.html` from synthetic data.
