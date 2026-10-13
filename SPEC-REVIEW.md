# Review: AU Lead Dashboard Spec

Reviewed 2026-10-13 against what has been measured in `SCHEMA-FINDINGS.md`,
`BANKSTATEMENT-DATA.md` and `PUBLIC-API.md`, and against what is already
running at `aus-reporting-production.up.railway.app`.

The spec is sound and most of it should be built as written. This covers
only where measurement changes the answer.

---

## 1. The two hardest dependencies already exist

The spec treats the per-lender ping log and repeat-applicant identifiers
as things to ask the AU platform for, with Phases 2 to 4 gated on them.
**Both are already in the database, with five years of history.**

### The ping log

`OverflowReporting.LenderApplicationResults` — **98,941,245 rows**, one
per lead per lender offered:

`LeadId`, `LeadApplicationId`, `AffiliateId`, `AffiliateName`,
`LenderId`, `LenderTierId`, `LenderTierName`, `StatusId`, `DateCreated`,
`LoanAmount`, `Cost`, `Email`

That is the spec's `ping_fact` table, already populated back to 2021.
`StatusId` carries the response. Decline reasons exist too:
`FailedFiltersV2` (194.3M rows) decoded through `FailedFiltersLookup`
(1,347 rows of `Message`).

Not present: price offered as distinct from `Cost`, response time, and
any existing-customer flag. Those three genuinely do need asking for.

### The applicant key

`Leads.Id` already groups applications by person — **780,863 leads
against 4,445,535 applications, 5.7 each**. The spec's bridging problem,
the one that cost the US build weeks of Excel exports, does not exist
here.

The spec is still right that it must be re-keyed on export. `LeadId` is a
stable identifier linking one person's applications, so it is personal
information by the spec's own reasoning. HMAC it on the way out with a
key held in env, exactly as described — but as a privacy control, not as
a workaround for missing data.

**This reframes the plan.** The question is not whether the platform will
build these. It is how to reach what is already there — which lands back
on the firewall, because none of it is in the API.

## 2. Open questions that are already answered

**Which platform, and is it the same report API as the US?** The AU
platform is Overflow, `admin.overflowhub.com.au`. Six endpoints under
`/api/PublicReports/`: `Affiliates`, `LenderTiers`,
`LenderLeadCountReport`, `LeadQualityReport`,
`AffiliateLeadCountReport`, `InternalAffiliateLeadCountReport`. Same
family as the US names, not the same set. All six are measured in
`PUBLIC-API.md`. Basic auth; credentials are required (an anonymous call
returns 401).

**Is there a `LeadList` endpoint?** No. The nearest is
`LeadQualityReport`, which returns one row per application — 14,610 for
seven days — with affordability indicators and the **raw**
`LeadApplicationId`, not a hash.

**Must data be hosted in Australia?** Still a question for counsel, but
the fact is now known: the source server resolves to **Australia
Southeast**. Railway is not in Australia, so this is a live cross-border
question rather than a theoretical one.

**Which time zone defines the business day?** Still open, and there is a
related trap now measured: the API's `endDate` is **inclusive at day
granularity**, so a call for D to D+1 returns two days. The source server
runs UTC. Both need settling together.

**Volume sizing and backups.** A volume is now mounted. Everything
collected so far is counts, so it is tiny; the spec's 1 GB floor is the
right planning number once `lead_fact` and `ping_fact` arrive.

## 3. Where the spec conflicts with what the API actually reports

### Metric definitions will not survive contact

The spec fixes definitions on day one, which is right. But the AU API
already has its own, and they differ:

- **Conversion is accepted ÷ sent**, not accepted ÷ received. Measured
  exactly: 6,126 / 30,372 = 20.169893322797%, matching the API's own
  `TotalConversionRate` to twelve decimal places. "Sent" means lead ×
  lender tier, so the denominator is pings, not leads.
- **The two reports disagree on revenue** for the same window: 71,237
  from the affiliate report against 122,813 from the lender report, while
  commission agrees to within 1%. Profit is 4.17x apart.

Fixing names is not enough. **Which revenue figure is real has to be
settled before any scorecard targets it**, because the target metric is
revenue-derived.

### "Commission" may mean the opposite of what the US build means

The spec sets the scoring target as "$ per lead received (commission ÷
all leads)". In the AU data, `TotalProfit = TotalRevenue -
TotalCommission` exactly, so **commission is money paid away, not money
received**. A scorecard fitted to commission per lead would be optimising
the cost side.

If the US "commission" is income, the term inverts between markets. Worth
pinning before the first fit, because the error would be invisible in the
output — the model would simply learn the wrong thing.

### `TotalFinalProfit` is miscalculated

`TotalCommission - TotalBSCost = TotalFinalCommission` holds exactly.
But `TotalFinalProfit` equals `TotalProfit`, so the bank statement cost
never reaches profit. Understates by the whole `TotalBSCost`. Small now,
but systematic.

## 4. The AU build has an attribute set the US does not

The spec's lead-list fields are the US ones: loan amount, purpose,
income, employment, residential status, tenure, state, age band.

`LeadQualityReport` adds twenty affordability fields the US build has no
equivalent for — gambling as a percentage of income, dishonours, SACC
loan counts and recency, Centrelink proportion, income spent on day of
deposit. These are `LeadMetrics` served row by row (proven by a shared
`CenterlinkMonthly` misspelling), and thirty days of them show real
discrimination: 11% of applicants gamble more than 30% of income, 44%
carry three or more dishonours.

**This is probably the single biggest scoring advantage the AU build
has**, and the spec does not mention it. It should be in the attribute
list for Module 3.

Three of the fields are not usable and are documented in `PUBLIC-API.md`:
`CenterlinkMonthly` contradicts `CentrelinkAsPercentOfIncome` three ways,
`IsAustralianResident` is null for 60%, and `HasPrimaryIncomeSource` is
false for 61%.

## 5. What is already built

Against the phased plan, roughly Phase 1 with parts of Phase 2:

| Spec | Status |
|---|---|
| Aggregate report feed, stored daily | Done — 90 days, self-rebuilding |
| PII-free banded store | Done — counts per band, no row survives ingest |
| Lead quality by band, split by lender result | Done |
| Affiliate funnel, received to sold | Done |
| Lender tier performance | Done |
| Stdlib Python, SQLite, Railway volume, no build step | Done |
| Four roles | Not started — one shared password today |
| `lead_fact`, `ping_fact`, `applicant_seen` | Not started |
| Scoring | Not started |

The access model is the gap that matters first. The spec restricts
lead-level tabs to admin and analyst, and the analyst role is what a
cloud session would log in as. Today there is one password and one view.

## 6. Suggested changes to the plan

1. **Re-aim Phase 0.** Stop asking the platform for a ping log and an
   applicant key. Ask instead for either API access to
   `LenderApplicationResults` and `Leads.Id`, or a route for a collector
   to reach the database. The data exists; only the path to it does not.
2. **Settle the revenue discrepancy before Phase 3.** No scorecard should
   be fitted to a target that two reports disagree about by 1.72x.
3. **Add affordability to the Module 3 attribute list**, less the three
   broken fields.
4. **Decide the timezone and the window semantics together.** The
   inclusive `endDate` already corrupted a 90-day backfill once; it was
   caught by two routes to the same number disagreeing.
5. **Build the roles before lead-level data lands**, not after. It is
   cheap now and the spec makes it a precondition for the lead-level tabs.
