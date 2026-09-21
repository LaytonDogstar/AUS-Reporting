/*
    004 - dim.Date
    ------------------------------------------------------------------
    An AEST calendar.

    THE REPORTING DAY IS AEST, FIXED UTC+10, as decided 2026-09-21.

    The source server runs UTC (verified: local and UTC times are
    identical, offset zero), so every timestamp in staging is UTC and
    the model shifts it by +10 hours to get the reporting day.

    Fixed UTC+10 is AEST read literally, which is what Queensland
    observes year round. Sydney and Melbourne shift to AEDT (UTC+11) for
    daylight saving. If the business day should follow Sydney instead,
    change the AEST_OFFSET_HOURS shift in 005_model_views.sql to:

        TODATETIMEOFFSET(<col>, '+00:00') AT TIME ZONE 'AUS Eastern Standard Time'

    That restates history for roughly five months of every year, so it
    should be settled before these numbers are compared with another
    system.

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
