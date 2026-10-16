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
- Pages: Today (`/today`), Yesterday (`/yesterday`), Day (`/day`) and Days
  (`/days`), all `today.html`, then Affiliates (`/`, `template.html`),
  Lenders (`/lenders`) and Customers (`/customers`) share one stylesheet and
  one tab bar, `main.page_tabs()`, put in place of `<!--__TABS__-->`: Today |
  Yesterday | Day | Days first, then Affiliates | Lenders | Customers, each
  with its date or range beside it. Today and Yesterday are the US
  dashboard's views of the same name, fitted to Overflow; today is read live
  every ten minutes. Day (any finished day, `?date=`) and Days (the range)
  are the same view without the intraday charts (`dash.period_payload`): one
  point a day, each day against its own typical day.
- Dark mode is the default on every page, whatever the browser's setting;
  the Light/Dark button's choice is remembered in the browser
  (`localStorage` "theme") and holds across pages.
- The range is the US dashboard's: last 7, 14, 28, 30, 56 or 90 days, or
  custom dates. It is chosen once and remembered in a cookie across every
  period page, Days included; Today, Yesterday and Day are one day each and have none. A custom
  range bounds every windowed query through `win_end()`, so a new windowed
  query needs `AND business_date <= win_end()` beside its `>= ?`.
  Trend lines read back at least 28 days (`store.trend_cutoff()`), so a
  7-day range still has a line and an earlier level; totals stay on the
  range itself.
- Google Ads spend is summed from the internal report's rows (its totals block
  reads 0) and shown for FF as a whole: all of it is booked to one source.
- Today, Yesterday, Day and Days carry a Profit tile from the two affiliate reports
  (both read live for today): external profit, FF profit after Ads spend, or
  for All sources the two added - profit is money, so adding it is sound
  where adding the reports' lead counts is not.
- Trend tables label each measure's columns (Trend, Latest, Change), sort on
  every column, and open sorted by revenue, highest first.
- Lenders: Tippla stays in all reporting, with revenue shown as $0 (its tier
  price is $0). The Dummy test lender (24) and DCS (22, offline) are left out
  via `store.EXCLUDED_LENDER_IDS`. (Layton, 15 Oct 2026.)
- FF (internal) and non-FF (external) affiliate counts from the two affiliate
  reports are never added together. The Affiliates page opens side by side
  (`dash.payload_both`): every row carries its population, each figure is
  worked out per population and shown next to the other - two values in a
  tile, two daily charts, two funnels, one trend table with FF marked - or
  one population alone by the switch. The day views offer All sources,
  and open on it: their figures come from the ping log, which counts both
  alike, so adding them there is sound.
- A sparkline's pop-up shows every measure of its row, the clicked one first:
  the trend tables on Affiliates and Lenders, the who-bought-from-whom tables,
  and every row on the day views (each hour beside the running figure on
  Today and Yesterday; each day against its typical day on Days). The sparkline itself stays one line.
- Lead-level identifiers (`LeadId`, `LeadApplicationId`) and the credential
  fields returned by `Affiliates` are never stored.

## Access

- Every page needs a login (`service/auth.py`): Viewer (dashboard pages),
  Analyst (those plus diagnostics, read-only), Admin (everything, plus
  actions and `/admin`). A new page must be added to `auth.VIEWER_PATHS` or
  `auth.ANALYST_PATHS`, or only admins can open it.
- Every action is a POST carrying the session's form token (`post_button`),
  never a GET link.
- `APP_PASSWORD` signs in as the break-glass `admin` and is never stored.

## Deploying

Logins live in `auth.db` beside the data and need the Railway volume at
`/data`. Without the volume every push wipes the live database, logins
included, and starts a rebuild of several hours. Batch changes into as few
pushes as possible.
