# Overflow reporting service

Calls the PublicReports API and shows what each report returns. Standard
library only — no pip, no build step.

It exists because `admin.overflowhub.com.au` is a public hostname, so
this half of the work needs no PowerShell and no machine inside the
network. **The SQL database is a different matter**: port 1433 is
firewalled to allow-listed addresses, so nothing hosted here can reach
it. The funnel, the affordability indicators, campaign attribution and
both cost columns all live there and still need a collector running
inside the network.

## The page

`/` is the dashboard: tiles, a daily trend, the funnel and an affiliate
table, with a 7/30/90-day range and an affiliate filter. The look is
`template.html`, a copy of the one in `dashboard/` — that build queries
the source database from inside the network, this one renders the same
page from what the collector stored, so it can live on a public URL.
`dash.py` is the join between them.

**It shows one population at a time.** FF and non-FF lead counts cannot
be added into a single figure, so the page is built per source and says
which it is showing; the switch beside the affiliate filter moves
between them.

`/lenders` is the same look turned to the buying side, reached by the
Affiliates | Lenders switch at the top of either page. Per lender it
shows the three rates to keep from falling — how many offered leads
pass its filters, how many of those it buys, how many sales reach its
own page — as rolling seven-day lines, with its tiers beneath, and
which customers it buys against those it is offered, band by band.
Lenders are not split by source: the lender report covers every source
together. The Dummy test lender and DCS, an offline lender, are left
out.

`/customers` is buyer profiles, laid out as on the US dashboard: every
measure at once — employment, time at employer, income, wages, loan
amount, age, Centrelink share, gambling, payday spending, dishonours,
SACC loans — down the side, the lenders across by revenue, and a Pool
column for each band's share of every lead received. Cells show a
lender's share of its purchases, how many times as common the band is
there as in the pool, or the share of the band it bought, shaded blue
where it over-buys and orange where it under-buys. Selecting a lender
opens its profile: every band with share, pool, index and a trend line
of its share over time, which opens against the pool, with a shift
either way called beyond chance.

The other pages are reached from the links under the tiles:
`/scorecard` ranks affiliates on profit per lead, `/quality` is the
affordability distributions split by lender result, `/pings` is the
ping log, `/affiliates` the funnel per source, and `/collection` is the
collector's own health.

## How it runs

A background thread wakes every 30 minutes, finds days that have not been
collected, and collects a few at a time. Today is never collected — it is
still happening — so the newest day is yesterday, on Sydney time, which is
how the API dates its reports. Nothing is called while
a page loads: the reports take 15–17 seconds each, so the pages only ever
read what was already stored.

`/collect` fills in missing days on demand. `/collect?days=90` backfills.
Measured 14 Oct 2026: about 25 seconds a day across the seven calls, so
30 days took 15 minutes and 90 would take about three quarters of an
hour. The ping log is most of the traffic, at roughly 23 MB a day.
Lead quality per lender tier, added 15 Oct 2026, is the slowest part
since: about sixty calls a day at five to six seconds each, run four at
a time, so roughly a minute and a half a day on top.

**An empty database rebuilds itself.** On start, if nothing has been
collected, the service fills its whole history in one unattended pass.
Nothing stored here is a system of record — the API can regenerate all of
it — so losing the database should cost time, not data. That makes the
Railway volume a convenience rather than a requirement: without one a
redeploy clears the file and the service spends its first three hours
or so rebuilding ninety days, newest first, with nobody pressing
anything.

Mount a volume at `/data` to skip that. The service checks for the
directory and falls back to a local file when it is absent, so there is
no setting to change either way.

## What it stores

Not the responses. For each call it keeps the *shape* — every field
path, its type, and numeric values — with strings reduced to a type and
a length:

```
$.Totals.LeadsSold              number     9,134
$.Rows[0].Accepted              number     418
$.Rows[0].Cost                  number     2.75
$.Rows[0].ApplicantName         string     len 12
$.Rows[0].ApplicantEmail        string     len 27
$.Rows[0].LeadApplicationId     guid       len 36
```

A name, an email or an address can only appear in a string, and none of
that belongs outside Azure. The counts are what answer the open
questions, and they survive intact.

### Collected data

Seven kinds and two lookups, in `store.py`:

**Lender tier figures, stored whole.** A tier name with its counts,
revenue, commission and profit describes a commercial relationship, not a
person. One row per tier per day, plus the report's own totals block kept
separately so a disagreement between the two stays visible.

**Lead quality, stored only as counts per band.** A row of the quality
report is one applicant's affordability, so the applicant never lands
here — the distribution does. Age becomes a band, gambling becomes a
range, and what is kept is how many fell in each, split by the lender's
`Result` so acceptance rate by band is answerable. Categorical fields
keep their value, because a label shared by thousands is not personal
data; a cardinality cap of 50 guards the case where a field turns out to
hold free text after all.

