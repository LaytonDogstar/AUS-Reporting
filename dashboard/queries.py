"""The SQL the dashboard runs.

Kept apart from everything else so the queries can be read, reviewed and
run by hand without touching Python.

All of it is read-only and runs under READ UNCOMMITTED, so it cannot
block the replication subscriber it reads from.

TIME. The source server runs UTC. The reporting day is AEST, so every
date is shifted +10 hours before being truncated to a day. Changing to
Sydney local time including AEDT means changing AEST_SHIFT_HOURS here
and nowhere else.
"""

from __future__ import annotations

AEST_SHIFT_HOURS = 10

# Applied to a UTC datetime column to get the AEST calendar day.
def aest_day(column: str) -> str:
    return f"CAST(DATEADD(HOUR, {AEST_SHIFT_HOURS}, {column}) AS date)"


# --- OverflowReporting ------------------------------------------------

STAGE_COUNTS_BY_DAY = f"""
-- One row per AEST day, affiliate and stage: how many applications
-- reached that stage. The funnel, at its natural grain.
SELECT
    {aest_day('DateCreated')}          AS Day,
    AffiliateId,
    StageId,
    COUNT(DISTINCT LeadApplicationId)  AS Applications,
    COUNT(*)                           AS StageEvents
FROM dbo.LeadApplicationStages WITH (NOLOCK)
WHERE DateCreated >= DATEADD(DAY, ?, SYSUTCDATETIME())
GROUP BY {aest_day('DateCreated')}, AffiliateId, StageId;
"""

# --- Overflow ---------------------------------------------------------

APPLICATIONS_BY_DAY = f"""
-- Application volume and loan size per AEST day and affiliate.
SELECT
    {aest_day('DateCreated')}  AS Day,
    AffiliateId,
    COUNT(*)                   AS Applications,
    COUNT(DISTINCT LeadId)     AS DistinctLeads,
    AVG(CAST(LoanAmount AS float))    AS AvgLoanAmount,
    AVG(CAST(MonthlyIncome AS float)) AS AvgMonthlyIncome
FROM dbo.LeadApplications WITH (NOLOCK)
WHERE DateCreated >= DATEADD(DAY, ?, SYSUTCDATETIME())
GROUP BY {aest_day('DateCreated')}, AffiliateId;
"""

ACCEPTS_BY_DAY = f"""
-- The sale to a lender. Counted as distinct applications, because
-- roughly 1% of applications have more than one accept and counting
-- events would overstate conversion.
SELECT
    {aest_day('DateCreated')}          AS Day,
    AffiliateId,
    COUNT(DISTINCT LeadApplicationId)  AS ApplicationsSold,
    COUNT(*)                           AS AcceptEvents
FROM dbo.LeadApplicationAccepts WITH (NOLOCK)
WHERE DateCreated >= DATEADD(DAY, ?, SYSUTCDATETIME())
GROUP BY {aest_day('DateCreated')}, AffiliateId;
"""

AFFILIATES = """
-- Names for the ids above. Password, CredfinSecretKey and
-- TalefinClientSecret live in this table and are never selected.
SELECT
    Id,
    Name,
    DisplayName,
    IsActive,
    IsDeleted,
    IsInternalSource
FROM dbo.Affiliates WITH (NOLOCK);
"""


# Which database each query runs against. Azure SQL Database cannot join
# across the two, so they are fetched separately and joined in the page.
SOURCES = {
    "stage_counts": ("OverflowReporting", STAGE_COUNTS_BY_DAY),
    "applications": ("Overflow", APPLICATIONS_BY_DAY),
    "accepts": ("Overflow", ACCEPTS_BY_DAY),
    "affiliates": ("Overflow", AFFILIATES),
}

# Queries taking the look-back window as their one parameter. The value
# passed is NEGATIVE days (DATEADD cannot take "-?" - a parameter marker
# may not carry a sign).
WINDOWED = {"stage_counts", "applications", "accepts"}


# Stage labels, supplied by the development team 22 Sep 2026. There is
# no lookup table for these in either source database, so this is the
# only place they are recorded.
#
# funnel_order comes from observed journey paths. None = not a funnel
# step. outcome marks the two competing terminal states.
STAGES = {
    1:  {"label": "Received",               "order": 10,   "outcome": None},
    2:  {"label": "Landed",                 "order": 20,   "outcome": None},
    3:  {"label": "Accepted T&Cs",          "order": 30,   "outcome": None},
    4:  {"label": "Bank statement required", "order": 40,  "outcome": None},
    5:  {"label": "Credfin landed",         "order": 50,   "outcome": None},
    6:  {"label": "Proviso landed",         "order": 50,   "outcome": None},
    7:  {"label": "Bank statement extracted", "order": 60, "outcome": None},
    8:  {"label": "Sell begun",             "order": 70,   "outcome": None},
    9:  {"label": "Sell completed",         "order": 80,   "outcome": None},
    12: {"label": "Interstitial",           "order": 85,   "outcome": None},
    10: {"label": "Offer",                  "order": 90,   "outcome": "good"},
    13: {"label": "Decline",                "order": 90,   "outcome": "bad"},
    # Not funnel steps: never emitted, retired, or error conditions.
    11: {"label": "Offer accepted",         "order": None, "outcome": None},
    14: {"label": "Duplicate bank statement", "order": None, "outcome": None},
    15: {"label": "Refresh bank statement", "order": None, "outcome": None},
    16: {"label": "Bank statement retry",   "order": None, "outcome": None},
    17: {"label": "No primary income source", "order": None, "outcome": None},
}
