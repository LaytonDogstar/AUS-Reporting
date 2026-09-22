/*
    stage_counts - read by both builders (Python and PowerShell).

    {{AEST_SHIFT_HOURS}} is substituted before execution. The
    source server runs UTC; the reporting day is AEST.

    {{WINDOW_DAYS}} is a NEGATIVE integer, substituted by the builder.
    It is a token rather than a bound parameter because pyodbc and
    SqlClient disagree on placeholder syntax (? versus @name) and
    these files are shared. Both builders type it as an integer
    before substituting; never substitute a string here.

    Read-only. Runs under READ UNCOMMITTED so it cannot block the
    replication subscriber it reads from.
*/
-- One row per AEST day, affiliate and stage: how many applications
-- reached that stage. The funnel, at its natural grain.
SELECT
    CAST(DATEADD(HOUR, {{AEST_SHIFT_HOURS}}, DateCreated) AS date)          AS Day,
    AffiliateId,
    StageId,
    COUNT(DISTINCT LeadApplicationId)  AS Applications,
    COUNT(*)                           AS StageEvents
FROM dbo.LeadApplicationStages WITH (NOLOCK)
WHERE DateCreated >= DATEADD(DAY, {{WINDOW_DAYS}}, SYSUTCDATETIME())
GROUP BY CAST(DATEADD(HOUR, {{AEST_SHIFT_HOURS}}, DateCreated) AS date), AffiliateId, StageId;
