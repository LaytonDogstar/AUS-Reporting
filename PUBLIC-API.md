# The PublicReports API

Reviewed 2026-10-13 from the Swagger definition at
`https://admin.overflowhub.com.au/swagger/docs/v1`.

No credentials were used and no endpoint was called. Everything below
comes from the published definition.

---

## 1. What it offers

Six operations, all under `/api/PublicReports/`:

| Method | Path | Takes |
|---|---|---|
| GET | `Affiliates` | nothing |
| GET | `LenderTiers` | nothing |
| GET | `LenderLeadCountReport` | date range, optional affiliate, affiliate group, lender, lender tier, `allActiveOnly` |
| POST | `AffiliateLeadCountReport` | date range **plus a comparison date range**, arrays of affiliate group, affiliate, lender and lender tier ids, `IsAllActiveOnly`, `ExcludeInternalSources` |
| POST | `InternalAffiliateLeadCountReport` | as above, without the affiliate filters |
| GET | `LeadQualityReport` | date range, optional affiliate, affiliate group, lender, lender tier, **and a single `leadApplicationId`** |

Two details in the request shapes are worth noting on their own.

**Period comparison is a built-in concept.** `CompareStartDate` and
`CompareEndDate` are first-class parameters, so "this month against
last" is already how the business thinks about these numbers. The
dashboard should match that rather than invent its own comparison.

**Internal and affiliate traffic are formally separated.** There is an
`ExcludeInternalSources` flag and an entirely separate
`InternalAffiliateLeadCountReport`. That matches `Affiliates.IsInternalSource`
in the database, and it matches the attribution finding — campaign data
covers 26 of 185 affiliates, almost certainly the internal ones.

## 2. The definition says nothing about what comes back

Every single operation declares its 200 response as:

```json
"schema": { "type": "object" }
```

No field names, no types, nothing. So the definition tells us what each
report can be asked for and nothing whatsoever about what it answers.

**This is the blocker.** "Lead quality" is exactly the material we want,
but whether `LeadQualityReport` returns the affordability indicators,
a single composite score, or a count by some quality band cannot be
determined without calling it. One authenticated call against a narrow
date range answers it.

## 3. What it does not cover

Set against `REPORTING-SCOPE.md`, the API provides lead counts and
whatever "quality" turns out to mean. It has no endpoint for:

- **The funnel.** Nothing exposes `LeadApplicationStages` or the 17
  journey stages. The funnel is the core of the dashboard.
- **Affordability.** No endpoint maps to the 82 `LeadMetrics` columns,
  unless `LeadQualityReport` turns out to be exactly that.
- **Attribution.** No UTM, campaign, keyword or creative anywhere.
- **Cost.** Neither `BankStatementRetrievals.Cost` nor
  `LenderApplicationResults.Cost` has an endpoint. Margin reporting is
  not available through this API.
- **Row-level lender outcomes.** The reports are counts. "What kind of
  customer does Lender A accept" needs the customer attached to each
  decision, which a count cannot carry.

So it is a complement, not a replacement. It does not remove the need
for a collector.

## 4. Where it is genuinely valuable

**It can settle the accept-definition question.** That is the single
thing blocking any published conversion rate: `LeadApplicationAccepts`
says 1,270,433, `SellHistory` says 592,557, the `Offer` stage says
356,000. If `LenderLeadCountReport` returns the business's own sanctioned
count for a given date range, then comparing it against each of the three
candidates identifies which one the business already treats as the truth.
That is an empirical answer to a question we were otherwise going to have
to ask and take on trust.

The same applies to "lead quality". If the business already has a
definition, the dashboard should use it rather than invent a competing
one.

**It is reachable from anywhere.** Unlike the database, which is
firewalled to allow-listed addresses, this is a public hostname. For the
metrics it covers, a hosted dashboard could call it directly with no
collector and no FLOWWEB4. That does not change the overall architecture,
because the funnel, affordability, attribution and cost all still have to
come from the database — but it is worth knowing.

