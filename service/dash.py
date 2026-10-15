"""Render the dashboard page from what the collector stored.

Named `dash` rather than `dashboard` so it cannot shadow the repository's
`dashboard/` package, which is the other build - the one that queries the
source database from inside the network.

The look is `dashboard/template.html` - tiles, a trend, a funnel, an
affiliate selector - which was written against the source database and
only runs inside the network. The data is this service's, collected
from the public API and reconciled against the lender report. This
module is the join between them: it builds the payload that template
expects out of counts rather than rows.

`template.html` here is a copy of that file, because Railway serves the
`service` folder alone and cannot see a sibling directory. A test
fails if the two drift.

**The two populations are not mixed.** FF and non-FF affiliate counts
cannot be added into one figure, so the page is built for one source at
a time and says which it is showing.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import store

TEMPLATE = Path(__file__).with_name("template.html")
PLACEHOLDER = "/*__DASHBOARD_DATA__*/null"

# The affiliate report's own funnel steps, given the stage ids the page
# already knows. 10 and 13 are the two it treats as outcomes - the good
# one and the bad one - so sold and declined take those.
FUNNEL = (
    ("received", 1, "Received", 10),
    ("accepted_tc", 3, "Accepted T&Cs", 30),
    ("bs_extracted", 7, "Statements extracted", 60),
    ("submitted", 8, "Submitted to lenders", 70),
    ("sold", 10, "Sold", 90),
    ("decline", 13, "Declined", 90),
)

SOURCE_LABELS = {"external": "External affiliates",
                 "internal": "Internal sources (FF)"}


def stages():
    """Only the steps this data has. The page draws what it is given,
    and a funnel padded with stages that never fire reads as attrition
    that did not happen."""
    return {str(sid): {"label": label, "order": order,
                       "outcome": "good" if sid == 10 else "bad" if sid == 13 else None}
            for _field, sid, label, order in FUNNEL}


def payload(conn, days=30, source="external"):
    cutoff = store.business_cutoff(days)
    rows = conn.execute(
        "SELECT business_date, affiliate_id, affiliate_name, received, accepted_tc,"
        " bs_extracted, submitted, sold, decline FROM affiliate_day"
        " WHERE business_date >= ? AND source = ? AND affiliate_id IS NOT NULL"
        " AND received > 0" + store._not_test(), (cutoff, source)).fetchall()

    applications, accepts, stage_counts = [], [], []
    for day, aid, _name, received, tc, bs, submitted, sold, decline in rows:
        applications.append({"Day": day, "AffiliateId": aid,
                             "Applications": received, "AvgLoanAmount": None})
        accepts.append({"Day": day, "AffiliateId": aid,
                        "ApplicationsSold": sold or 0, "AcceptEvents": sold or 0})
        values = {"received": received, "accepted_tc": tc, "bs_extracted": bs,
                  "submitted": submitted, "sold": sold, "decline": decline}
        for field, sid, _label, _order in FUNNEL:
            n = values.get(field) or 0
            if n:
                stage_counts.append({"Day": day, "AffiliateId": aid, "StageId": sid,
                                     "Applications": n, "StageEvents": n})

    seen = {r["AffiliateId"] for r in applications}
    known = {r[0]: r[1:] for r in conn.execute(
        "SELECT affiliate_id, name, is_internal, is_active FROM affiliate")}
    names = {r[0]: r[1] for r in conn.execute(
        "SELECT affiliate_id, affiliate_name FROM affiliate_day"
        " WHERE affiliate_id IS NOT NULL GROUP BY affiliate_id")}
    affiliates = []
    for aid in sorted(seen):
        name, is_internal, is_active = known.get(aid, (None, None, None))
        affiliates.append({"Id": aid, "Name": name or names.get(aid) or f"Affiliate {aid}",
                           "DisplayName": name or names.get(aid) or f"Affiliate {aid}",
                           "IsActive": bool(is_active), "IsDeleted": False,
                           "IsInternalSource": bool(is_internal)})

    return {
        "generatedUtc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "windowDays": days,
        "reportingTimeZone": "AUS Eastern Standard Time",
        "source": source,
        "sourceLabel": SOURCE_LABELS.get(source, source),
        "stages": stages(),
        "affiliates": affiliates,
        "applications": applications,
        "accepts": accepts,
        "stage_counts": stage_counts,
    }


def render(data, template: Path = TEMPLATE) -> str:
    html = template.read_text(encoding="utf-8")
    if PLACEHOLDER not in html:
        raise ValueError(f"{template}: data placeholder not found")
    # The page is embedded in a <script>, so a closing tag inside the
    # JSON would end it early.
    body = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return html.replace(PLACEHOLDER, body)


def page(conn, days=30, source="external") -> bytes:
    return render(payload(conn, days, source)).encode("utf-8")
