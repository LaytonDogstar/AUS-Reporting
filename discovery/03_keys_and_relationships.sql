/*
    03 - Primary keys, foreign keys, unique constraints
    ------------------------------------------------------------------
    This is what tells us how tables actually join - and specifically
    whether the access doc's "M.ID = L.ID" example is correct.

    Read-only. Metadata only - reads no application data.
    Run against BOTH Overflow and OverflowReporting.
*/
SET NOCOUNT ON;
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

/* --- Primary keys ------------------------------------------------- */
SELECT
    DB_NAME()       AS database_name,
    s.name          AS schema_name,
    t.name          AS table_name,
    i.name          AS pk_name,
    ic.key_ordinal  AS key_position,
    c.name          AS column_name
FROM sys.indexes i
JOIN sys.tables  t  ON t.object_id = i.object_id
JOIN sys.schemas s  ON s.schema_id = t.schema_id
JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
JOIN sys.columns c  ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE i.is_primary_key = 1
ORDER BY s.name, t.name, ic.key_ordinal;

/* --- Foreign keys ------------------------------------------------- */
SELECT
    DB_NAME()       AS database_name,
    fk.name         AS fk_name,
    ps.name         AS parent_schema,
    pt.name         AS parent_table,
    pc.name         AS parent_column,
    rs.name         AS referenced_schema,
    rt.name         AS referenced_table,
    rc.name         AS referenced_column,
    fk.is_disabled,
    fk.is_not_trusted
FROM sys.foreign_keys fk
JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
JOIN sys.tables  pt ON pt.object_id = fkc.parent_object_id
JOIN sys.schemas ps ON ps.schema_id = pt.schema_id
JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id     AND pc.column_id = fkc.parent_column_id
JOIN sys.tables  rt ON rt.object_id = fkc.referenced_object_id
JOIN sys.schemas rs ON rs.schema_id = rt.schema_id
JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id AND rc.column_id = fkc.referenced_column_id
ORDER BY ps.name, pt.name, fk.name;

/* --- Unique constraints / unique indexes -------------------------
   In the absence of declared FKs these are the next best clue to
   what is a safe join key. */
SELECT
    DB_NAME()       AS database_name,
    s.name          AS schema_name,
    t.name          AS table_name,
    i.name          AS index_name,
    ic.key_ordinal  AS key_position,
    c.name          AS column_name
FROM sys.indexes i
JOIN sys.tables  t ON t.object_id = i.object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE i.is_unique = 1
  AND i.is_primary_key = 0
  AND ic.is_included_column = 0
ORDER BY s.name, t.name, i.name, ic.key_ordinal;
