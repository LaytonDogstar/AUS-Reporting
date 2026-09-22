/*
    applications - read by both builders (Python and PowerShell).

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
-- Application volume and loan size per AEST day and affiliate.
SELECT
    CAST(DATEADD(HOUR, {{AEST_SHIFT_HOURS}}, DateCreated) AS date)  AS Day,
    AffiliateId,
    COUNT(*)                   AS Applications,
    COUNT(DISTINCT LeadId)     AS DistinctLeads,
    AVG(CAST(LoanAmount AS float))    AS AvgLoanAmount,
    AVG(CAST(MonthlyIncome AS float)) AS AvgMonthlyIncome
FROM dbo.LeadApplications WITH (NOLOCK)
WHERE DateCreated >= DATEADD(DAY, {{WINDOW_DAYS}}, SYSUTCDATETIME())
GROUP BY CAST(DATEADD(HOUR, {{AEST_SHIFT_HOURS}}, DateCreated) AS date), AffiliateId;
