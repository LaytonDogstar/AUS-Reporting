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

    Read-only, metadata only. Row counts come from sys.partitions, the
    same catalog views 01_tables_and_views.sql uses, so nothing is
    scanned and nothing is read from any column.

    Deliberately NOT sys.dm_db_partition_stats: that dynamic management
    view needs VIEW DATABASE PERFORMANCE STATE, which the reporting
    login does not have. It fails with "permission denied".

    CORRECTED 2026-09-22. The first version joined sys.allocation_units
    while counting rows, which multiplies the count by the number of
    allocation units a table has. LeadApplicationCustomValues has an
    nvarchar(max) column, so it has three, and read 67.8M against a true
    22.6M. Rows now come from sys.partitions alone.

    Also dumps the 82 LeadMetrics column names, which the original
    discovery summarised but never listed. Those are the affordability
    and risk indicators, so we need the names to choose between them.

    Run against BOTH Overflow and OverflowReporting.

    .\Invoke-Sql.ps1 -Database Overflow -File .\bankstatement_inventory.sql -OutCsv C:\discovery\bankstatements.csv
*/
SET NOCOUNT ON;
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

-- 1. Every table by size. The big unknowns will stand out immediately.
SELECT
    DB_NAME()      AS database_name,
    s.name         AS schema_name,
    t.name         AS table_name,
    r.row_count    AS approx_row_count,
    z.total_mb     AS total_mb
FROM sys.tables  t
JOIN sys.schemas s ON s.schema_id = t.schema_id
CROSS APPLY (
    -- Rows from sys.partitions ALONE. Joining allocation units here
    -- multiplies the count by the number of allocation units the table
    -- has, so a table with an nvarchar(max) column reads 3x too high.
    SELECT SUM(p.rows) AS row_count
    FROM sys.partitions p
    WHERE p.object_id = t.object_id AND p.index_id IN (0, 1)
) r
CROSS APPLY (
    SELECT CAST(SUM(a.total_pages) * 8.0 / 1024 AS decimal(18,2)) AS total_mb
    FROM sys.partitions p
    JOIN sys.allocation_units a ON a.container_id = p.partition_id
    WHERE p.object_id = t.object_id
) z
ORDER BY r.row_count DESC, s.name, t.name;

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
   OR LOWER(t.name) LIKE '%leadmetrics%'
ORDER BY t.name, c.column_id;