## 5. Security: two things to check

**The definition is readable without credentials.** It was retrieved
anonymously. That on its own is not a vulnerability — many teams publish
their API documentation deliberately — but it means the shape of every
report, and the fact that `LeadQualityReport` accepts a single
`leadApplicationId`, is public.

**The definition declares HTTP Basic authentication, but no operation
references it.** That is ambiguous in a way worth resolving. Either the
tooling simply did not annotate the operations — common with Swashbuckle,
and the likely explanation — or the endpoints under a controller named
`PublicReport` are genuinely open.

If they are open, the exposure is real: an anonymous caller could request
lead counts by affiliate and lender for any date range, and whatever
`LeadQualityReport` returns, from a public hostname. Worth ten seconds to
rule out:

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://admin.overflowhub.com.au/api/PublicReports/Affiliates"
```

`401` or `403` is the answer you want. `200` means it needs attention
before anything else here proceeds.

Separately, Basic authentication is a weak control for a reporting API:
the credential is static, long-lived, carries no scope, and is sent on
every request. It is fine over TLS between two systems you control, but
any logging layer that records request headers captures it, and there is
no way to revoke one consumer without changing it for all of them. If
this API is going to become a dependency, a scoped token with an expiry
is the better shape.

## 6. Recommended next step

One authenticated call to each of `LenderLeadCountReport` and
`LeadQualityReport`, over a narrow date range, with the response captured
to a file. That answers what the reports actually return, and whether the
lender count matches one of our three accept candidates.

Run it from your own machine rather than pasting credentials anywhere.
The response may contain real lead data, so treat the output as
restricted — the fields and shape are what matters here, not the rows.

---

# What the reports actually return

Measured 2026-10-13 from the Railway service, seven-day window
(2026-10-06 to 2026-10-13). All six endpoints returned 200.

**The API requires credentials.** An anonymous call to
`/api/PublicReports/Affiliates` returned 401. The ambiguity in section 5
is resolved: a controller named `PublicReport` is not open.

## 7. `LeadQualityReport` is `LeadMetrics`, served row by row

14,610 rows for seven days — about 762,000 a year. Not a summary.

Nineteen of its twenty fields are `LeadMetrics` columns, two of them
lightly renamed (`Dishonours` → `TotalDishonours`, `SaccDishonours` →
`TotalSaccDishonours`). The twentieth, `HasPrimaryIncomeSource`, is
derived.

The proof is a misspelling. Both the API and `LeadMetrics` column 19
carry **`CenterlinkMonthly`** — "Centerlink", not "Centrelink". A typo
survives being copied; it does not arise twice.

So the affordability material — age, employment, wages, Centrelink
proportion, gambling as a percentage of income, dishonours, SACC loans,
income spent on day of deposit, and the lender `Result` — is already
available over HTTPS, with no firewall and no collector.

**Ten of the twenty-two shortlisted columns are not served**:
`TotalMonthlyIncome`, `DaysSinceLastSalary`, `IsLatestIncomePresent`,
`EstimatedActiveSACCLoans`, `NumberofSaccProviders`,
`IsInsolvencyExists`, `DebtAsPercentageOfIncomeExcludingCashDeposits90Days`,
`NumberofDishonours30Days`,
`ConfirmedAndInferredGamblingAsPercentageOfIncome`, and **`AffiliateId`**.

`AffiliateId` is the awkward one. It is a filter parameter but not a
returned field, so slicing quality by affiliate means one call per
affiliate rather than one call and a group-by. There is also no
geography: no state, no postcode.

## 8. `LenderLeadCountReport` carries the commercial numbers

176 lender tiers (of 191 that exist), each with `TotalIgnored`,
`TotalProccessed` *(sic)*, `TotalSent`, `TotalAccepted`,
`TotalBlacklist`, `TotalRedirect`, `ConversionRate`, `EarningPerLead`,
`ExtraIncome`, `TotalRevenue`, `TotalCommission`, `TotalProfit` and
`TotalBlacklistPrice` — plus a totals block.

For the seven days measured:

| | |
|---|---|
| Ignored | 181,385 |
| Processed | 605,108 |
| Sent | 30,372 |
| **Accepted** | **6,126** |
| Redirect | 5,780 |
| Blacklist | 0 |
| Revenue | 122,813.00 |
| Commission | 54,229.85 |
| **Profit** | **68,583.15** |
| Conversion rate | 20.1699% |

Revenue minus commission equals profit to the cent, so the margin is
simply what is not paid away. That is a 55.8% margin, about 20.05 of
revenue per accepted lead, and roughly 3.58M of profit annualised.

**We did not know the commercial figures were available at all.** The
earlier finding was that cost lived only in the database, in
`BankStatementRetrievals.Cost` and `LenderApplicationResults.Cost`. The
API goes further and gives revenue, commission and profit already
attributed per lender tier.

## 9. The conversion rate is defined, and it is not what we assumed

`TotalAccepted / TotalSent` = 6,126 / 30,372 = **20.169893322797%**,
against the API's reported `20.169893322797314`. An exact match.

So the business measures conversion as **accepted over sent**, not
accepted over applications. The denominator is leads submitted to a
lender, not leads received. Every funnel percentage in the dashboard has
to use that definition or it will disagree with the numbers people
already quote.

## 10. The accept question, narrowed

6,126 accepted in seven days is about 319,000 a year. Against the three
candidates, annualised over the periods each one covers:

| Candidate | Rate | Against the API |
|---|---|---|
| `SellHistory` | ~372,700/year | 0.86x |
| `LeadApplicationAccepts` | ~242,900/year | 1.31x |
| `Offer` stage | ~68,100/year | **4.69x** |

**The `Offer` stage is ruled out.** It is nowhere near, and a conversion
rate built on it would have understated performance roughly fivefold.

Between the other two the annualised comparison cannot decide — they
cover different periods and volume has not been flat. Running
`discovery/adhoc/accept_candidates.sql` over the same seven days settles
it exactly. That is now the only outstanding step on this question.

## 11. Cost of calling it

| Endpoint | Time | Size |
|---|---|---|
| `Affiliates` | 2,310 ms | 137,900 B |
| `LenderTiers` | 1,999 ms | 104,204 B |
| `LenderLeadCountReport` | 15,074 ms | 56,345 B |
| `LeadQualityReport` | 17,173 ms | 9,050,340 B |
| `AffiliateLeadCountReport` | 14,216 ms | 42,620 B |
| `InternalAffiliateLeadCountReport` | 16,365 ms | 1,730 B |

Fifteen to seventeen seconds for the reports. **Nothing can call these
while a user waits.** They run on a schedule and the result is stored,
which is what the service already does.

`InternalAffiliateLeadCountReport` returning 1,730 bytes against
`AffiliateLeadCountReport`'s 42,620 is worth a question: either internal
volume genuinely is that small for the week, or the filter is excluding
almost everything.

---

## 12. `endDate` is inclusive, at day granularity

Measured 2026-10-13, after a 90-day backfill produced figures that could
be checked against a call already made.

A call with `startDate=D` and `endDate=D+1` returns **two days**, not
one. The first collector used that form, so every stored day also
contained the day after it.

The proof is arithmetic. Seven stored days, 2026-10-06 to 2026-10-12,
against the single call already made over 2026-10-06 to 2026-10-13:

| | Days summed | One call | Ratio |
|---|---|---|---|
| Sent | 55,098 | 30,372 | 1.814 |
| Accepted | 11,031 | 6,126 | 1.801 |
| Revenue | 218,384.00 | 122,813.00 | 1.778 |

Not 1.0, and not 2.0 either — which is the giveaway. If each call
returns `d(D) + d(D+1)`, then summing consecutive calls counts the
interior days twice and the two edges once. Unrolling that chain,
`d(D+1) = call(D) - d(D)`, the unknown first day cancels and the implied
eight-day total is fixed:

| | Implied | Actual | Off by |
|---|---|---|---|
| Sent | 30,440 | 30,372 | 0.22% |
| Accepted | 6,136 | 6,126 | 0.16% |
| Revenue | 122,987.00 | 122,813.00 | 0.14% |

Three independent measures landing within a quarter of a percent is not
coincidence. **A single day is `startDate=D, endDate=D`.**

The residual fraction of a percent is the gap between when the range call
was made and when the backfill ran.

### Guarded, not just commented

`/verify` fetches a range in one call, then each of its days separately,
and compares. Against a stub that reproduces the inclusive behaviour, the
corrected window gives a ratio of 1.000 on all three measures and the
original form gives 1.677, reported as a mismatch. A comment would not
have caught this; a check does.

**Two things follow for anything built on this API.** Every date window
needs deciding rather than assuming — the same ambiguity applies to the
affiliate reports and anything added later. And a figure that can be
derived two ways should be, at least once, because this was invisible
until two routes to the same number disagreed.

---

# `AffiliateLeadCountReport`

Measured 2026-10-13, window 2026-10-06 to 2026-10-13 (eight days, given
section 12). 110 affiliates returned.

## 13. It carries the funnel

Per affiliate: `TotalReceived`, `TotalAcceptedTC`, `TotalBsRefreshed`,
`TotalDuplicate`, `TotalBsExtracted`, `TotalSubmitted`, `TotalSold`,
`TotalDecline`, `TotalRedirect`, `TotalFunded`, then `TotalRevenue`,
`TotalCommission`, `TotalBSCost`, `TotalFinalCommission`, `TotalProfit`,
`TotalFinalProfit`, `VolumeComparisonPercentage`.

Those step names map onto the journey stages recovered from the database:
received, accepted T&Cs, bank statement extracted, submitted, sold or
declined.

**So the funnel does not need a database collector after all.** It is
counts per affiliate rather than per-application stage events, so
time-between-stages and individual stage paths still are not available —
but funnel conversion by affiliate, which is what a funnel page shows, is
served directly.

`TotalSold + TotalDecline = TotalSubmitted` exactly (2,797 + 5,590 =
8,387), which is a good sign the steps are internally consistent.

**Read it per affiliate, not in aggregate.** `TotalAcceptedTC` is 2,601
against `TotalSubmitted` of 8,387 — submitted is four times a step that
supposedly precedes it. That is the same finding as the database: only
some affiliates emit an accepted-T&Cs step, so an aggregate funnel mixes
journeys and its step-to-step percentages are not a real sequence.

`TotalFunded` is **0**, confirming from a second source that lenders
report no funding back.

## 14. The two reports disagree on revenue

Same window, same business:

| | Affiliate report | Lender report | Ratio |
|---|---|---|---|
| Revenue | 71,237.00 | 122,813.00 | 1.72 |
| Commission | 54,795.15 | 54,229.85 | 0.99 |
| **Profit** | **16,441.85** | **68,583.15** | **4.17** |

Commission agrees to within 1%. Revenue does not, and profit is more than
fourfold apart.

The grain difference explains the volumes — lender `TotalSent` of 30,372
against affiliate `TotalSubmitted` of 8,387 is 3.62 lender tiers per
application, and 6,126 accepts against 2,797 sold is 2.19 accepts per sold
application. So the lender report counts per application-and-tier while
the affiliate report counts per application. That plausibly inflates
lender-side revenue, but 1.72 does not match 2.19 either, so it is not a
clean explanation.

**Which revenue figure is real is a question for the dev team, and it
blocks any published margin.** The fact that commission agrees while
revenue does not points at the revenue side, but that is inference.

An earlier estimate of roughly 3.1M annualised profit came from the
lender report. On the affiliate report the same period annualises to
about 750k. Until this is settled, neither number should be quoted.

**Half answered 2026-10-13.** The affiliate lead count is two reports,
not one: the normal report and the internal one, which can no longer be
combined into a single figure, and `ExcludeInternalSources` is to be
ignored because FF affiliates carry a special computation behind it. The
collector now calls both and stores them apart. What is still open is
whether the lender report spans both populations — if it does, it cannot
be set against either affiliate figure on its own.

## 15. `TotalFinalProfit` looks wrong

`TotalCommission - TotalBSCost = TotalFinalCommission` holds exactly:
54,795.15 - 521.12 = 54,274.03.

But `TotalFinalProfit` equals `TotalProfit`, both 16,441.85, which is
`Revenue - Commission`. If `FinalCommission` is what is actually paid
away, final profit should be `Revenue - FinalCommission` = 16,962.97.

So the bank statement cost is deducted from commission but never reaches
profit. Final profit understates by the whole `TotalBSCost` — 521.12 over
eight days here, 3.2% of profit. Worth raising: it is a small number now
but it is systematic, and it is the figure most likely to be quoted.

**Answered 2026-10-13.** Old behaviour rather than a bug: the bank statement cost
does not affect profit for a non-FF affiliate. The two profit columns
therefore agree by design for those affiliates, and the figure stands as
reported.

---

# What the quality data says

Thirty days to 2026-10-13, 54,234 leads — about 1,808 a day, matching the
14,610 over eight days measured earlier. Row counts agree exactly across
every metric, so the collection itself is sound.

## 16. `CenterlinkMonthly` is not Centrelink

Three fields describe the same thing and cannot all be right:

| | Leads | Share |
|---|---|---|
| `CentrelinkAsPercentOfIncome` = 0 | 37,714 | 69.5% |
| `CenterlinkMonthly` < $1 | 2,425 | 4.5% |
| `CenterlinkMonthly` ≥ $3,000 | 40,804 | 75.2% |
| `EmploymentStatus` = Centrelink | 6,958 | 12.8% |

Anyone drawing 0% of their income from Centrelink must have $0 of it, so
the second row cannot be smaller than the first. It is short by 35,289.
And more leads are recorded with over $3,000 a month of Centrelink than
report receiving any at all.

The first sampled row settles it: `CenterlinkMonthly` and `WagesMonthly`
were both **5595.96**, identical to the cent. The field is almost
certainly total monthly income under a Centrelink label — note it is also
the one spelled "Centerlink", while the percentage field is spelled
correctly, which suggests different origins.

**Do not use it as Centrelink income.**
`CentrelinkAsPercentOfIncome` and `EmploymentStatus` agree with each
other well enough to be usable; this does not.

**Answered 2026-10-13.** The name was wrong. The field holds wages plus Centrelink, so it is
usable as total monthly income and must never be set against
`CentrelinkAsPercentOfIncome`. It was renamed in the API on 2026-10-14;
the collector accepts either spelling and stores both under one metric,
and the quality page carries the correction above the bands.

## 17. Two more fields that cannot carry weight

**`IsAustralianResident` is unknown for 60.0%** — 32,554 of 54,234 null.
True for 35.3%, false for 4.7%. Not usable as a filter.

**`HasPrimaryIncomeSource` is false for 61.1%.** In a lending funnel that
is implausible on its face, and the related journey stage
(`NoPrimaryIncomeSource`) has never fired once in five years. Either the
flag is inverted, or it means something other than its name. Worth asking
before anyone filters on it.

**Twelve applicants are recorded as under 18.** Small, and most likely
the same date-of-birth corruption seen in the database, where values run
from 1753 to the year 2881. But lending to a minor is a regulatory
problem rather than a data one, so it is worth confirming they are not
real.

**Answered 2026-10-13.** Neither field is broken, and neither is a
population rate. `IsAustralianResident` is only captured for iframe
leads; `HasPrimaryIncomeSource` is only populated for leads whose bank
statements are downloaded, so false means not determined. The twelve
under-18s are test data. The quality page states all three above the
bands rather than leaving the share to be read as the whole funnel.

## 18. The risk signal looks sound

| | Leads | Share |
|---|---|---|
| Gambling over 30% of income | 5,984 | 11.0% |
| Gambling over 5% of income | 12,644 | 23.3% |
| 11+ dishonours | 10,291 | 19.0% |
| 3+ dishonours | 23,977 | 44.2% |
| Wages under $1,000/month | 11,312 | 20.9% |
| Full-time employment | 35,009 | 64.6% |
| Aged 25–34 | 19,675 | 36.3% |

These are plausible, internally consistent and discriminating — a
population where 11% gamble more than a third of their income and 44%
have three or more dishonours is exactly the population these indicators
exist to describe.

## 19. `LenderApplicationResults`, probed without credentials

Added to the API on 2026-10-14. No credentials were available to call
it, but routing alone settles two of the three unknowns, because this
API answers 401 only once a request has matched a route and its
parameters:

| Call | Status |
|---|---|
| `GET LenderApplicationResults?startDate=&endDate=` | **401** |
| `GET LeadQualityReport?startDate=&endDate=` (known good) | 401 |
| `GET LenderApplicationResults` (no parameters) | 404 |
| `GET LeadQualityReport` (no parameters, known good) | 404 |
| `POST LenderApplicationResults` | 404 |
| `GET LenderApplicationResultsReport`, `…Result` | 404 |

So it is a **GET taking the same date window as the other report
endpoints**, not a POST like the affiliate reports, and the name is
exactly `LenderApplicationResults`. A parameterless call 404s on the
known-good endpoints too, so that status says nothing about whether a
route exists — only the parameterised form does.

An authenticated call on 2026-10-14 settled the payload.

**It is a bare top-level array**, not the `Items` envelope every other
report uses — 694,356 rows for a seven-day window, 161.8 MB, 28.9
seconds. That is roughly 99,000 rows and 23 MB a day, an order of
magnitude heavier than anything else collected here.

| Field | Type |
|---|---|
| `LeadId` | guid |
| `LeadApplicationId` | guid |
| `AffiliateId` | number |
| `LenderId` | number |
| `LenderTierId` | number |
| `StatusId` | number |
| `Cost` | number |
| `DateCreated` | date-like |
| `Id` | number |

`StatusId` is the integer the dev team described: 0 FilterFailed, 1
Sold, 2 Decline, 3 Error, 4 Ignored.

**The grain is the ping**: one row per lead application and lender, with
the tier it was offered at and what came back. That is the lender funnel
from the other end — the lender report says what each tier bought, this
says what it was offered and declined, which is the first time a decline
is an event here rather than a residue.

**Two identifiers come with it.** `LeadId` is the applicant key that
question 12 asked for, and `LeadApplicationId` joins to everything else.
Neither is stored: the collector reduces each day to counts per lender,
tier and status in one pass and drops the rows, keeping distinct
application and lead counts per day so "lenders tried per application"
and "applications per lead" are answerable without an identifier leaving
Azure. Repeat-customer reporting beyond that would need either hashed
keys or an aggregate computed inside the network, and that is a decision
rather than an oversight.

**What does not join.** The rows carry `LenderId` and `LenderTierId` but
no names, and the lender report's `LenderTierName` does not come with an
id. Until something maps them, the ping log page can only show numbers.

## 20. What is still missing from the page

All of the above is **marginal** — how many applicants look a certain
way. It does not say which ones are worth having.

The collector already stores every count split by the lender's `Result`,
so acceptance rate by band is in the database. The page was summing over
that dimension and discarding it. Showing it turns "11% gamble heavily"
into "leads gambling over 30% convert at X against Y for the rest", which
is the question the whole engagement started with.
