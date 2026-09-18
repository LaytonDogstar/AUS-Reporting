/*
    02 - Full column inventory
    ------------------------------------------------------------------
    Every column in every table, with type, nullability and flags.
    This is the single most useful output of the pack.

    Read-only. Metadata only - reads no application data.
    Run against BOTH Overflow and OverflowReporting.
*/
SET NOCOUNT ON;
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

SELECT
    DB_NAME()           AS database_name,
    s.name              AS schema_name,
    t.name              AS table_name,
    c.column_id         AS ordinal,
    c.name              AS column_name,
    ty.name             AS data_type,
    CASE
        WHEN c.max_length = -1                              THEN 'MAX'
        WHEN ty.name IN ('nvarchar','nchar','ntext')        THEN CAST(c.max_length / 2 AS varchar(10))
        WHEN ty.name IN ('varchar','char','varbinary','binary','text','image')
                                                            THEN CAST(c.max_length AS varchar(10))
        ELSE ''
    END                 AS max_length,
    CASE WHEN ty.name IN ('decimal','numeric','float','real')
         THEN CAST(c.precision AS varchar(10)) + ',' + CAST(c.scale AS varchar(10))
         ELSE '' END    AS precision_scale,
    c.is_nullable,
    c.is_identity,
    c.is_computed,
    dc.definition       AS default_definition
FROM sys.columns c
JOIN sys.tables  t  ON t.object_id  = c.object_id
JOIN sys.schemas s  ON s.schema_id  = t.schema_id
JOIN sys.types   ty ON ty.user_type_id = c.user_type_id
LEFT JOIN sys.default_constraints dc
       ON dc.object_id = c.default_object_id
ORDER BY s.name, t.name, c.column_id;
