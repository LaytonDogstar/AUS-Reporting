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
- Trend tables label each measure's columns (Trend, Latest, Change), sort on
  every column, and open sorted by revenue, highest first.
- FF (internal) and non-FF (external) affiliate counts are never added
  together; pages show one population at a time.
- Lead-level identifiers (`LeadId`, `LeadApplicationId`) and the credential
  fields returned by `Affiliates` are never stored.

## Deploying

Railway has no volume, so every push wipes the live database and starts a
rebuild of several hours. Batch changes into as few pushes as possible.
