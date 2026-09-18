/*
    08 - Lookup table & status code candidates
    ------------------------------------------------------------------
    Status codes are where reporting goes wrong quietly. This finds the
    small tables that probably decode them, and the columns that
    probably hold them.

    This script does NOT read table contents. It GENERATES the SELECT
    statements for you to review and run by hand, so that nobody
    accidentally dumps a table full of customer data to a CSV.

    Read-only. Metadata only - reads no application data.
    Run against BOTH Overflow and OverflowReporting.
*/
SET NOCOUNT ON;
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

DECLARE @lookup_max_rows int = 500;    -- "small enough to be a lookup"

/* --- 1. Small tables = probable lookups, with a ready-made query --- */
WITH sized AS (
    SELECT
        s.name AS schema_name,
        t.name AS table_name,
        SUM(CASE WHEN i.index_id IN (0,1) THEN p.rows ELSE 0 END) AS approx_rows
    FROM sys.tables  t
    JOIN sys.schemas s ON s.schema_id = t.schema_id
    JOIN sys.indexes i ON i.object_id = t.object_id
    JOIN sys.partitions p ON p.object_id = i.object_id AND p.index_id = i.index_id
    GROUP BY s.name, t.name
)
SELECT
    DB_NAME()   AS database_name,
    schema_name,
    table_name,
    approx_rows,
    'SELECT * FROM ' + QUOTENAME(schema_name) + '.' + QUOTENAME(table_name) + ';' AS suggested_query
FROM sized
WHERE approx_rows BETWEEN 1 AND @lookup_max_rows
ORDER BY approx_rows, schema_name, table_name;

/* --- 2. Columns that look like status / type / error codes -------- */
SELECT
    DB_NAME()   AS database_name,
    s.name      AS schema_name,
    t.name      AS table_name,
    c.name      AS column_name,
    ty.name     AS data_type,
    CASE WHEN EXISTS (
            SELECT 1 FROM sys.foreign_key_columns fkc
            WHERE fkc.parent_object_id = c.object_id
              AND fkc.parent_column_id = c.column_id)
         THEN 'yes - has FK, decode table is known'
         ELSE 'no - meaning must be documented by the business'
    END AS has_foreign_key
FROM sys.columns c
JOIN sys.tables  t  ON t.object_id     = c.object_id
JOIN sys.schemas s  ON s.schema_id     = t.schema_id
JOIN sys.types   ty ON ty.user_type_id = c.user_type_id
WHERE LOWER(c.name) LIKE '%status%'
   OR LOWER(c.name) LIKE '%state%'
   OR LOWER(c.name) LIKE '%stage%'
   OR LOWER(c.name) LIKE '%type%'
   OR LOWER(c.name) LIKE '%code%'
   OR LOWER(c.name) LIKE '%reason%'
   OR LOWER(c.name) LIKE '%error%'
   OR LOWER(c.name) LIKE '%result%'
   OR LOWER(c.name) LIKE '%outcome%'
   OR LOWER(c.name) LIKE '%flag%'
ORDER BY s.name, t.name, c.name;
