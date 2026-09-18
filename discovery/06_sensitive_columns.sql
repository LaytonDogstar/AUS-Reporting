/*
    06 - Sensitive / PII column scan
    ------------------------------------------------------------------
    Flags columns whose NAME suggests personal or banking data, so we
    know what a reporting layer must never select. Australian-specific
    identifiers (BSB, TFN, Medicare) are included.

    Read-only. Metadata only - reads no column VALUES, only names.
    Run against BOTH Overflow and OverflowReporting.

    NOTE: name-based detection is a starting point, not an audit. A
    column called "Ref3" can still hold an account number. Treat the
    output as the list to review with compliance, not as complete.
*/
SET NOCOUNT ON;
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

WITH cols AS (
    SELECT
        s.name        AS schema_name,
        t.name        AS table_name,
        c.name        AS column_name,
        ty.name       AS data_type,
        LOWER(c.name) AS lname
    FROM sys.columns c
    JOIN sys.tables  t  ON t.object_id    = c.object_id
    JOIN sys.schemas s  ON s.schema_id    = t.schema_id
    JOIN sys.types   ty ON ty.user_type_id = c.user_type_id
),
classified AS (
    SELECT
        schema_name,
        table_name,
        column_name,
        data_type,
        CASE
            WHEN lname LIKE '%bsb%'        OR lname LIKE '%sortcode%'    OR lname LIKE '%sort[_]code%'
              OR lname LIKE '%accountnum%' OR lname LIKE '%account[_]num%'
              OR lname LIKE '%accountno%'  OR lname LIKE '%acct%'
              OR lname LIKE '%iban%'       OR lname LIKE '%swift%'
              OR lname LIKE '%cardnum%'    OR lname LIKE '%card[_]num%'
              OR lname LIKE '%cvv%'        OR lname LIKE '%bankaccount%'  THEN 'BANKING'
            WHEN lname LIKE '%tfn%'        OR lname LIKE '%medicare%'
              OR lname LIKE '%passport%'   OR lname LIKE '%licen%'
              OR lname LIKE '%ssn%'        OR lname LIKE '%nationalid%'
              OR lname LIKE '%dob%'        OR lname LIKE '%dateofbirth%'
              OR lname LIKE '%birth%'                                     THEN 'IDENTITY'
            WHEN lname LIKE '%password%'   OR lname LIKE '%passwd%'
              OR lname LIKE '%secret%'     OR lname LIKE '%token%'
              OR lname LIKE '%apikey%'     OR lname LIKE '%api[_]key%'
              OR lname LIKE '%hash%'       OR lname LIKE '%salt%'         THEN 'CREDENTIAL'
            WHEN lname LIKE '%email%'      OR lname LIKE '%phone%'
              OR lname LIKE '%mobile%'     OR lname LIKE '%address%'
              OR lname LIKE '%postcode%'   OR lname LIKE '%surname%'
              OR lname LIKE '%lastname%'   OR lname LIKE '%firstname%'
              OR lname LIKE '%fullname%'   OR lname LIKE '%ipaddress%'    THEN 'CONTACT/PII'
            ELSE NULL
        END AS sensitivity_category
    FROM cols
)
SELECT
    DB_NAME() AS database_name,
    schema_name,
    table_name,
    column_name,
    data_type,
    sensitivity_category
FROM classified
WHERE sensitivity_category IS NOT NULL
ORDER BY sensitivity_category, schema_name, table_name, column_name;
