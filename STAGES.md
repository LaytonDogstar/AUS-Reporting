# `LeadApplicationStages.StageId`

`StageId` drives the funnel and has **no lookup table anywhere in either
database** — it is the only stage-related column in either, and nothing
decodes it. This is what the data itself says, so the remaining labels
can be confirmed rather than guessed at from scratch.

Profiled 22 Sep 2026 from `discovery/adhoc/stage_ids.sql` over 19.76M
events, 2021-07-01 to present.

---

## Established facts

### The journey order

From 20 sampled recent journeys, every one of which was a prefix or
variation of the same sequence:

```
1 → [3 → 4] → 5 → 7 → [8 → 9] → 10 → [8 → 9]… → 13
                                              ↘ 12
```

Observed paths, most complete first:

```
1 → 3 → 4 → 5 → 7 → 8 → 9 → 10 → 8 → 9 → 13
1 → 3 → 4 → 5 → 7 → 8 → 9 → 10
1 → 5 → 7 → 8 → 9 → 10
1 → 5 → 7 → 8 → 9 → 12
1 → 3 → 4 → 5 → 5
1 → 8 → 9 → 12
1 → 8 → 9
```

Average position within a journey confirms it: 1 → 2 (1.30) → 3 (2.02)
→ 5 (2.75) → 4 (3.02) → 7 (3.49) → 8 (3.66) → 9 (4.66) → 12 (5.48) →
10 (6.94) → 13 (9.30).

### Stage 1 fires exactly once per application

4,443,774 events across 4,443,774 distinct applications — a ratio of
**1.000**, always at position 1, and matching `Overflow.LeadApplications`
(4,443,646) to within 0.00%. Stage 1 is the application being created.
This one is not in doubt.

### Two request/response pairs

`3 → 4` and `8 → 9` are adjacent in every sampled path, and their
volumes differ by almost nothing:

| Pair | First | Second | Gap | Unanswered |
|---|---|---|---|---|
| 3 → 4 | 192,802 | 192,785 | 17 | 0.0088% |
| 8 → 9 | 4,093,727 | 4,093,507 | 220 | 0.0054% |

A call out and a call back. **The gap is the failure count** — 220
requests in 4.09M that never got a response. That is a free reliability
metric once the pair is labelled.

### Two stages repeat; the rest fire about once

| Stage | Events per application |
|---|---|
| 2 | 1.97 |
| 10 | 1.89 |
| everything else | 1.00 – 1.06 |

Stages 2 and 10 happen roughly twice per application. Combined with
`8 → 9` recurring *after* stage 10 in the longest paths, stage 10 looks
like a loop boundary — try, fail, try the next one.

### When each stage appeared

| Introduced | Stages |
|---|---|
| 2021-07-01 (launch) | 1, 7, 8, 9, 10, 12, 13 |
| 2021-11-04 | 5 |
| 2022-03-09 | 2, 3, 4 |
| 2022-07-08 | 6 |

### Dead and never-used stages

| Stage | Life | Events |
|---|---|---|
| 14 | 2022-11-21 → 2023-02-16 | 7 |
| 16 | 2023-04-05 → 2023-05-16 | 134 |
| 6 | 2022-07-08 → 2026-08-26, one affiliate only | 1,258 |
| **11, 15** | **never emitted at all** | 0 |

11 and 15 were allocated in code and never used — nothing here is
purged, so a retired stage would still show historic rows. 14 and 16
were short-lived experiments. All four should be excluded from funnel
reporting; 6 is a single-affiliate edge case.

### Not every affiliate runs the same journey

Of 19 affiliates active in the last 30 days:

| Stage | Affiliates emitting it |
|---|---|
| 1 | 19 |
| 8, 9, 12 | 18 |
| 5 | 17 |
| 7, 10 | 15 |
| 13 | 13 |
| 2 | 8 |
| 3, 4 | 7 |
| 6 | 1 |

