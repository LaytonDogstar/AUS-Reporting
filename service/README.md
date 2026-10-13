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
questions, and they survive intact. Verified by test: with a stub
returning `Jane Citizen` and `jane.citizen@example.com.au`, neither
string reaches the database.

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
press **Call all six endpoints**.

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
