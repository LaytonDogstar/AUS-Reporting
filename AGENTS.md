# AUS Reporting agents

Six agents keep the dashboard right and explain what it shows. They
are defined in `.claude/agents/`; their scripts are in `tools/agents/`;
their reports go to the dashboard's **Admin → Agent reports**
(`/admin/reports`), and the buyer briefing also heads **Today**.

| Agent | File | When | What it does |
|---|---|---|---|
| Data | `data-checker.md` | daily (scheduled) | The dashboard's numbers against the platform's own reports and against each other; flags and explains any gap |
| Buyer | `buyer-analyst.md` | daily, early morning (scheduled) | Why lenders bought more or less - filters, tiers, traffic and customer mix - written as the morning briefing |
| Testing | `site-tester.md` | after every change, and nightly (scheduled) | The test suite before anything merges; the live site nightly: every page, its data, script errors, sparkline pop-ups, freshness |
| UX | `ux-reviewer.md` | on demand, after a release | Ranked layout and usability recommendations, at most seven |
| Developer | `developer.md` | on demand, per feature | Builds one feature or fix in its own worktree, with tests |
| Manager | `manager.md` | on demand | Briefs the others, reviews and merges their work, verifies it live, reports to Layton |

Reporting is merged into the buyer agent, so one voice writes the
morning briefing.

## What they need

Set in the Claude environment that runs them (never pasted into a
chat):

| Variable | For |
|---|---|
| `AGENT_TOKEN` | every agent: reads the dashboard and posts reports. The same value as the Railway variable of that name, 32+ characters |
| `OVERFLOW_API_USER`, `OVERFLOW_API_PASSWORD` | the data check: the platform's reports, fetched fresh |
| `OVERFLOW_PORTAL_EMAIL`, `OVERFLOW_PORTAL_PASSWORD` | the buyer agent's failed-filter counts (`--filters`) |
| `AUS_DASHBOARD_URL` | optional; defaults to the production URL |

The token reads as a read-only Analyst and can write only reports.

## Running one by hand

From the repo root:

```
python tools/agents/data_check.py --post                 # yesterday and the day before
python tools/agents/data_check.py --date 2026-11-15 --days 1
python tools/agents/site_check.py --post                 # nightly live check
python tools/agents/buyer_data.py --filters > digest.json  # the buyer agent's raw material
python tools/agents/post_report.py --agent buyer --kind briefing --status info \
    --title "..." --summary-file summary.txt --body-file briefing.md
```

Leave off `--post` to print without posting. In a Claude session, ask
for the agent by name ("run the data-checker for yesterday") and it
follows its definition, investigating anything flagged before it posts.

## Schedules

Daily data check, daily buyer briefing (before the morning) and the
nightly site test run as scheduled Claude Code tasks, each in a fresh
cloud session on this repository, Sydney time. Each one runs its
agent's definition and posts its report; nothing they do changes the
dashboard, the platform or the portal.

## Rules every agent keeps

- Read-only on the platform, the portal and the live site. The only
  write is a report.
- Counts and money only: never a lead ID, application ID, name, email,
  phone or bank detail in a report, a file or a log.
- FF and external lead counts are never added together.
- The dashboard rules in `CLAUDE.md` hold for anything the developer
  builds; nothing merges without the tester's run and the manager's
  review.
