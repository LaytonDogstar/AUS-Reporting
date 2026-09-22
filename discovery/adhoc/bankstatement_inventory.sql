/*
    Bank statement data - what is actually stored?
    ------------------------------------------------------------------
    Settles one question: does either database hold the 90 days of
    retrieved bank statement data, and at what grain - summary,
    categorised income, or individual transactions?

    The written-up findings name BankStatementSummaries and
    AccountCategorisation but never captured their row counts, and
    roughly 111M of Overflow's 117.6M rows are unaccounted for by the
    tables we have measured. This shows where those rows are.

    Read-only, metadata only. Row counts come from partition stats, so
    nothing is scanned and nothing is read from any column.

    Run against BOTH Overflow and OverflowReporting.

    .\Invoke-Sql.ps1 -Database Overflow -File .\bankstatement_inventory.sql -OutCsv C:\discovery\bankstatements.csv
*/
SET NOCOUNT ON;
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

-- 1. Every table by size. The big unknowns will stand out immediately.
SELECT
    DB_NAME()                         AS database_name,
    s.name                            AS schema_name,
    t.name                            AS table_name,
    SUM(p.row_count)                  AS row_count,
    CAST(SUM(p.reserved_page_count) * 8.0 / 1024 AS decimal(18,1)) AS reserved_mb
FROM sys.dm_db_partition_stats p
JOIN sys.tables  t ON t.object_id = p.object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE p.index_id IN (0, 1)          -- heap or clustered only, no double counting
GROUP BY s.name, t.name
ORDER BY SUM(p.row_count) DESC;

-- 2. Columns of anything that looks like bank statement material, so we
--    can tell a per-account summary from a per-transaction ledger.
--    Names only. No values are read.
SELECT
    DB_NAME()      AS database_name,
    s.name         AS schema_name,
    t.name         AS table_name,
    c.column_id    AS ordinal,
    c.name         AS column_name,
    ty.name        AS data_type,
    c.max_length   AS max_length,
    c.is_nullable  AS is_nullable
FROM sys.columns c
JOIN sys.tables  t  ON t.object_id     = c.object_id
JOIN sys.schemas s  ON s.schema_id     = t.schema_id
JOIN sys.types   ty ON ty.user_type_id = c.user_type_id
WHERE LOWER(t.name) LIKE '%bankstatement%'
   OR LOWER(t.name) LIKE '%statement%'
   OR LOWER(t.name) LIKE '%transaction%'
   OR LOWER(t.name) LIKE '%categorisation%'
   OR LOWER(t.name) LIKE '%categorization%'
   OR LOWER(t.name) LIKE '%account%'
   OR LOWER(t.name) LIKE '%credfin%'
   OR LOWER(t.name) LIKE '%talefin%'
   OR LOWER(t.name) LIKE '%proviso%'
ORDER BY t.name, c.column_id;
