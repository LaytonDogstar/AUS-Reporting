# Questions and answers

Twenty-seven questions raised on 2026-10-13 from the schema discovery,
the PublicReports API and the AU lead dashboard spec. Twenty were
answered outright the same day and three in part — the commercial ones by
Layton, the dev ones by the dev team. Four are parked.

What is still open is at the bottom. Nothing in it blocks the build.

---

## A. Commercial

**1. Which revenue figure is real?** For 2026-10-06 to 13 the affiliate
report says 71,237 and the lender report says 122,813 — a 1.72x gap, on
the same days. Commission agrees to within 1%, so it is the revenue side
that disagrees. Profit therefore differs 4.17x.
**Half answered.** The affiliate lead count is two reports, not one: the
normal report and the internal one, which can no longer be combined into
a single figure. Whether the lender report spans both populations is
*still open* — see below.

**2. Does "commission" mean money paid out or money taken in?**
**Answered: go with the AU data**, where `Profit = Revenue - Commission`
exactly. It is money paid away to affiliates, so a scoring target of
"commission ÷ leads" would be optimising cost, not value.

**3. Why did bank statement retrievals fall 55% since 2023?** 1.46M in
2023, 807k in 2024, 748k in 2025, roughly 663k annualised in 2026.
**Answered: fewer statements pulled per applicant, not falling volume.**
Nothing to be concerned about.

**4. Is internal volume genuinely tiny?** `InternalAffiliateLeadCountReport`
returned 1,730 bytes for a week where the affiliate equivalent returned
42,620. **Answered: yes, internal volume really is smaller.** Leads are
counted from `TotalReceived`.

**5. Which products are in scope, and what loan-amount bands?**
**Answered in part: band on loan amounts.** The product list — small
amount, medium amount, personal — has not been narrowed.

**6. Who uses this dashboard and who owns it day to day?**
**Answered: Layton only, for now.** The spec's four roles are not needed
yet, which also removes them as a precondition for lead-level tabs.

**7. Will lenders ever share funded outcomes?** `TotalFunded` is 0 and
`FundedLeads` has been frozen since October 2024.
**Answered: no.** Funded outcomes rarely get back to us.

**8. Could `MoneyspotLeadKey` reconcile funded data?** It appears on
233,848 applications across 56 affiliates. **Answered: no.**

**9. Is the AU pingtree the same vendor as the US?**
**Answered: related technology, different environment.**

**10. Do AU lenders return an "existing customer" flag on the ping?**
**Answered in part: reject reasons do come back on the API.** Whether an
existing-customer flag is among them is *still open*.

---

## B. For the dev team

**11. What does "accept" mean?** Three tables, three numbers:
`LeadApplicationAccepts` 1,270,433 · `SellHistory` 592,557 · `Offer`
stage 356,000.
**Answered: `LeadApplicationAccepts` is the accept count.** `SellHistory`
exists to compute daily caps and daily email counts and is purged when
needed; `Offer` is a different scenario, closer to a decline.

**12. API access to `LenderApplicationResults` and `Leads.Id`?**
**Answered, shipped and collected 2026-10-14.** It is a GET on the same
date window as the other reports, returning a bare top-level array of
about 99,000 rows a day: lead, application, affiliate, lender, tier,
status, cost, created. `LeadId` arrives with it, so the applicant key
question is answered by the same endpoint. The collector reduces it to
counts per lender, tier and status; see `PUBLIC-API.md` section 19.

**13. What do the `StatusId` values mean** in `LenderApplicationResults`?
**Answered:**

| `StatusId` | Meaning |
|---|---|
| 0 | FilterFailed |
| 1 | Sold |
| 2 | Decline |
| 3 | Error |
| 4 | Ignored |

**14. Is `LeadMetrics.Age` trustworthy?** **Answered: yes — it is age at
application time**, which is the figure reporting wants. `DateOfBirth`,
which holds values from 1753 to 2881, is not used.

**15. What is `CenterlinkMonthly`?** **Answered: the name was wrong, and
was renamed in the API on 2026-10-14 to `WageAndCenterlinkMonthly`** —
singular "Wage", and it kept the "Centerlink" misspelling. It holds
wages plus Centrelink and cannot be set against
`CentrelinkAsPercentOfIncome`. The collector accepts that name and the
older ones, storing them under one metric, so the series runs through
the rename rather than stopping at it.

**16. `HasPrimaryIncomeSource` is false for 61% of leads.**
**Answered: the feature was only implemented for leads whose bank
statements are downloaded.** False means not determined, so the field is
not a population rate.

**17. `IsAustralianResident` is null for 60%.**
**Answered: it is only captured for iframe leads.** Also not a
population rate.

**18. Twelve applicants are recorded as under 18.**
**Answered: test data.**

**19. `TotalFinalProfit` never deducts `TotalBSCost`.**
**Answered: old behaviour, not a bug.** The bank statement cost does not
affect profit for a non-FF affiliate.

**20. 43,240 rows in `LeadApplicationCustomValues` have an empty field
name**, all from one affiliate, written continuously since 14 May 2025.
**Answered: confirmed a bug.**

**21. Credfin or Talefin?** **Answered: one company, not two.** Credfin
renamed itself Talefin a few years ago; the Talefin score is a feature
added about two years ago. So a "bank statement success rate" is one
vendor's step, whichever name the column uses.

**22. Which timezone does the API report in?** **Answered: Sydney.**
With `endDate` already confirmed inclusive at day granularity, the window
semantics are now settled.

**23. What permissions does the `MaretOverflow` login hold?**
**Answered: read-only.**

---

## C. For the CTO or compliance

**Parked on 2026-10-13** — not being chased. The build carries on as
designed: counts per band leave Azure, applicant rows do not.

**24.** May Australian applicant data be processed outside Australia?
**25.** What is the agreed list of sensitive attributes excluded from
scoring, and who signs the scorecard off?
**26.** If the score decides routing, is that an automated decision under
the amended Privacy Act?
**27.** What is the backup route? Railway volume backups need the Pro
plan.

---

## Still open

- **Does the lender report span both affiliate populations?** If it does,
  it cannot be set against either affiliate figure on its own, and the
  1.72x revenue gap is still unexplained. If it covers only one, that is
  most of the answer. *Blocks: any published margin.*
- **Which products are in scope** (question 5). Bands are on loan
  amounts; the product list is not narrowed.
- **Is an existing-customer flag among the reject reasons** the lender
  API returns (question 10)?

## Settled earlier, for completeness

- Does the API require credentials? **Yes** — an anonymous call returns
  401.
- Is there a `LeadList` endpoint? **No.** `LeadQualityReport` is the
  nearest and returns a raw `LeadApplicationId`.
- Is the raw bank statement data stored? **No.** Only derived
  indicators; the transaction lines were never retained.
- Where is the source database? **Australia Southeast.**
- Is `endDate` inclusive? **Yes**, at day granularity.
- How is conversion defined? **Accepted ÷ sent**, where sent is lead x
  lender tier.
