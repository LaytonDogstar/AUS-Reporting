# Bank statement data: what is stored

Answers one question — does either database hold the 90 days of
retrieved bank statement data, and at what grain?

Measured 2026-09-22 from `discovery/adhoc/bankstatement_inventory.sql`
run against both databases. Metadata only: row counts from
`sys.partitions`, column names from `sys.columns`. No column value was
read.

---

## 1. The raw transactions are not stored. The derived metrics are.

There is no transaction-level table anywhere. Every row in `Overflow` is
now accounted for:

| Table | Rows | Size |
|---|---|---|
| `LeadApplicationCustomValues` | 67,841,415 | 2.8 GB |
| `LenderApplicationResults` | 31,185,133 | 2.8 GB |
| `CredfinStatus` | 5,211,330 | 1.0 GB |
| `LeadApplications` | 4,445,486 | 2.9 GB |
| `BankStatementSummaries` | 3,909,457 | **4.4 GB** |
| `CredfinLeadStatus` | 1,704,612 | 98 MB |
| `LeadApplicationAccepts` | 1,270,414 | 649 MB |
| `Leads` | 780,850 | 225 MB |
| `LeadScores` | 604,665 | 93 MB |
| `SellHistory` | 592,538 | 43 MB |
| `FundedLeads` | 146,000 | 15 MB |
| 19 smaller tables | ~12,000 | <5 MB |

That totals ~117.7M rows, matching the database's own figure. The 111M
rows previously unaccounted for are `LeadApplicationCustomValues` and
`LenderApplicationResults`, not a transaction ledger.

**This is a good outcome.** The most sensitive material — individual
transaction lines, which reveal health, gambling, relationships and
religion — was never retained. What was kept is the analysis of it.

## 2. Two overlapping affordability tables, not one

`BankStatementSummaries` is far more than the account summary the earlier
findings implied. It is the largest table in `Overflow` by storage.

| | `BankStatementSummaries` | `LeadMetrics` |
|---|---|---|
| Database | `Overflow` | `OverflowReporting` |
| Rows | 3,909,457 | 1,617,269 |
| Size | 4.4 GB | 795 MB |
| Columns | 92 | 82 |
| Grain | per **bank account** | per **application** |
| History | not yet measured | from 2023-01-24 |
| Direct identifiers | `AccountNumber`, `SortCode`, `AccountName`, `Employer` | none |
| Lender outcome | no | `Result`, `LenderTierName` |
| Join keys | `LeadId`, `LeadApplicationId`, `AffiliateId` | `LeadApplicationId`, `AffiliateId` |

They overlap heavily — `GamblingPercentageOfIncome`, `DaysSinceLastSalary`,
`IsInsolvencyExists`, `CentrelinkAsPercentIncomeECD` and others appear in
both. The 2.4:1 row ratio is consistent with roughly two accounts per
applicant.

`LeadMetrics` is the better reporting source: one row per application, no
identifiers to strip, and it already carries the lender outcome. Its only
disadvantage is starting in 2023.

## 3. Shortlist — 22 of the 82 `LeadMetrics` columns

Enough for lead quality and ideal-customer work without taking the rest.

**Keys and context**
`LeadApplicationId`, `AffiliateId`, `DateCreated`

**Applicant**
`Age`, `EmploymentStatus`, `TimeAtEmployer`, `LoanAmount`

**Income**
`TotalMonthlyIncome`, `WagesMonthly`, `CentrelinkAsPercentOfIncome`,
`DaysSinceLastSalary`, `IsLatestIncomePresent`

**Risk**
`GamblingPercentageOfIncome`,
`ConfirmedAndInferredGamblingAsPercentageOfIncome`, `Dishonours`,
`SaccDishonours`, `NumberofDishonours30Days`, `EstimatedActiveSACCLoans`,
`NumberofSaccProviders`, `IsInsolvencyExists`,
`DebtAsPercentageOfIncomeExcludingCashDeposits90Days`,
`IncomeSpentOnDayOfDeposit`

**Outcome**
`Result`, `LenderTierName`

State and postcode are not in `LeadMetrics`; they come from
`LeadApplications` on `LeadApplicationId`.

## 4. Three things worth knowing that were not being looked for

**Retrieval cost is recorded.** `OverflowReporting.BankStatementRetrievals`
holds 1,705,958 rows with `Cost`, `AffiliateId`, `DateCreated` and
`IsSecondTry`. Cost per retrieval by affiliate is directly answerable,
which turns lead quality from a conversion question into a margin one.

**Repeat applicants need no pseudonym scheme.** `Leads` (780,850) against
`LeadApplications` (4,445,486) is ~5.7 applications per lead, and
`LeadId` is on both `LeadApplications` and `BankStatementSummaries`.
Applications can be grouped per person using a key the system already
has, so the salted-hash design proposed earlier is unnecessary.

**Credfin, not Talefin, is the retrieval path.** `CredfinStatus` (5.2M)
and `CredfinLeadStatus` (1.7M) exist; there is no Talefin table. With
`CredfinLanded` on 47% of applications and `Affiliates.TalefinClientSecret`
also present, the likeliest reading is Credfin retrieves and Talefin
scores — but that is inference and needs confirming.

## 5. Personal data

Blocked, unchanged: `BankStatementSummaries.AccountNumber` and
`.SortCode`.

Newly identified:

- `BankStatementSummaries.AccountName` and `.Employer` — personal
  identifiers, restricted tier
- `BankstatementEmailQueue.Email` — a direct identifier in
  `OverflowReporting`, which the earlier finding that the reporting
  database holds no sensitive columns did not catch

`LeadMetrics` carries no direct identifiers in any of its 82 columns.

## 6. Open

1. **What is `LeadApplicationCustomValues`?** 67.8M rows, 58% of
   `Overflow`, never examined. Likely affiliate-supplied custom fields,
   which means it could contain anything, including personal data.
2. **How far back does `BankStatementSummaries` go?** If 2021, it is the
   only long-run affordability history, since `LeadMetrics` starts 2023.
3. **What is `LeadMetrics.Result`?** If it is the lender decision, lender
   acceptance by customer type is answerable from one table.
4. **Why does `LenderApplicationResults` differ between databases** —
   31.2M in `Overflow`, 98.9M in `OverflowReporting`?
5. **`LeadScores` (604,665 rows)** — not examined.
