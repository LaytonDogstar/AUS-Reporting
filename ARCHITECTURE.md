# Reporting architecture

A proposal, grounded in what discovery actually found (see
[`SCHEMA-FINDINGS.md`](SCHEMA-FINDINGS.md)). Decisions are stated with
the constraint that forced them, so they can be argued with.

---

## The constraints that decide everything

1. **Cross-database joins do not work.** Azure SQL Database,
   EngineEdition 5. Lead identity lives in `Overflow`, metrics live in
   `OverflowReporting`, and no single query can span them.
2. **There are no useful indexes.** Every user index in both databases
   is the clustered PK on `Id`. No index covers `LeadApplicationId`; no
   date column leads an index. Every join and every date filter is a
   full scan.
3. **Access is read-only** (`db_datareader`). We cannot add the indexes
   that would fix point 2, and should not want to — they would change a
   replication subscriber that something else owns.
4. **Volumes are real.** 65.5 GB / 378M rows in `OverflowReporting`,
   14.7 GB / 117M rows in `Overflow`.

### Measured, 22 Sep 2026 — this is weaker than it first appeared

The core funnel query — daily counts by affiliate and stage over 30
days, scanning all 19.76M rows of `LeadApplicationStages` — returns in
**5.13 seconds**.

That is far better than expected and it changes the conclusion. Five
seconds is fine for a scheduled Power BI import refresh, and tolerable
even interactively. **The performance argument for a warehouse does not
hold for this workload at this size.**

What survives the measurement:

- **Cross-database joins still do not work** (point 1). But Power BI can
  import from both databases separately and relate them in its own
  model, which solves it without a warehouse.
- **`FailedFiltersV2` is untested and 10x larger** — 194M rows, 26.9 GB
  against the 1.2 GB just measured. If failed-filter analysis is ever
  needed, that query is likely a different story.
- **Logic in version-controlled SQL rather than inside a `.pbix`** is a
  governance preference, not a technical blocker.

**Revised recommendation: start with Power BI importing from both
sources.** Cheaper, faster to stand up, no infrastructure. Revisit the
warehouse when one of these appears: `FailedFiltersV2` analysis is
needed, refresh times grow uncomfortable, several people need shared
governed definitions, or the load on the source becomes visible.

Little of the work so far is wasted either way — the model views become
Power BI's source queries almost directly, and the schema findings and
stage definitions carry over whole.

The rest of this document describes the warehouse design, which remains
the right answer if and when those conditions arrive.

---

## Shape

```
  Overflow ─┐
            ├─► extract ─► warehouse (staging → model) ─► Power BI
OverflowRep ┘   (ours)          (ours, indexed)
```

Four layers, each with one job:

| Layer | What | Owned by |
|---|---|---|
| Source | The two Azure SQL databases | Not us. Read-only, unchanged. |
| Extract | Incremental copy, inside the network boundary | This repo |
| Warehouse | New Azure SQL DB: `stg_` raw landing, then `dim_`/`fct_` model | This repo |
| Semantic | Power BI model and reports | This repo where it can be |

---

## The extract, and why it is cheap

The thing that makes this affordable is that **the big tables have
`bigint`/`int` IDENTITY primary keys, and the PK is the clustered
index.**

```sql
SELECT ... FROM dbo.FailedFiltersV2
WHERE Id > @last_seen_id
ORDER BY Id;
```

That is a clustered index seek. It costs the same against 194M rows as
against 1M. Date-based incrementals would scan, because no date column
is indexed — **so the watermark must be `Id`, not `DateCreated`.**

Coverage:

| | Watermarkable (`Id` is IDENTITY) | GUID-keyed |
|---|---|---|
| `OverflowReporting` | 27 tables, incl. all the multi-million ones | 3 |
| `Overflow` | 20 tables | 5 |

Every table over 5M rows is watermarkable. The GUID-keyed tables are the
smaller ones — the largest is `LeadApplications` at 4.4M rows / 2.9 GB.

So two extraction modes:

- **Incremental (`Id` watermark)** for the IDENTITY tables. Runs as
  often as we like; each run reads only what is new.
