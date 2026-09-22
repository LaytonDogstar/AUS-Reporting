/*
    Timing test: is a view over the source fast enough?
    ------------------------------------------------------------------
    The question is whether reporting can run directly against
    OverflowReporting, or whether it needs an indexed copy.

    A view would run exactly these queries every time it was opened, so
    whatever they cost here is what a dashboard would cost.

    Run against OverflowReporting. Read-only, no personal data,
    READ UNCOMMITTED so it cannot block anything.

    RUN IT TWICE. The first run reads from disk, the second may be
    served from memory. Both numbers are worth knowing: the first is
    what a user hits in the morning, the second is the best case.
*/
SET NOCOUNT ON;
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

/* --- 1. Daily funnel by affiliate, last 30 days -------------------
   The core reporting query: how many applications reached each stage,
   per affiliate, per day. This is what a funnel dashboard runs.

   DateCreated is not indexed, so this reads all 19.76M rows to find
   the last 30 days. */
SELECT
    CAST(DateCreated AS date)          AS Day,
    AffiliateId,
    StageId,
    COUNT(DISTINCT LeadApplicationId)  AS Applications,
    COUNT(*)                           AS StageEvents
FROM dbo.LeadApplicationStages WITH (NOLOCK)
WHERE DateCreated >= DATEADD(DAY, -30, SYSUTCDATETIME())
GROUP BY CAST(DateCreated AS date), AffiliateId, StageId
ORDER BY Day DESC, AffiliateId, StageId;

/* --- 2. Yesterday only --------------------------------------------
   The narrowest useful question. With an index this would be near
   instant; without one it costs the same as the 30-day query, because
   either way the whole table is read. That contrast is the finding. */
SELECT
    StageId,
    COUNT(DISTINCT LeadApplicationId)  AS Applications
FROM dbo.LeadApplicationStages WITH (NOLOCK)
WHERE DateCreated >= CAST(DATEADD(DAY, -1, SYSUTCDATETIME()) AS date)
  AND DateCreated <  CAST(SYSUTCDATETIME() AS date)
GROUP BY StageId
ORDER BY StageId;
