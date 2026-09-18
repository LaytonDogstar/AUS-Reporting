/*
    07 - Identifier columns & the Leads <-> LeadMetrics question
    ------------------------------------------------------------------
    The access doc gives this example:

        select * from Overflow.dbo.Leads L
        join OverflowReporting.dbo.LeadMetrics M on M.ID = L.ID

    Two things need checking:
      1. Is LeadMetrics.ID really the lead identifier, or is it an
         identity column with a separate LeadID foreign key? If it is
         the latter, that example silently returns wrong rows.
      2. Does a cross-database join even run here? (see script 00)

    Read-only. Metadata only - reads no application data.
    Run against BOTH Overflow and OverflowReporting.
*/
SET NOCOUNT ON;
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

/* --- 1. Every column on any lead-ish table ------------------------ */
SELECT
    DB_NAME()   AS database_name,
    s.name      AS schema_name,
    t.name      AS table_name,
    c.column_id AS ordinal,
    c.name      AS column_name,
    ty.name     AS data_type,
    c.is_nullable,
    c.is_identity
FROM sys.columns c
JOIN sys.tables  t  ON t.object_id     = c.object_id
JOIN sys.schemas s  ON s.schema_id     = t.schema_id
JOIN sys.types   ty ON ty.user_type_id = c.user_type_id
WHERE t.name LIKE '%Lead%'
ORDER BY s.name, t.name, c.column_id;

/* --- 2. Every identifier-shaped column, anywhere ------------------
   Shows how the lead grain is carried across tables, and whether the
   naming is consistent (ID vs LeadID vs LeadRef vs GUID). */
SELECT
    DB_NAME()   AS database_name,
    s.name      AS schema_name,
    t.name      AS table_name,
    c.name      AS column_name,
    ty.name     AS data_type,
    c.is_identity,
    CASE WHEN EXISTS (
            SELECT 1 FROM sys.index_columns ic
            WHERE ic.object_id = c.object_id AND ic.column_id = c.column_id)
         THEN 1 ELSE 0 END AS appears_in_an_index
FROM sys.columns c
JOIN sys.tables  t  ON t.object_id     = c.object_id
JOIN sys.schemas s  ON s.schema_id     = t.schema_id
JOIN sys.types   ty ON ty.user_type_id = c.user_type_id
WHERE LOWER(c.name) = 'id'
   OR LOWER(c.name) LIKE '%leadid%'
   OR LOWER(c.name) LIKE '%lead[_]id%'
   OR LOWER(c.name) LIKE '%[_]id'
   OR LOWER(c.name) LIKE '%guid%'
   OR LOWER(c.name) LIKE '%uuid%'
   OR LOWER(c.name) LIKE '%reference%'
   OR LOWER(c.name) LIKE '%[_]ref'
ORDER BY s.name, t.name, c.name;

/* --- 3. Column names common to both databases ---------------------
   Run this in each database and diff the two outputs: columns present
   in Overflow but missing from OverflowReporting are exactly what the
   access doc means by the replica being "stripped down". */
SELECT DISTINCT
    DB_NAME()   AS database_name,
    t.name      AS table_name,
    c.name      AS column_name
FROM sys.columns c
JOIN sys.tables t ON t.object_id = c.object_id
ORDER BY t.name, c.name;