- **Full snapshot** for the GUID-keyed tables and the small
  configuration tables. Nightly, off-peak.

### The limitation of an Id watermark

**It catches inserts, not updates.** A row edited in place keeps its
`Id` and will not be re-read.

Mitigation, by table type:

- **Configuration tables** (`Lenders`, `Affiliates`, `PingTrees`,
  `CommissionOverrides`, …) — all tiny, hundreds of rows. Full snapshot
  every run. Cost is nil.
- **Mutable tables with a `uniqueidentifier` key**
  (`LeadApplications`, `LeadApplicationAccepts`, `Leads`) — full
  snapshot. They have no usable watermark anyway, so the question does
  not arise; the largest is 4.4M rows and a subset of columns.
- **Append-only event tables** (`FailedFiltersV2`,
  `LeadApplicationStages`, `ApiErrors`, `LeadRedirects`, …) — inserts
  only by nature. Watermark is sufficient.

Any table where this assumption is wrong produces quietly stale
reporting, so the assumption is worth confirming per table rather than
inherited from this document.

### Replication lag

The source runs ~2 minutes behind live. Irrelevant for daily reporting,
fatal for anything claiming to be real-time. The warehouse should stamp
each load with its extract time, and reports should show "data as at"
rather than implying live.

---

## The model

`LeadApplicationId` is the grain — 18 tables in `OverflowReporting` and
7 in `Overflow` carry it. The model is built around it.

```
dim_lead ──┐
           ├── fct_lead_application  (one row per LeadApplicationId)
dim_affiliate ─┤        │
dim_lender ────┤        ├── fct_application_stage   (funnel)
dim_date ──────┘        ├── fct_lender_result       (pingtree outcomes)
                        ├── fct_accept              (the sale - see below)
                        ├── fct_lead_metrics        (2023-01-24 onward)
                        └── agg_failed_filter_daily (aggregated, not raw)
```

### The funnel ends at the sale, not at funding

Lenders do not report funded outcomes back, so **funded data does not
exist to be reported on.** That is confirmed rather than assumed:
`FundedLeads` stopped being written in October 2024, and
`LeadFundedStatuses` holds 1,269,731 rows against just **5** rows in
`LeadFundedStatusHistories` — statuses are created per accept and then
never transition.

So the measurable conversion event is `LeadApplicationAccepts`: the lead
being sold to a lender. That is also the commercial event, since revenue
is commission on the sale. `fct_accept` is the end of the funnel, and no
metric should be named or described as "funded".

If lender funding feeds are ever obtained, they arrive as a new source
and a new fact table; nothing in this design needs to change to
accommodate that.

Two further deliberate choices:

**`FailedFiltersV2` is aggregated, not copied.** 194M rows and 26 GB to
support "why did leads fail" questions that are almost always asked at
daily-count-by-filter-and-lender level. Copying it row-for-row doubles
the storage bill to answer a question nobody asks that way. Aggregate on
extract; keep raw only if a real use case appears.

**`dim_lead` holds no PII.** See below.

---

## Personal data

The reporting database contains **no banking columns at all**. The only
account-level data found is in `Overflow`:
`BankStatementSummaries.AccountNumber` and `.SortCode`.

**Neither is extracted. Ever.** There is no reporting question that
needs an account number, and the cheapest way to never leak one is to
never copy it.

`dim_lead` carries a surrogate key plus non-identifying attributes —
state, age band, affiliate — and leaves names, email, mobile, date of
birth and address in the source. If a genuine need for identifiers
appears (a support lookup, say), it is a separate, access-controlled
path, not a column on a dimension every report can reach.

`LeadMetrics` is the exception worth noting for good reasons: 82 columns
of derived affordability and risk indicators with no direct identifiers
beyond `LeadApplicationId`. It is the richest reporting material here
and carries the least risk.

---

## Time

**The server runs UTC** and the data is Australian. Two consequences:

- The warehouse stores UTC throughout. Conversion happens once, in the
  semantic layer.
- **The reporting day is AEST**, as decided 2026-09-21. `dim_date` is
  built against AEST and every daily figure means an AEST day.