**The funnel has to be read per affiliate, not in aggregate.** An
affiliate that never emits stage 7 has not "dropped out at stage 7" —
that step is not part of its journey. This fits
`Affiliates.JourneyVersion` existing as a column.

---

## Hypotheses, with the evidence for each

To be confirmed by whoever built the journey. Nothing below should be
used as a label until it is.

| Stage | Hypothesis | Evidence | Confidence |
|---|---|---|---|
| 1 | Application created | 1:1 with `LeadApplications`, always first | **Settled** |
| 3 → 4 | Salary pre-check, request and response | Only 7 affiliates emit it, and `Affiliates.EnableSalaryPreCheck` is a per-affiliate flag. Added 2022-03-09, so a later feature. Early position (2.02, 3.02) | Good — and directly testable |
| 7 | Bank statement retrieval | 1,705,395 against `BankStatementRetrievals` at 1,705,256 — 0.01%. Sits after stage 5 | Strong |
| 8 → 9 | Lender call, out and back — likely the PingTree ping/post | Matches `LeadMetadata` (4,093,364) to 0.01%. Recurs after stage 10, consistent with trying successive lenders | Reasonable |
| 10 | Decline, or move to next lender | Repeats 1.89× per application, sits between `8 → 9` cycles | Reasonable |
| 12 | The sale to a lender | 1,242,741 against `LeadApplicationAccepts` at 1,269,729 — 2.17%. Terminal in several paths | Plausible, not tight |
| 13 | The other terminal outcome — pingtree exhausted, no lender took it | Latest average position (9.30), terminal, and follows the retry loop | Plausible |
| 5 | Terms agreed, or landing page reached | Added 2021-11-04, 17 of 19 affiliates, position 2.75 | Weak — volume matches nothing |
| 2 | A retry or notification step | 1.97× per application, 8 affiliates, can occur at position 1 | Weak — absent from all 20 sampled paths |
| 6 | Single-affiliate special case | One affiliate, 1,258 events over four years | n/a |

### The 12-versus-13 question

These look like **competing terminal outcomes**: 1,230,891 distinct
applications reach 12 and 692,347 reach 13, and no sampled journey
contains both. Together that is 1.92M of 4.44M applications — so about
**43% reach a terminal stage and 57% stop somewhere earlier**, which is
itself a headline funnel number once confirmed.

If 12 is the sale, 13 is most likely "nobody bought it". Confirming that
they are mutually exclusive would settle it:

```sql
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

SELECT
    SUM(CASE WHEN has12 = 1 AND has13 = 1 THEN 1 ELSE 0 END) AS both_12_and_13,
    SUM(CASE WHEN has12 = 1 AND has13 = 0 THEN 1 ELSE 0 END) AS only_12,
    SUM(CASE WHEN has12 = 0 AND has13 = 1 THEN 1 ELSE 0 END) AS only_13
FROM (
    SELECT
        LeadApplicationId,
        MAX(CASE WHEN StageId = 12 THEN 1 ELSE 0 END) AS has12,
        MAX(CASE WHEN StageId = 13 THEN 1 ELSE 0 END) AS has13
    FROM dbo.LeadApplicationStages WITH (NOLOCK)
    WHERE StageId IN (12, 13)
      AND DateCreated >= DATEADD(DAY, -30, SYSUTCDATETIME())
    GROUP BY LeadApplicationId
) AS x;
```

`both_12_and_13` near zero means they are competing outcomes and the
funnel has a clean win/lose split.

---

## What this means for the model

1. **`dim_stage` is a hand-maintained table in the warehouse**, not
   extracted. There is nothing upstream to extract it from. It holds
   `StageId`, a label, a funnel order, and an `is_active` flag.
2. **Exclude 6, 11, 14, 15, 16** from funnel reporting.
3. **Funnel percentages must be per affiliate**, or per
   `JourneyVersion`, never aggregate.
4. **The `8 → 9` gap is a reliability metric** worth surfacing — 220
   unanswered calls in 4.09M.
5. Report stage 1 as the denominator: it is exactly one per application.
