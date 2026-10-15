# AUS Reporting

Working rules for this repository. The service is in `service/`, deployed
by Railway from the `claude/code-review-tsyca6` branch; see
`service/README.md`.

## Dashboard rules

- **Every sparkline expands into a pop-up.** Clicking (or Enter/Space on) a
  sparkline opens the full chart in the `#chartPop` dialog: axis, the
  earlier weeks' level, values on hover. This holds for every sparkline,
  including any added later, on any page. `sparkline()` in each template
  throws without an `expand` description, and `tests/test_sparklines.py`
  fails on any call site that does not pass one. (Layton, 15 Oct 2026.)
- Pages: Affiliates (`/`, `template.html`), Lenders (`/lenders`), Customers
  (`/customers`), Today (`/today`) and Yesterday (`/yesterday`, both
  `today.html`) share one stylesheet and the Affiliates | Lenders | Customers
  | Today | Yesterday switch. Today and Yesterday are the US dashboard's views
  of the same name, fitted to Overflow; today is read live every ten minutes.
- Google Ads spend is summed from the internal report's rows (its totals block
  reads 0) and shown for FF as a whole: all of it is booked to one source.
- Trend tables label each measure's columns (Trend, Latest, Change), sort on
  every column, and open sorted by revenue, highest first.
- Lenders: Tippla stays in all reporting, with revenue shown as $0 (its tier
  price is $0). The Dummy test lender (24) and DCS (22, offline) are left out
  via `store.EXCLUDED_LENDER_IDS`. (Layton, 15 Oct 2026.)
- FF (internal) and non-FF (external) affiliate counts from the two affiliate
  reports are never added together; pages built on those reports show one
  population at a time. Today and Yesterday also offer All sources, and open
  on it: their figures come from the ping log, which counts both alike.
- Lead-level identifiers (`LeadId`, `LeadApplicationId`) and the credential
  fields returned by `Affiliates` are never stored.

## Deploying

Railway has no volume, so every push wipes the live database and starts a
rebuild of several hours. Batch changes into as few pushes as possible.
