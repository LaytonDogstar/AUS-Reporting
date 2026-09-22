/*
    Two open questions from BANKSTATEMENT-DATA.md
    ------------------------------------------------------------------
    1. What is LeadApplicationCustomValues? 67.8M rows, 58% of Overflow,
       never examined.
    2. How far back does BankStatementSummaries go? If 2021, it is the
       only long-run affordability history, because LeadMetrics starts
       2023-01-24.

    Deliberately reads NO column values from LeadApplicationCustomValues.
    The name says affiliate-supplied custom fields, so it may hold
    personal data, and we do not select from a 67.8M-row table of unknown
    contents. This reads its column names only; once we can see what the
    columns are, a second query can count distinct field names safely.

    Everything that does read data reads dates and counts, nothing else.

    Safe to run against BOTH databases: every data query is guarded by
    OBJECT_ID, so a table that only exists in one of them is skipped
    rather than failing the batch.

    Expect roughly 20-40 seconds against Overflow (BankStatementSummaries
    is 4.4 GB and DateCreated is not indexed, so it is a full scan) and
    near-instant against OverflowReporting.

    .\Invoke-Sql.ps1 -Database Overflow -File .\custom_values_and_history.sql -OutCsv C:\discovery\custom.csv
*/
SET NOCOUNT ON;
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

/* --- 1. Column names of the tables never examined ------------------
   Metadata only. Nothing is read from any row. */
SELECT
    DB_NAME()      AS database_name,
    t.name         AS table_name,
    c.column_id    AS ordinal,
    c.name         AS column_name,
    ty.name        AS data_type,
    c.max_length   AS max_length,
    c.is_nullable  AS is_nullable
FROM sys.columns c
JOIN sys.tables  t  ON t.object_id     = c.object_id
JOIN sys.types   ty ON ty.user_type_id = c.user_type_id
WHERE t.name IN (
    'LeadApplicationCustomValues',
    'LeadScores',
    'LenderApplicationResults',
    'FailedFiltersLookup',
    'SellHistory'
)
ORDER BY t.name, c.column_id;

/* --- 2. How far back does BankStatementSummaries go? ---------------
   Overflow only. Reads DateCreated and counts, nothing else. The
   per-year breakdown costs no more than the min/max, since both need
   the same scan. */
IF OBJECT_ID('dbo.BankStatementSummaries', 'U') IS NOT NULL
EXEC sp_executesql N'
SELECT
    YEAR(DateCreated)              AS year_created,
    COUNT(*)                       AS rows_in_year,
    CAST(MIN(DateCreated) AS date) AS first_in_year,
    CAST(MAX(DateCreated) AS date) AS last_in_year
FROM dbo.BankStatementSummaries WITH (NOLOCK)
GROUP BY YEAR(DateCreated)
ORDER BY year_created;';

/* --- 3. Same for LeadScores ----------------------------------------
   604,665 rows, cheap, and worth knowing whether scoring predates the
   2023 metrics. Column is assumed to be DateCreated; if this returns
   nothing the table names it something else and result 1 will show it. */
IF OBJECT_ID('dbo.LeadScores', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.LeadScores', 'DateCreated') IS NOT NULL
EXEC sp_executesql N'
SELECT
    YEAR(DateCreated) AS year_created,
    COUNT(*)          AS rows_in_year
FROM dbo.LeadScores WITH (NOLOCK)
GROUP BY YEAR(DateCreated)
ORDER BY year_created;';