**Affiliate figures, stored per report.** There are two affiliate
reports — the normal one and the internal one — and the dev team
confirmed on 13 Oct 2026 that FF and non-FF affiliates can no longer be
combined into a single affiliate lead count. Both are collected and
stored with the report they came from, shown one after the other, and
never added together. Which report holds which population is not yet
confirmed, so the stored names are `external` and `internal` after the
endpoints rather than after the populations.

**The ping log, stored only as counts.** `LenderApplicationResults`
returns one row per lead application and lender — roughly 99,000 rows
and 23 MB a day, the heaviest call here by an order of magnitude — and
each row carries `LeadId` and `LeadApplicationId`. Neither is stored.
Each day is reduced in one pass to counts per lender, tier and status,
with the distinct applications and leads behind them counted once over
the whole day, because a lead offered to several lenders would otherwise
be counted once per ping.

**The two lookups.** `LenderTiers` maps a tier id to its name and
lender; `Affiliates` maps an affiliate id to its name, group and whether
the source is internal. Both are snapshots, replaced whole on each
collection, and both are what put names on the ping log, whose rows
carry ids alone. `Affiliates` also returns each affiliate's portal
username and password and its Credfin secret key — none of which is
read, let alone stored.

**Lead quality per affiliate and per lender tier.** The same bands,
counted one affiliate or one tier at a time: `LeadQualityReport` takes
an `affiliateId` and a `lenderTierId`. With the tier filter it returns
every lead of the day and what that tier did with it — filtered out,
ignored, declined, errored or sold — so what a lender was offered, let
through and bought is answerable by band. Leads the tier was never
offered come back as `Not Processed` and are not kept. About sixty tier
calls a day at five to six seconds each, run four at a time
(`QUALITY_WORKERS`). The `lenderId` filter is ignored by the API, which
is why this is per tier.

**Per affiliate and lender, from the ping log.** The same rows counted
by affiliate, lender and status, for the drill-down each way round:
picking an affiliate shows the lenders its leads were offered to, what
passed their filters and what they bought, with trend lines; picking a
lender shows the same for each affiliate whose leads it was offered.
About 600 rows a day. Added 15 Oct 2026; a database collected before
then marks its ping-log days uncollected so they are fetched again.

**Per affiliate, from the ping log.** The same rows counted a second
way, by affiliate and status, so what lenders did with a source's leads
can be set against what that source sent. That is what the scorecard
ranks on.

Verified by test against a stub returning `Jane Citizen`,
`jane.citizen@example.com.au`, a GUID and a timestamp on every one of 800
rows a day across five days. None of the four reaches the database or any
rendered page, while `FullTime`, `Decline` and the tier names all do, and
the gambling-against-`Result` cross-tab comes out intact.

Counts below ten are shown as `<10` rather than a number.

## Setting it up on Railway

All in the web UI. Nothing to install.

1. **New Project → Deploy from GitHub repo →** `LaytonDogstar/AUS-Reporting`.
2. **Settings → Source → Root Directory:** `service`

   Required. The repository root has a `requirements.txt` listing
   `pyodbc`, which needs a system ODBC driver and will fail the build.
   Pointing at `service` means Railway only sees this folder.
3. **Branch:** nothing to set. `claude/code-review-tsyca6` is the
   repository's default branch, so Railway selects it and redeploys on
   every push to it.
4. **Variables** — add three:

   | Name | Value |
   |---|---|
   | `APP_PASSWORD` | a password you choose, to open the page |
   | `OVERFLOW_API_USER` | the admin.overflowhub.com.au login |
   | `OVERFLOW_API_PASSWORD` | its password |

   The service refuses to serve anything until `APP_PASSWORD` is set,
   because a Railway URL is public.
5. **Settings → Networking → Generate Domain.** That is your URL.
6. *Optional:* **Data → Add Volume**, mount path `/data`. Without one the
   history resets on each deploy, which costs nothing but a re-run.

Open the URL, enter any username and the `APP_PASSWORD` you set, and
press **Call all seven endpoints**.

## What the page answers

**Is the API actually public?** Every page load makes one call carrying
no credentials, because that question only has a meaningful answer
before any are sent. `401` or `403` is what we want. A green line means
it is protected; a red one means a controller named `PublicReport` is
open to anyone with the URL.

**What do the reports return?** The Swagger definition declares every
response as a bare `object`, so it says nothing about the contents. This
is how we find out.

**Which number is "accept"?** `LenderLeadCountReport` over a date range
gives the business's own sanctioned count. Compare it against
`discovery/adhoc/accept_candidates.sql` over the same range, and
whichever of the three candidates matches is the definition already in
use — the question blocking every conversion figure.

## Local run

```bash
APP_PASSWORD=whatever OVERFLOW_API_USER=... OVERFLOW_API_PASSWORD=... python3 main.py
```

Listens on `$PORT`, default 8080.
