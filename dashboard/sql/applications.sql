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
--
-- COUNT(DISTINCT LeadId) was here and cost about 50 of this query's 55
-- seconds: LeadId is a uniqueidentifier and there are 4.4M rows. The
-- page never displayed it. AvgMonthlyIncome went with it for the same
-- reason. Add either back only alongside something that shows it.
SELECT
    CAST(DATEADD(HOUR, {{AEST_SHIFT_HOURS}}, DateCreated) AS date)  AS Day,
    AffiliateId,
    COUNT(*)                       AS Applications,
    AVG(CAST(LoanAmount AS float)) AS AvgLoanAmount
FROM dbo.LeadApplications WITH (NOLOCK)
WHERE DateCreated >= DATEADD(DAY, {{WINDOW_DAYS}}, SYSUTCDATETIME())
GROUP BY CAST(DATEADD(HOUR, {{AEST_SHIFT_HOURS}}, DateCreated) AS date), AffiliateId;