> **One thing to confirm: AEST fixed, or Sydney local time?**
> Taken literally, AEST is UTC+10 all year, which is what Queensland
> observes. Sydney and Melbourne shift to AEDT (UTC+11) for daylight
> saving, and many businesses say "AEST" when they mean "our local
> time".
>
> It matters for about five months a year: with fixed UTC+10 a lead at
> 00:30 Sydney summer time lands on the previous reporting day. The
> model currently implements **fixed UTC+10**, matching the literal
> reading. Switching to `Australia/Sydney` is a one-line change in
> `dim_date` but restates history, so it is worth settling before
> anyone compares numbers to another system.

---

## Traps the model must encode

These are established facts from discovery, and each one silently
produces wrong numbers rather than an error:

| Trap | Handling |
|---|---|
| `AffiliateRawData` ends 2025-11-28, `AffiliateRawDataV2` begins the same day | Union both in staging; never read one alone |
| `FundedLeads` frozen since Oct 2024 | Do not use. Lenders do not report funding; the funnel ends at the sale |
| `LeadMetrics` starts 2023-01-24 | Metric definitions must state this; no YoY before 2024 |
| `DateOfBirth` / `MoveInDate` contain 1753 sentinels and dates up to 5687 | Filter to a sane range before any age calculation |
| Config `DateCreated` is a 2025-03-04 migration stamp | Never use to date a lender or affiliate relationship |
| `OfflineConversions` dead since 2023-11-12 | Exclude, or label clearly as historic |

---

## Where it runs

The extract must run inside the network boundary — the databases are IP
restricted and reachable via FLOWWEB4.

**Recommended: a Python job in this repo, on a schedule.** Version
controlled, reviewable, testable against a local SQL Server, and
portable. Start it on FLOWWEB4 under Task Scheduler; move it to an Azure
Container App Job or Function in the VNet when it earns the right.

**Alternative: Azure Data Factory.** Managed, native, has watermarked
copy built in, no VM to babysit. The trade is that pipeline logic lives
in the portal rather than in git, which makes review and change history
harder. If the team would rather not own a scheduled job, this is the
reasonable choice.

Either way:

- The extract runs as a **dedicated read-only service login**, not a
  named person's account.
- Credentials live in Key Vault. Never in the repo, never in a config
  file.
- The warehouse is a **new Azure SQL Database** — same platform, same
  admin skills, cheap at this volume. Synapse and Fabric are not
  warranted by 80 GB of source, most of which we are not copying.

---

## Open questions that block build

1. ~~Where did funding data go after October 2024?~~ **Answered:**
   nowhere. Lenders do not provide it. The funnel ends at the sale; see
   above.
2. ~~What is the business timezone for a reporting day?~~ **Answered:**
   AEST. Confirm whether that means fixed UTC+10 or Sydney local time
   including AEDT — see section "Time".
3. **Which metrics matter, and who owns their definitions?** The model
   above is a shape, not a specification. Conversion rate, cost per
   sale, affiliate quality — each needs an owner and a written
   definition before it goes on a dashboard.
4. **Is `LeadMetrics.Age` trustworthy** where `LeadApplications.DateOfBirth`
   is not?
5. **What does `SortCode` hold?** A UK term in an Australian system.

Question 3 decides what gets built next. The rest can be answered as the model grows.

Also open: **`StageId` in `LeadApplicationStages` has no lookup table**
anywhere in either database — it is the only stage-related column in
either, and nothing decodes it. The meaning of each value has to come
from the business before the funnel can be labelled.
`discovery/adhoc/stage_ids.sql` profiles the values to make that
conversation concrete.

---

## Suggested sequence

1. Answer open questions 1 and 2.
2. Stand up the warehouse database and the extract for the spine only —
   `LeadApplications`, `Leads`, `Affiliates`, `Lenders`. Small, fast,
   proves the pattern end to end.
3. Add `LeadApplicationStages` and `LeadApplicationAccepts` — that is
   enough for a funnel and a conversion view, which is most of what
   anyone wants.
4. Add `LeadMetrics` for affordability and quality reporting.
5. Aggregate `FailedFiltersV2` only once someone asks a question that
   needs it.

Steps 2 and 3 are where the value is. Everything after is refinement.
