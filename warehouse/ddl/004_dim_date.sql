/*
    004 - dim.Date
    ------------------------------------------------------------------
    A Sydney calendar.

    THE REPORTING DAY IS SYDNEY LOCAL TIME, including AEDT. Decided
    2026-09-21 as "AEST" and settled 2026-10-13, when the dev team
    confirmed the PublicReports API reports on Sydney time. These
    numbers get compared with that API, so they follow the same clock.

    The source server runs UTC (verified: local and UTC times are
    identical, offset zero), so every timestamp in staging is UTC and
    005_model_views.sql converts it:

        <col> AT TIME ZONE 'UTC' AT TIME ZONE 'AUS Eastern Standard Time'

    which carries daylight saving. The literal reading of AEST - fixed
    UTC+10, what Queensland observes - was the earlier implementation
    and would disagree with the API for the five months a year that AEDT
    is in force. Nothing is loaded yet, so no history is restated by
    the change.

    Financial year is the Australian one, starting 1 July. Note that the
    data floor of 2021-07-01 is exactly the start of FY2022.

    Idempotent - safe to re-run.
*/
SET NOCOUNT ON;

IF SCHEMA_ID('dim') IS NULL EXEC('CREATE SCHEMA dim;');
GO

IF OBJECT_ID('dim.Date') IS NULL
BEGIN
    CREATE TABLE dim.Date (
        DateKey          int          NOT NULL CONSTRAINT PK_dim_Date PRIMARY KEY CLUSTERED,  -- yyyymmdd
        [Date]           date         NOT NULL,
        [Year]           smallint     NOT NULL,
        [Quarter]        tinyint      NOT NULL,
        [Month]          tinyint      NOT NULL,
        MonthName        nvarchar(20) NOT NULL,
        MonthStart       date         NOT NULL,
        [Day]            tinyint      NOT NULL,
        DayOfWeek        tinyint      NOT NULL,   -- 1 = Monday
        DayName          nvarchar(20) NOT NULL,
        IsWeekend        bit          NOT NULL,
        IsoWeek          tinyint      NOT NULL,
        WeekStartMonday  date         NOT NULL,
        FinancialYear    smallint     NOT NULL,   -- Australian: FY2022 = Jul 2021 to Jun 2022
        FinancialQuarter tinyint      NOT NULL,
        FinancialYearLabel nvarchar(9) NOT NULL
    );
    CREATE UNIQUE INDEX UX_dim_Date_Date ON dim.Date ([Date]);
END
GO

/*  2021-01-01 through 2032-12-31. The data starts 2021-07-01, so this
    covers it with room either side.  */
WITH numbers AS (
    SELECT TOP (4383) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1 AS n
    FROM sys.all_objects AS a
    CROSS JOIN sys.all_objects AS b
),
dates AS (
    SELECT CAST(DATEADD(DAY, n, '2021-01-01') AS date) AS d
    FROM numbers
),
built AS (
    SELECT
        d,
        CAST(CONVERT(char(8), d, 112) AS int)                    AS DateKey,
        CAST(YEAR(d) AS smallint)                                AS [Year],
        CAST(DATEPART(QUARTER, d) AS tinyint)                    AS [Quarter],
        CAST(MONTH(d) AS tinyint)                                AS [Month],
        DATENAME(MONTH, d)                                       AS MonthName,
        DATEFROMPARTS(YEAR(d), MONTH(d), 1)                      AS MonthStart,
        CAST(DAY(d) AS tinyint)                                  AS [Day],
        -- ISO weekday: Monday = 1, independent of DATEFIRST
        CAST((DATEPART(WEEKDAY, d) + @@DATEFIRST + 5) % 7 + 1 AS tinyint) AS DayOfWeek,
        DATENAME(WEEKDAY, d)                                     AS DayName,
        CAST(DATEPART(ISO_WEEK, d) AS tinyint)                   AS IsoWeek,
        -- Australian financial year starts 1 July
        CAST(CASE WHEN MONTH(d) >= 7 THEN YEAR(d) + 1 ELSE YEAR(d) END AS smallint) AS FinancialYear,
        CAST(((MONTH(d) + 5) % 12) / 3 + 1 AS tinyint)           AS FinancialQuarter
    FROM dates
    WHERE d <= '2032-12-31'
)
MERGE dim.Date AS t
USING (
    SELECT
        DateKey, d AS [Date], [Year], [Quarter], [Month], MonthName, MonthStart,
        [Day], DayOfWeek, DayName,
        CAST(CASE WHEN DayOfWeek IN (6, 7) THEN 1 ELSE 0 END AS bit) AS IsWeekend,
        IsoWeek,
        CAST(DATEADD(DAY, 1 - DayOfWeek, d) AS date)              AS WeekStartMonday,
        FinancialYear,
        FinancialQuarter,
        CAST(CONCAT('FY', FinancialYear) AS nvarchar(9))          AS FinancialYearLabel
    FROM built
) AS s ON t.DateKey = s.DateKey
WHEN NOT MATCHED BY TARGET THEN INSERT
    (DateKey, [Date], [Year], [Quarter], [Month], MonthName, MonthStart,
     [Day], DayOfWeek, DayName, IsWeekend, IsoWeek, WeekStartMonday,
     FinancialYear, FinancialQuarter, FinancialYearLabel)
    VALUES (s.DateKey, s.[Date], s.[Year], s.[Quarter], s.[Month], s.MonthName,
            s.MonthStart, s.[Day], s.DayOfWeek, s.DayName, s.IsWeekend,
            s.IsoWeek, s.WeekStartMonday, s.FinancialYear, s.FinancialQuarter,
            s.FinancialYearLabel);
GO
