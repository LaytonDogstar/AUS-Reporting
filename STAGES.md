# The funnel: `LeadApplicationStages.StageId`

Labels supplied by the development team, 22 Sep 2026. `StageId` has no
lookup table in either source database, so `dim.Stage` in the warehouse
is the only place they are recorded — it is hand-maintained and must be
updated when the journey changes.

Volumes profiled from 19.76M events, 2021-07-01 to 2026-09-22.

---

## The stages

| Id | Label | Applications | % of Received | Notes |
|---:|---|---:|---:|---|
| 1 | `Received` | 4,443,774 | 100.0% | Exactly one per application. The denominator |
| 2 | `Landed` | 140,624 | 3.2% | 8 of 19 active affiliates |
| 3 | `AcceptedTC` | 191,907 | 4.3% | 7 of 19 affiliates |
| 4 | `RequireBankStatement` | 191,894 | 4.3% | Follows `AcceptedTC` |
| 5 | `CredfinLanded` | 2,089,331 | 47.0% | Credfin bank-statement path |
| 6 | `ProvisoLanded` | 1,229 | 0.03% | Proviso path. One affiliate |
| 7 | `BankStatementExtracted` | 1,681,621 | 37.8% | |
| 8 | `BeginSell` | 3,876,526 | 87.2% | |
| 9 | `SellCompleted` | 3,876,319 | 87.2% | |
| 12 | `Interstitial` | 1,230,891 | 27.7% | A page, not an outcome |
| **10** | **`Offer`** | **356,061** | **8.0%** | **Positive outcome** |
| **13** | **`Decline`** | **692,347** | **15.6%** | **Negative outcome** |
| 11 | `OfferAccepted` | **0** | 0% | **Never emitted** |
| 14 | `DuplicateBankstatement` | 7 | — | Error. Last seen 2023-02-16 |
| 15 | `RefreshBankStatement` | **0** | — | Never emitted |
| 16 | `BankstatementRetry` | 132 | — | Retry. Last seen 2023-05-16 |
| 17 | `NoPrimaryIncomeSource` | **0** | — | Never emitted |

Ordered by observed journey position, not by Id.

## The journey

From 20 sampled recent journeys, all variations of one sequence:

```
Received → [Landed] → [AcceptedTC → RequireBankStatement]
         → CredfinLanded / ProvisoLanded
         → BankStatementExtracted
         → BeginSell → SellCompleted → [Interstitial]
         → Offer  ←→  Decline
```

Real examples:

```
Received → AcceptedTC → RequireBankStatement → CredfinLanded
        → BankStatementExtracted → BeginSell → SellCompleted
        → Offer → BeginSell → SellCompleted → Decline

Received → CredfinLanded → BankStatementExtracted
        → BeginSell → SellCompleted → Interstitial

Received → BeginSell → SellCompleted
```

`BeginSell → SellCompleted` recurring after `Offer` is the PingTree
working through lenders in turn.

---

## Three things that matter for reporting

### `OfferAccepted` has never fired, in five years

The stage exists. It is where a lender accepting an offer would be
recorded. It has **never once been emitted** across 19.76M events.

That is independent confirmation that lenders do not report outcomes
back — the schema anticipated a feed that never arrived. `Offer` at 8.0%
is the furthest the data can see.

`RefreshBankStatement` and `NoPrimaryIncomeSource` have also never
fired, though with less consequence.

### `Interstitial` is a page, not a conversion

At 27.7% of applications it is tempting to read as an outcome. It is a
page shown after the sell process. Counting it as a conversion would
overstate performance more than threefold.

### The funnel must be read per affiliate

The stage set varies by affiliate. Of 19 active in the last 30 days:

| Stage | Affiliates emitting it |
|---|---|
| `Received` | 19 |
| `BeginSell`, `SellCompleted`, `Interstitial` | 18 |
| `CredfinLanded` | 17 |
| `BankStatementExtracted`, `Offer` | 15 |
| `Decline` | 13 |
| `Landed` | 8 |
| `AcceptedTC`, `RequireBankStatement` | 7 |
| `ProvisoLanded` | 1 |

An affiliate that never emits `AcceptedTC` has not dropped out there —
its journey captures consent elsewhere, consistent with affiliate pages
that already collect it. **Aggregate funnel percentages across
affiliates are wrong.** Use `agg.StageFunnelByAffiliateDay`, which
computes reach against `Received` for the same affiliate.

---

## Smaller observations

**`BeginSell` → `SellCompleted` is a reliability signal.** 4,093,727
against 4,093,507 — 220 sells begun that never completed, 0.005%. Worth
monitoring; it is not a business outcome.

**`AcceptedTC` → `RequireBankStatement` behaves the same way**, 17
unmatched out of 192,802.

**`Offer` repeats 1.89× per application.** One application can receive
several offers, presumably from different lenders. Count distinct
applications, not events, for a conversion rate.

**`CredfinLanded` and `ProvisoLanded` are alternative providers**, not
sequential steps. Treat them as one funnel level.

**`Offer` and `Decline` may overlap.** 356,061 and 692,347 respectively,
together 23.6% of applications. One lender offering while another
declines is plausible, so they are not necessarily exclusive.
`validate.sql` check 7 measures the overlap once data is loaded.

---

## What still needs confirming

- **Does `Offer` mean an offer was presented to the customer, or that a
  lender bid?** It changes what "conversion" means.
- **How does `LeadApplicationAccepts` (1.27M) relate to `Offer`
  (356k)?** Accepts outnumber offers by 3.5×, so they are counting
  different things. This matters: the commercial event drives commission
  reporting.
- **What is `Landed`, and why only 8 affiliates?**
