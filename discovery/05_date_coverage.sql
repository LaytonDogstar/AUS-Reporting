/*
    05 - Date coverage & data freshness
    ------------------------------------------------------------------
    MIN/MAX of every date-typed column. Tells us:
      - how far back history actually goes (vs what people assume)
      - which tables are still being written to
      - what the 30-day transactional deletion has already removed

    *** THIS IS THE ONLY SCRIPT IN THE PACK THAT READS DATA. ***
    It reads date columns only - no names, no bank details. It is also
    the only one that can be slow: MIN/MAX on an unindexed column is a
    table scan.

    RUN THIS AGAINST THE REPORTING REPLICA, NOT LIVE.
    Tables above @max_rows are skipped; raise it only if you need to.
*/
SET NOCOUNT ON;
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

DECLARE @max_rows bigint = 20000000;   -- skip anything bigger than this

IF OBJECT_ID('tempdb..#candidates') IS NOT NULL DROP TABLE #candidates;
IF OBJECT_ID('tempdb..#results')    IS NOT NULL DROP TABLE #results;

CREATE TABLE #candidates (
    id          int IDENTITY(1,1) PRIMARY KEY,
    schema_name sysname,
    table_name  sysname,
    column_name sysname,
    data_type   sysname,
    approx_rows bigint,
    is_index_leading_col bit
);

CREATE TABLE #results (
    schema_name sysname,
    table_name  sysname,
    column_name sysname,
    approx_rows bigint,
    min_value   nvarchar(50),
    max_value   nvarchar(50),
    note        nvarchar(400)
);

INSERT INTO #candidates (schema_name, table_name, column_name, data_type, approx_rows, is_index_leading_col)
SELECT
    s.name,
    t.name,
    c.name,
    ty.name,
    rc.approx_rows,
    CASE WHEN EXISTS (
            SELECT 1 FROM sys.index_columns ic
            WHERE ic.object_id = c.object_id
              AND ic.column_id = c.column_id
              AND ic.key_ordinal = 1)
         THEN 1 ELSE 0 END
FROM sys.columns c
JOIN sys.tables  t  ON t.object_id     = c.object_id
JOIN sys.schemas s  ON s.schema_id     = t.schema_id
JOIN sys.types   ty ON ty.user_type_id = c.user_type_id
CROSS APPLY (
    SELECT SUM(CASE WHEN i.index_id IN (0,1) THEN p.rows ELSE 0 END) AS approx_rows
    FROM sys.indexes i
    JOIN sys.partitions p ON p.object_id = i.object_id AND p.index_id = i.index_id
    WHERE i.object_id = t.object_id
) rc
WHERE ty.name IN ('date','datetime','datetime2','smalldatetime','datetimeoffset')
  AND c.is_computed = 0;

DECLARE @i          int = 1,
        @n          int,
        @sch        sysname,
        @tbl        sysname,
        @col        sysname,
        @rows       bigint,
        @sql        nvarchar(max),
        @min_value  nvarchar(50),
        @max_value  nvarchar(50);

SELECT @n = MAX(id) FROM #candidates;

WHILE @i <= ISNULL(@n, 0)
BEGIN
    SELECT @sch = schema_name, @tbl = table_name, @col = column_name, @rows = approx_rows
    FROM #candidates WHERE id = @i;

    IF @rows > @max_rows
    BEGIN
        INSERT INTO #results (schema_name, table_name, column_name, approx_rows, min_value, max_value, note)
        VALUES (@sch, @tbl, @col, @rows, NULL, NULL, 'SKIPPED - table exceeds @max_rows');
    END
    ELSE
    BEGIN
        SET @sql = N'SELECT @mn = CONVERT(nvarchar(50), MIN(' + QUOTENAME(@col) + N'), 126), '
                 + N'       @mx = CONVERT(nvarchar(50), MAX(' + QUOTENAME(@col) + N'), 126) '
                 + N'FROM '  + QUOTENAME(@sch) + N'.' + QUOTENAME(@tbl) + N' WITH (NOLOCK);';

        SET @min_value = NULL;
        SET @max_value = NULL;

        BEGIN TRY
            EXEC sp_executesql @sql,
                 N'@mn nvarchar(50) OUTPUT, @mx nvarchar(50) OUTPUT',
                 @mn = @min_value OUTPUT, @mx = @max_value OUTPUT;

            INSERT INTO #results (schema_name, table_name, column_name, approx_rows, min_value, max_value, note)
            VALUES (@sch, @tbl, @col, @rows, @min_value, @max_value, NULL);
        END TRY
        BEGIN CATCH
            INSERT INTO #results (schema_name, table_name, column_name, approx_rows, min_value, max_value, note)
            VALUES (@sch, @tbl, @col, @rows, NULL, NULL, 'ERROR: ' + ERROR_MESSAGE());
        END CATCH;
    END

    SET @i = @i + 1;
END

SELECT
    DB_NAME() AS database_name,
    r.schema_name,
    r.table_name,
    r.column_name,
    r.approx_rows,
    c.is_index_leading_col   AS was_cheap_to_read,
    r.min_value,
    r.max_value,
    r.note
FROM #results r
LEFT JOIN #candidates c
       ON c.schema_name = r.schema_name
      AND c.table_name  = r.table_name
      AND c.column_name = r.column_name
ORDER BY r.schema_name, r.table_name, r.column_name;

DROP TABLE #candidates;
DROP TABLE #results;
