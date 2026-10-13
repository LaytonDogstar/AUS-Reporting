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
