/*
    What field names are in LeadApplicationCustomValues?
    ------------------------------------------------------------------
    The table is key/value: Name holds the field name, Value holds the
    content. 67.8M rows, 58% of Overflow.

    Reads Name, AffiliateId and DateCreated. Does NOT read Value. Field
    names are not personal data; the values behind them may well be, and
    there is no reason to look at them to decide whether this table is
    worth using.

    Deliberately no COUNT(DISTINCT LeadApplicationId): on the earlier
    funnel query a distinct count over a uniqueidentifier cost 50 of 65
    seconds, for a number nothing displayed. Affiliate is an int over
    185 values, so that one is cheap.

    Nothing here is indexed, so this is a full scan of 2.8 GB. Expect
    30-90 seconds. READ UNCOMMITTED, so it cannot block anything.

    Run against Overflow.

    .\Invoke-Sql.ps1 -Database Overflow -File .\custom_value_names.sql -OutCsv C:\discovery\custom-names.csv
*/
SET NOCOUNT ON;
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

SELECT
    Name                             AS field_name,
    COUNT(*)                         AS rows_held,
    COUNT(DISTINCT AffiliateId)      AS affiliates_using_it,
    CAST(MIN(DateCreated) AS date)   AS first_seen,
    CAST(MAX(DateCreated) AS date)   AS last_seen,
    CAST(100.0 * COUNT(*) / SUM(COUNT(*)) OVER () AS decimal(5,2)) AS pct_of_rows
FROM dbo.LeadApplicationCustomValues WITH (NOLOCK)
GROUP BY Name
ORDER BY rows_held DESC;
