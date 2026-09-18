/*
    04 - Index inventory
    ------------------------------------------------------------------
    Tells us which queries will be cheap and which will scan. Important
    on a replica that live users depend on: an unindexed report query
    against a large table is how reporting takes the system down.

    Read-only. Metadata only - reads no application data.
    Run against BOTH Overflow and OverflowReporting.
*/
SET NOCOUNT ON;
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

SELECT
    DB_NAME()               AS database_name,
    s.name                  AS schema_name,
    t.name                  AS table_name,
    ISNULL(i.name,'[HEAP]') AS index_name,
    i.type_desc             AS index_type,
    i.is_unique,
    i.is_primary_key,
    STUFF((
        SELECT ', ' + c2.name
        FROM sys.index_columns ic2
        JOIN sys.columns c2
          ON c2.object_id = ic2.object_id AND c2.column_id = ic2.column_id
        WHERE ic2.object_id = i.object_id
          AND ic2.index_id  = i.index_id
          AND ic2.is_included_column = 0
        ORDER BY ic2.key_ordinal
        FOR XML PATH(''), TYPE).value('.','nvarchar(max)'), 1, 2, '')  AS key_columns,
    STUFF((
        SELECT ', ' + c3.name
        FROM sys.index_columns ic3
        JOIN sys.columns c3
          ON c3.object_id = ic3.object_id AND c3.column_id = ic3.column_id
        WHERE ic3.object_id = i.object_id
          AND ic3.index_id  = i.index_id
          AND ic3.is_included_column = 1
        ORDER BY c3.name
        FOR XML PATH(''), TYPE).value('.','nvarchar(max)'), 1, 2, '')  AS included_columns,
    i.has_filter,
    i.filter_definition
FROM sys.indexes i
JOIN sys.tables  t ON t.object_id = i.object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
ORDER BY s.name, t.name, i.index_id;
