"""Post a report an agent wrote to the dashboard's Agent reports.

    python tools/agents/post_report.py --agent buyer --kind briefing --status info \\
        --title "Moneyspot bought 15% less" --summary-file summary.txt --body-file briefing.md

The summary is the short version shown in lists and at the top of
Today (for a briefing); the body is the full text. Both are shown as
plain text, escaped. Needs AGENT_TOKEN.
"""

import argparse
from pathlib import Path

from common import post_report

import agentstore                                          # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True, choices=agentstore.AGENTS)
    ap.add_argument("--kind", required=True, choices=agentstore.KINDS)
    ap.add_argument("--status", required=True, choices=agentstore.STATUSES)
    ap.add_argument("--title", required=True)
    ap.add_argument("--summary", default="")
    ap.add_argument("--summary-file")
    ap.add_argument("--body-file")
    a = ap.parse_args()
    summary = Path(a.summary_file).read_text() if a.summary_file else a.summary
    body = Path(a.body_file).read_text() if a.body_file else ""
    print(post_report(a.agent, a.kind, a.status, a.title, summary.strip(), body.strip()))


if __name__ == "__main__":
    main()
