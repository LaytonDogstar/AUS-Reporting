/*
    Which number is "accept"?
    ------------------------------------------------------------------
    Three tables look like they describe one event and disagree by up to
    3.5x across all history:

        LeadApplicationAccepts   1,270,433
        SellHistory                592,557
        Offer stage (StageId 10)   356,000

    The PublicReports API returns the business's own sanctioned lead
    count. Run its LenderLeadCountReport over a date range, run this over
    the SAME range, and whichever candidate matches is the definition the
    business already uses. That settles it empirically instead of by
    asking and taking the answer on trust.

    EDIT THE TWO DATES BELOW to match what Invoke-PublicReport.ps1
    printed. They are inclusive of @Start, exclusive of @End.

    Counted twice: once on UTC day boundaries, once on UTC+10, because
    the server runs UTC and the API probably does not. If the two differ
    and one matches the API, that also tells us which timezone the API
    reports in - which we have never established.

    Read-only, READ UNCOMMITTED. Safe against both databases: each query
    is guarded by OBJECT_ID, so a table in only one of them is skipped.

    SLOW. LenderApplicationResults is 20.7 GB with no index on its date
    column, so that section is a full scan - allow two to three minutes.

    .\Invoke-Sql.ps1 -Database OverflowReporting -Username LaytonB -File .\accept_candidates.sql -OutCsv C:\discovery\accepts-reporting.csv
    .\Invoke-Sql.ps1 -Database Overflow          -Username LaytonB -File .\accept_candidates.sql -OutCsv C:\discovery\accepts-overflow.csv
*/
SET NOCOUNT ON;
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

DECLARE @Start date = '2026-10-06';   -- <-- edit
DECLARE @End   date = '2026-10-13';   -- <-- edit

-- The same window expressed on UTC+10 boundaries. Midnight in Sydney is
-- 14:00 the previous day in UTC.
DECLARE @StartUtc  datetime2 = CAST(@Start AS datetime2);
DECLARE @EndUtc    datetime2 = CAST(@End   AS datetime2);
DECLARE @StartAest datetime2 = DATEADD(HOUR, -10, CAST(@Start AS datetime2));
DECLARE @EndAest   datetime2 = DATEADD(HOUR, -10, CAST(@End   AS datetime2));

/* --- 1. The candidates --------------------------------------------- */
IF OBJECT_ID('dbo.LeadApplicationStages', 'U') IS NOT NULL
EXEC sp_executesql
    N'SELECT ''Offer stage (StageId 10)'' AS candidate,
             COUNT(DISTINCT CASE WHEN DateCreated >= @su AND DateCreated < @eu
                                 THEN LeadApplicationId END) AS applications_utc,
             COUNT(DISTINCT CASE WHEN DateCreated >= @sa AND DateCreated < @ea
                                 THEN LeadApplicationId END) AS applications_aest
     FROM dbo.LeadApplicationStages WITH (NOLOCK)
     WHERE StageId = 10
       AND DateCreated >= @sa AND DateCreated < @eu;',
    N'@su datetime2, @eu datetime2, @sa datetime2, @ea datetime2',
    @su = @StartUtc, @eu = @EndUtc, @sa = @StartAest, @ea = @EndAest;

IF OBJECT_ID('dbo.LeadApplicationAccepts', 'U') IS NOT NULL
EXEC sp_executesql
    N'SELECT ''LeadApplicationAccepts'' AS candidate,
             SUM(CASE WHEN DateCreated >= @su AND DateCreated < @eu THEN 1 ELSE 0 END) AS rows_utc,
             SUM(CASE WHEN DateCreated >= @sa AND DateCreated < @ea THEN 1 ELSE 0 END) AS rows_aest,
             COUNT(DISTINCT CASE WHEN DateCreated >= @su AND DateCreated < @eu
                                 THEN LeadApplicationId END) AS applications_utc
     FROM dbo.LeadApplicationAccepts WITH (NOLOCK)
     WHERE DateCreated >= @sa AND DateCreated < @eu;',
    N'@su datetime2, @eu datetime2, @sa datetime2, @ea datetime2',
    @su = @StartUtc, @eu = @EndUtc, @sa = @StartAest, @ea = @EndAest;

IF OBJECT_ID('dbo.SellHistory', 'U') IS NOT NULL
EXEC sp_executesql
    N'SELECT ''SellHistory'' AS candidate,
             SUM(CASE WHEN DateCreated >= @su AND DateCreated < @eu THEN 1 ELSE 0 END) AS rows_utc,
             COUNT(DISTINCT CASE WHEN DateCreated >= @su AND DateCreated < @eu
                                 THEN LeadApplicationId END) AS applications_utc
     FROM dbo.SellHistory WITH (NOLOCK)
     WHERE DateCreated >= @sa AND DateCreated < @eu;',
    N'@su date, @eu date, @sa date',
    @su = @Start, @eu = @End, @sa = @Start;

/* --- 2. Applications in the window, as the denominator -------------- */
IF OBJECT_ID('dbo.LeadApplications', 'U') IS NOT NULL
EXEC sp_executesql
    N'SELECT ''LeadApplications (denominator)'' AS candidate,
             SUM(CASE WHEN DateCreated >= @su AND DateCreated < @eu THEN 1 ELSE 0 END) AS rows_utc,
             SUM(CASE WHEN DateCreated >= @sa AND DateCreated < @ea THEN 1 ELSE 0 END) AS rows_aest
     FROM dbo.LeadApplications WITH (NOLOCK)
     WHERE DateCreated >= @sa AND DateCreated < @eu;',
    N'@su datetime2, @eu datetime2, @sa datetime2, @ea datetime2',
    @su = @StartUtc, @eu = @EndUtc, @sa = @StartAest, @ea = @EndAest;

/* --- 3. Lender responses by status ---------------------------------
   The API's "lender lead count" most likely maps to one of these
   statuses. StatusId has no lookup table anywhere, so the counts and
   their relative sizes are how we recognise it. This is the slow one. */
IF OBJECT_ID('dbo.LenderApplicationResults', 'U') IS NOT NULL
EXEC sp_executesql
    N'SELECT StatusId,
             COUNT(*)                          AS rows_utc,
             COUNT(DISTINCT LenderId)          AS lenders,
             CAST(SUM(Cost) AS decimal(18,2))  AS total_cost
     FROM dbo.LenderApplicationResults WITH (NOLOCK)
     WHERE DateCreated >= @su AND DateCreated < @eu
     GROUP BY StatusId
     ORDER BY rows_utc DESC;',
    N'@su datetime2, @eu datetime2',
    @su = @StartUtc, @eu = @EndUtc;
