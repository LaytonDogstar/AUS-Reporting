/*
    stage_counts - read by both builders (Python and PowerShell).

    {{REPORTING_TIME_ZONE}} is substituted before execution. The
    source server runs UTC; the reporting day is Sydney local time,
    including daylight saving, which is how the PublicReports API
    reports (confirmed by the dev team 2026-10-13).

    {{WINDOW_DAYS}} is a NEGATIVE integer, substituted by the builder.
    It is a token rather than a bound parameter because pyodbc and
    SqlClient disagree on placeholder syntax (? versus @name) and
    these files are shared. Both builders type it as an integer
    before substituting; never substitute a string here.

    Read-only. Runs under READ UNCOMMITTED so it cannot block the
    replication subscriber it reads from.
*/
-- One row per Sydney day, affiliate and stage: how many applications
-- reached that stage. The funnel, at its natural grain.
SELECT
    CAST(DateCreated AT TIME ZONE 'UTC'
         AT TIME ZONE '{{REPORTING_TIME_ZONE}}' AS date)          AS Day,
    AffiliateId,
    StageId,
    COUNT(DISTINCT LeadApplicationId)  AS Applications,
    COUNT(*)                           AS StageEvents
FROM dbo.LeadApplicationStages WITH (NOLOCK)
WHERE DateCreated >= DATEADD(DAY, {{WINDOW_DAYS}}, SYSUTCDATETIME())
GROUP BY CAST(DateCreated AT TIME ZONE 'UTC'
                  AT TIME ZONE '{{REPORTING_TIME_ZONE}}' AS date), AffiliateId, StageId;
