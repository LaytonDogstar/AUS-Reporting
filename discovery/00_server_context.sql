/*
    00 - Server & database context
    ------------------------------------------------------------------
    Answers: what engine is this, what can this login do, what time does
    the server think it is, and do cross-database queries actually work?

    Read-only. Metadata only - reads no application data.
    Run against BOTH Overflow and OverflowReporting.
*/
SET NOCOUNT ON;
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

/* --- 1. Engine identification ------------------------------------ */
SELECT
    @@SERVERNAME                                        AS server_name,
    DB_NAME()                                           AS current_database,
    CAST(SERVERPROPERTY('Edition') AS nvarchar(200))    AS edition,
    CAST(SERVERPROPERTY('EngineEdition') AS int)        AS engine_edition,
    CASE CAST(SERVERPROPERTY('EngineEdition') AS int)
        WHEN 1  THEN 'Personal/Desktop'
        WHEN 2  THEN 'Standard (SQL Server on VM/on-prem)'
        WHEN 3  THEN 'Enterprise (SQL Server on VM/on-prem)'
        WHEN 4  THEN 'Express'
        WHEN 5  THEN 'Azure SQL Database (PaaS) -- cross-database joins NOT supported'
        WHEN 6  THEN 'Azure Synapse Analytics'
        WHEN 8  THEN 'Azure SQL Managed Instance -- cross-database joins supported'
        WHEN 9  THEN 'Azure Synapse serverless'
        WHEN 11 THEN 'Azure SQL Edge'
        ELSE 'Unknown'
    END                                                 AS engine_edition_meaning,
    CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(50))  AS product_version,
    CAST(SERVERPROPERTY('Collation') AS nvarchar(200))      AS server_collation,
    CAST(DATABASEPROPERTYEX(DB_NAME(),'Collation') AS nvarchar(200)) AS database_collation,
    CASE WHEN CAST(DATABASEPROPERTYEX(DB_NAME(),'Collation') AS nvarchar(200)) LIKE '%\_CS\_%' ESCAPE '\'
         THEN 'CASE SENSITIVE'
         ELSE 'case insensitive' END                    AS collation_case_sensitivity;

/* --- 2. Server clock vs UTC --------------------------------------
   Azure SQL always runs UTC. If these agree, any "local Australian
   time" in the data was written by the application, not the server. */
SELECT
    SYSDATETIME()                                       AS server_local_time,
    GETUTCDATE()                                        AS server_utc_time,
    SYSDATETIMEOFFSET()                                 AS server_time_with_offset,
    DATEDIFF(MINUTE, GETUTCDATE(), SYSDATETIME())       AS local_minus_utc_minutes;

/* --- 3. Databases visible from this connection -------------------- */
SELECT
    name            AS database_name,
    database_id,
    create_date,
    state_desc
FROM sys.databases
ORDER BY name;

/* --- 4. Who am I, and am I actually read-only? -------------------- */
SELECT
    SUSER_SNAME()   AS login_name,
    USER_NAME()     AS database_user;

SELECT r.name AS role_membership
FROM sys.database_role_members rm
JOIN sys.database_principals r ON r.principal_id = rm.role_principal_id
JOIN sys.database_principals m ON m.principal_id = rm.member_principal_id
WHERE m.name = USER_NAME()
ORDER BY r.name;

SELECT
    entity_name,
    subentity_name,
    permission_name
FROM sys.fn_my_permissions(NULL, 'DATABASE')
ORDER BY permission_name;

/* --- 5. Can we join across the two databases? ---------------------
   The access doc's example query does a cross-database join. On Azure
   SQL Database that fails unless Elastic Query is configured. This
   settles it. Dynamic SQL so a compile failure is catchable. */
DECLARE @other_db  sysname,
        @probe_sql nvarchar(max),
        @tbl_count int;

SET @other_db = CASE WHEN DB_NAME() = 'Overflow' THEN 'OverflowReporting' ELSE 'Overflow' END;
SET @probe_sql = N'SELECT @cnt = COUNT(*) FROM ' + QUOTENAME(@other_db) + N'.sys.tables;';

BEGIN TRY
    EXEC sp_executesql @probe_sql, N'@cnt int OUTPUT', @cnt = @tbl_count OUTPUT;
    SELECT @other_db AS target_database,
           'SUPPORTED' AS cross_database_query,
           @tbl_count  AS tables_visible,
           NULL        AS error_message;
END TRY
BEGIN CATCH
    SELECT @other_db AS target_database,
           'NOT SUPPORTED' AS cross_database_query,
           NULL            AS tables_visible,
           ERROR_MESSAGE() AS error_message;
END CATCH;
