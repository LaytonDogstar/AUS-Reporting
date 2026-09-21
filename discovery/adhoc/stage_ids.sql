/*
    Ad-hoc: what are the StageId values?
    ------------------------------------------------------------------
    StageId drives the funnel but has no lookup table anywhere in either
    database, so its meaning has to be recognised from the data and then
    confirmed by whoever built the journey.

    Run against OverflowReporting.

    Read-only. Reads StageId, AffiliateId and DateCreated - no personal
    data of any kind.

    NOTE: this scans ~19.8M rows, because StageId is not indexed. Expect
    one to three minutes on the replica. It runs under READ UNCOMMITTED
    so it cannot block anything.

    This folder is NOT picked up by run-discovery.ps1; run it by hand.
*/
SET NOCOUNT ON;
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

/* --- 1. Every stage, with volume and lifespan ---------------------
   Read this as a funnel: stages near the top of the journey should have
   the highest counts, and each later stage fewer. A stage whose
   first_seen is recent is a journey change; one whose last_seen is old
   has been retired. */
SELECT
    StageId,
    COUNT(*)                        AS events,
    COUNT(DISTINCT LeadApplicationId) AS distinct_applications,
    CAST(MIN(DateCreated) AS date)  AS first_seen,
    CAST(MAX(DateCreated) AS date)  AS last_seen,
    CAST(100.0 * COUNT(*) / SUM(COUNT(*)) OVER () AS decimal(5,2)) AS pct_of_events
FROM dbo.LeadApplicationStages WITH (NOLOCK)
GROUP BY StageId
ORDER BY events DESC;

/* --- 2. Typical journey order -------------------------------------
   Average position of each stage within an application's own sequence.
   Ordered ascending, this is roughly the real journey order, which is
   usually enough to recognise what each stage is. */
WITH ordered AS (
    SELECT
        LeadApplicationId,
        StageId,
        ROW_NUMBER() OVER (
            PARTITION BY LeadApplicationId ORDER BY DateCreated, Id
        ) AS position_in_journey
    FROM dbo.LeadApplicationStages WITH (NOLOCK)
    WHERE DateCreated >= DATEADD(DAY, -30, SYSUTCDATETIME())   -- recent journeys only, to keep this cheap
)
SELECT
    StageId,
    COUNT(*)                                   AS events_30d,
    CAST(AVG(CAST(position_in_journey AS float)) AS decimal(6,2)) AS avg_position,
    MIN(position_in_journey)                   AS earliest_position,
    MAX(position_in_journey)                   AS latest_position
FROM ordered
GROUP BY StageId
ORDER BY avg_position;

/* --- 3. What each application's journey looks like ----------------
   Twenty recent journeys as an ordered stage path. Often the quickest
   way for someone who knows the product to say "that's the bank
   statement step". */
WITH recent AS (
    SELECT TOP (20) LeadApplicationId
    FROM dbo.LeadApplicationStages WITH (NOLOCK)
    WHERE DateCreated >= DATEADD(DAY, -2, SYSUTCDATETIME())
    GROUP BY LeadApplicationId
    HAVING COUNT(*) > 2
    ORDER BY MAX(DateCreated) DESC
)
SELECT
    s.LeadApplicationId,
    STUFF((
        SELECT ' -> ' + CAST(s2.StageId AS varchar(10))
        FROM dbo.LeadApplicationStages AS s2 WITH (NOLOCK)
        WHERE s2.LeadApplicationId = s.LeadApplicationId
        ORDER BY s2.DateCreated, s2.Id
        FOR XML PATH(''), TYPE).value('.', 'nvarchar(max)'), 1, 4, '') AS stage_path,
    COUNT(*) AS stage_count
FROM dbo.LeadApplicationStages AS s WITH (NOLOCK)
JOIN recent AS r ON r.LeadApplicationId = s.LeadApplicationId
GROUP BY s.LeadApplicationId
ORDER BY stage_count DESC;

/* --- 4. Does the stage set differ by affiliate? -------------------
   If some affiliates only ever emit a subset, the journey is
   configurable per affiliate (consistent with Affiliates.JourneyVersion)
   and the funnel has to be read per affiliate rather than overall. */
SELECT
    StageId,
    COUNT(DISTINCT AffiliateId) AS affiliates_emitting_this_stage
FROM dbo.LeadApplicationStages WITH (NOLOCK)
WHERE DateCreated >= DATEADD(DAY, -30, SYSUTCDATETIME())
GROUP BY StageId
ORDER BY affiliates_emitting_this_stage DESC;
