/*
    01 - Table & view inventory with row counts and size
    ------------------------------------------------------------------
    Row counts come from sys.partitions metadata, so this is instant and
    does NOT scan any table. Counts are approximate (accurate to within
    a few rows on a live system) - fine for sizing decisions.

    Read-only. Metadata only - reads no application data.
    Run against BOTH Overflow and OverflowReporting.
*/
SET NOCOUNT ON;
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

/* --- Tables ------------------------------------------------------- */
SELECT
    DB_NAME()      AS database_name,
    s.name         AS schema_name,
    t.name         AS table_name,
    r.row_count    AS approx_row_count,
    z.total_mb     AS total_mb,
    t.create_date  AS created,
    t.modify_date  AS schema_last_modified
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

/* --- Views -------------------------------------------------------
   Worth knowing: the reporting replica may already expose curated
   views that are a better starting point than the base tables. */
SELECT
    DB_NAME()       AS database_name,
    s.name          AS schema_name,
    v.name          AS view_name,
    v.create_date   AS created,
    v.modify_date   AS last_modified
FROM sys.views   v
JOIN sys.schemas s ON s.schema_id = v.schema_id
ORDER BY s.name, v.name;

/* --- Stored procedures & functions -------------------------------
   Existing reporting logic often already lives here. */
SELECT
    DB_NAME()       AS database_name,
    s.name          AS schema_name,
    o.name          AS object_name,
    o.type_desc     AS object_type,
    o.create_date   AS created,
    o.modify_date   AS last_modified
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE o.type IN ('P','FN','IF','TF')
ORDER BY o.type_desc, s.name, o.name;
