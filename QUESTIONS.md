# Open questions

Everything outstanding as at 2026-10-13, grouped by who can answer it.
Numbered so answers can come back as "3 = ...".

Each says what it blocks. Most of the build can continue regardless; the
ones marked **blocking** cannot.

---

## A. Commercial — probably answerable without asking anyone

**1. Which revenue figure is real?** For 2026-10-06 to 13 the affiliate
report says 71,237 and the lender report says 122,813 — a 1.72x gap, on
the same days. Commission agrees between them to within 1%, so it is the
revenue side that disagrees. Profit therefore differs 4.17x.
*Blocking: any published margin, and the scoring target.*

**2. Does "commission" mean money you pay out, or money you take in?**
In the AU data `Profit = Revenue - Commission` exactly, so it reads as
money paid away to affiliates. The US spec sets the scoring target as
"commission ÷ leads", which would then be optimising cost.
*Blocking: the scorecard. The error would be invisible in the output.*

**3. Why did bank statement retrievals fall 55% since 2023?** 1.46M in
2023, 807k in 2024, 748k in 2025, roughly 663k annualised in 2026. Is
that falling volume, or a change in how many accounts are pulled per
applicant?
*Blocks: trusting any trend line that crosses 2023.*

**4. Is internal volume genuinely tiny?** `InternalAffiliateLeadCountReport`
returned 1,730 bytes for a week where the affiliate equivalent returned
42,620. Real, or a filter excluding almost everything?

**5. Which products are in scope** — small-amount, medium-amount,
personal loans — and what loan-amount bands should reporting use?

**6. Who uses this dashboard and who owns it day to day?** The spec wants
four roles (admin, analyst, viewer, client). Today there is one password.
*Blocks: building the roles, which the spec makes a precondition for
lead-level tabs.*

**7. Will lenders ever share funded outcomes?** `TotalFunded` is 0 and
`FundedLeads` has been frozen since October 2024. Two independent sources
saying the same thing.

**8. `MoneyspotLeadKey` appears on 233,848 applications across 56
affiliates.** A lender-side reference. Could that relationship be used to
reconcile funded data?

**9. Is the AU pingtree the same vendor as the US?** The spec asks. The
API is a different shape, but the platform may still be related.

**10. Do AU lenders return an "existing customer" flag on the ping**, and
may it be used? The spec's Module 1 wants it; the US has no equivalent.

---

## B. For the dev team

**11. What does "accept" mean?** Three tables, three numbers:
`LeadApplicationAccepts` 1,270,433 · `SellHistory` 592,557 · `Offer`
stage 356,000. The API rules out the `Offer` stage — 6,126 a week
annualises to ~319,000 against the stage's ~68,000 — but cannot separate
the other two.
*Blocking: every conversion figure. This is the oldest open question.*

**12. Can we have API access to `LenderApplicationResults` and
`Leads.Id`, or a network route to the database?** These are the spec's
ping log and applicant key, and both already exist with five years of
history. They are simply not in the API.
*Blocking: Modules 1 and 4 of the spec.*

**13. What do the `StatusId` values mean** in `LenderApplicationResults`?
There is no lookup table in either database.

**14. Is `LeadMetrics.Age` trustworthy?** We would rather use it than
calculate from `DateOfBirth`, which holds values from 1753 to the year
2881.

**15. What is `CenterlinkMonthly`?** It contradicts
`CentrelinkAsPercentOfIncome` three ways — 69.5% report no Centrelink
income while 75.2% are recorded with over $3,000 a month of it — and in
the first sampled row it equalled `WagesMonthly` to the cent. It looks
like total income under the wrong label.

**16. `HasPrimaryIncomeSource` is false for 61% of leads.** In a lending
funnel. And the matching journey stage has never fired in five years.
Inverted, or does it mean something else?

**17. `IsAustralianResident` is null for 60%.** Is it being populated at
all?

**18. Twelve applicants are recorded as under 18.** Probably the same
date-of-birth corruption, but worth confirming they are not real.

**19. `TotalFinalProfit` never deducts `TotalBSCost`.**
`Commission - BSCost = FinalCommission` holds exactly, but
`FinalProfit` equals `Profit`. Looks like a bug in the API.

**20. 43,240 rows in `LeadApplicationCustomValues` have an empty field
name**, all from one affiliate, written continuously since 14 May 2025.
Something is still saving values with no name attached.

**21. Credfin or Talefin — which retrieves the statements and which
scores them?** Stages record `CredfinLanded` on 47% of applications and
there is no Talefin table, but `TalefinClientSecret` and
`ShowTaleFinScore` both exist.
*Blocks: labelling any "bank statement success rate" correctly.*

**22. Which timezone does the API report in?** The source server runs
UTC. The API's `endDate` is already confirmed inclusive at day
granularity. Both need settling together.

**23. What permissions does the `MaretOverflow` login hold?** Read-only
was measured for `LaytonB` — `db_datareader`, `CONNECT` and `SELECT`
only. That is a property of the login, not the server, and it has since
changed.

---

## C. For the CTO or compliance

**24. May Australian applicant data be processed outside Australia?** The
source server is in Australia Southeast. Railway is not. Nothing
personal leaves Azure under the current design — only counts per band —
but this decides how far that can go.
*Blocking: anything lead-level on Railway.*

**25. What is the agreed list of sensitive attributes** excluded from
scoring, and who signs the scorecard off before it influences routing?

**26. If the score decides routing, does that count as an automated
decision** under the amended Privacy Act, and what must the privacy
policy say?

**27. What is the backup route?** Railway volume backups need the Pro
plan. Backups would contain hashed keys and user accounts.

---

## Already answered, for completeness

- Does the API require credentials? **Yes** — an anonymous call returns
  401.
- Is there a `LeadList` endpoint? **No.** `LeadQualityReport` is the
  nearest and returns a raw `LeadApplicationId`.
- Is the raw bank statement data stored? **No.** Only derived
  indicators; the transaction lines were never retained.
- Where is the source database? **Australia Southeast.**
- Is `endDate` inclusive? **Yes**, at day granularity.
