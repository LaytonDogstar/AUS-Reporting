/*
    Post-load validation
    ------------------------------------------------------------------
    Run against the warehouse after an extract. Every check returns
    PASS or FAIL with the numbers behind it, so a wrong result is
    visible rather than merely plausible.

    Read-only. Safe to run any time.

    The reference counts in checks 1 and 6 are from the discovery run of
    21 Sep 2026 and will drift upward as data accumulates; they are
    lower bounds and ratios, not equalities.
*/
SET NOCOUNT ON;
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

DECLARE @results TABLE (
    Seq       int IDENTITY(1,1),
    Check_    nvarchar(80),
    Result    varchar(4),
    Detail    nvarchar(300)
);

/* --- 1. Stage 1 should be 1:1 with applications ------------------- */
DECLARE @apps bigint, @stage1 bigint, @stage1Apps bigint;
SELECT @apps = COUNT(*) FROM fct.LeadApplication;
SELECT @stage1 = COUNT(*), @stage1Apps = COUNT(DISTINCT LeadApplicationId)
FROM fct.ApplicationStage WHERE StageId = 1;

INSERT INTO @results (Check_, Result, Detail)
SELECT
    'Stage 1 fires once per application',
    CASE WHEN @stage1 = @stage1Apps THEN 'PASS' ELSE 'FAIL' END,
    CONCAT(@stage1, ' events across ', @stage1Apps, ' applications');

INSERT INTO @results (Check_, Result, Detail)
SELECT
    'Stage 1 count matches application count',
    CASE WHEN @apps = 0 THEN 'FAIL'
         WHEN ABS(@stage1Apps - @apps) * 100.0 / @apps < 1 THEN 'PASS'
         ELSE 'FAIL' END,
    CONCAT(@apps, ' applications vs ', @stage1Apps,
           ' with a stage 1 event (expect within 1%)');

/* --- 2. De-duplication is actually working ------------------------ */
DECLARE @stgRows bigint, @modelRows bigint;
SELECT @stgRows = COUNT(*) FROM stg.LeadApplicationStages;
SELECT @modelRows = COUNT(*) FROM fct.ApplicationStage;

INSERT INTO @results (Check_, Result, Detail)
SELECT
    'No duplicate stage events in the model',
    CASE WHEN @modelRows = (SELECT COUNT(DISTINCT Id) FROM stg.LeadApplicationStages)
         THEN 'PASS' ELSE 'FAIL' END,
    CONCAT(@stgRows, ' staged rows -> ', @modelRows, ' model rows (',
           @stgRows - @modelRows, ' duplicates removed)');

/* --- 3. Every stage in the data is in dim.Stage ------------------- */
DECLARE @unknown int = (SELECT COUNT(*) FROM dim.vwUnknownStages);
INSERT INTO @results (Check_, Result, Detail)
SELECT
    'All StageIds are known to dim.Stage',
    CASE WHEN @unknown = 0 THEN 'PASS' ELSE 'FAIL' END,
    CASE WHEN @unknown = 0 THEN 'no unknown stages'
         ELSE CONCAT(@unknown, ' unknown StageId(s) - the journey changed, so update 003_dim_stage.sql') END;

/* --- 4. Referential integrity the source never enforced ----------- */
DECLARE @orphanAff int = (
    SELECT COUNT(*) FROM fct.LeadApplication AS a
    LEFT JOIN dim.Affiliate AS af ON af.AffiliateId = a.AffiliateId
    WHERE af.AffiliateId IS NULL AND a.AffiliateId IS NOT NULL);

INSERT INTO @results (Check_, Result, Detail)
SELECT
    'Applications reference a known affiliate',
    CASE WHEN @orphanAff = 0 THEN 'PASS' ELSE 'FAIL' END,
    CONCAT(@orphanAff, ' application(s) with an AffiliateId not in dim.Affiliate');

DECLARE @orphanAccept int = (
    SELECT COUNT(*) FROM fct.Accept AS ac
    LEFT JOIN fct.LeadApplication AS a ON a.LeadApplicationId = ac.LeadApplicationId
    WHERE a.LeadApplicationId IS NULL);

INSERT INTO @results (Check_, Result, Detail)
SELECT
    'Accepts reference a known application',
    CASE WHEN @orphanAccept = 0 THEN 'PASS' ELSE 'FAIL' END,
    CONCAT(@orphanAccept, ' accept(s) with no matching application');

/* --- 5. Dates resolve ---------------------------------------------
   A CreatedDateKey with no dim.Date row means the calendar needs
   extending, and every date-sliced report would silently drop rows. */
DECLARE @orphanDate int = (
    SELECT COUNT(*) FROM fct.LeadApplication AS a
    LEFT JOIN dim.Date AS d ON d.DateKey = a.CreatedDateKey
    WHERE d.DateKey IS NULL);

INSERT INTO @results (Check_, Result, Detail)
SELECT
    'Application dates resolve in dim.Date',
    CASE WHEN @orphanDate = 0 THEN 'PASS' ELSE 'FAIL' END,
    CONCAT(@orphanDate, ' application(s) whose AEST date is outside dim.Date');

/* --- 6. Request/response pairs -------------------------------------
   Responses can never exceed requests. The shortfall is the failure
   count: 220 in 4.09M at the time of discovery. */
DECLARE @s8 bigint, @s9 bigint, @s3 bigint, @s4 bigint;
SELECT
    @s8 = SUM(CASE WHEN StageId = 8 THEN 1 ELSE 0 END),
    @s9 = SUM(CASE WHEN StageId = 9 THEN 1 ELSE 0 END),
    @s3 = SUM(CASE WHEN StageId = 3 THEN 1 ELSE 0 END),
    @s4 = SUM(CASE WHEN StageId = 4 THEN 1 ELSE 0 END)
FROM fct.ApplicationStage;

INSERT INTO @results (Check_, Result, Detail)
SELECT
    'Lender call responses do not exceed requests',
    CASE WHEN @s9 <= @s8 THEN 'PASS' ELSE 'FAIL' END,
    CONCAT('stage 8 = ', @s8, ', stage 9 = ', @s9, ', unanswered = ', @s8 - @s9);

INSERT INTO @results (Check_, Result, Detail)
SELECT
    'Pre-check responses do not exceed requests',
    CASE WHEN @s4 <= @s3 THEN 'PASS' ELSE 'FAIL' END,
    CONCAT('stage 3 = ', @s3, ', stage 4 = ', @s4, ', unanswered = ', @s3 - @s4);

/* --- 7. Offer and Decline should be competing outcomes ------------ */
DECLARE @both int = (
    SELECT COUNT(*) FROM agg.ApplicationFunnel
    WHERE ReceivedOffer = 1 AND Declined = 1);
DECLARE @either int = (
    SELECT COUNT(*) FROM agg.ApplicationFunnel
    WHERE ReceivedOffer = 1 OR Declined = 1);

INSERT INTO @results (Check_, Result, Detail)
SELECT
    'Offer and Decline are competing outcomes',
    CASE WHEN @either = 0 THEN 'FAIL'
         WHEN @both * 100.0 / @either < 5 THEN 'PASS'
         ELSE 'FAIL' END,
    CONCAT(@both, ' of ', @either, ' applications reached both Offer and Decline. Some overlap is expected where one lender offers and another declines; a high figure means they are not a clean win/lose split');

/* --- 7b. OfferAccepted has never fired ----------------------------
   Not a failure - it records that lenders do not report acceptance
   back. If this ever returns a non-zero count, a feed has started and
   funded reporting becomes possible for the first time. */
DECLARE @accepted int = (
    SELECT COUNT(*) FROM agg.ApplicationFunnel WHERE OfferAccepted = 1);

INSERT INTO @results (Check_, Result, Detail)
SELECT
    'OfferAccepted status (informational)',
    'PASS',
    CASE WHEN @accepted = 0
         THEN 'never emitted, as expected - lenders do not report acceptance back'
         ELSE CONCAT(@accepted, ' applications now have OfferAccepted. A lender feed has started; funded reporting is newly possible') END;

/* --- 8. Retired and never-used stages stay out of the funnel ------ */
DECLARE @excluded int = (
    SELECT COUNT(*) FROM fct.ApplicationStage
    WHERE StageId IN (11, 14, 15, 16, 17) AND IncludeInFunnel = 1);

INSERT INTO @results (Check_, Result, Detail)
SELECT
    'Error and unused stages are out of the funnel',
    CASE WHEN @excluded = 0 THEN 'PASS' ELSE 'FAIL' END,
    CONCAT(@excluded, ' event(s) from stages 11/14/15/16/17 flagged for the funnel');

/* --- 9. Nothing personal has arrived ------------------------------
   The extract cannot configure a banking or credential column, but a
   staging table altered by hand would not be caught there. */
DECLARE @pii int = (
    SELECT COUNT(*)
    FROM sys.columns AS c
    JOIN sys.tables  AS t ON t.object_id = c.object_id
    JOIN sys.schemas AS s ON s.schema_id = t.schema_id
    WHERE s.name = 'stg'
      AND (LOWER(REPLACE(c.name, '_', '')) IN
             ('accountnumber','sortcode','bsb','iban','password','secret',
              'secretkey','clientsecret','apikey','token','cardnumber','cvv',
              'firstname','lastname','email','mobilenumber','dateofbirth',
              'driverslicense')));

INSERT INTO @results (Check_, Result, Detail)
SELECT
    'No personal or banking columns in staging',
    CASE WHEN @pii = 0 THEN 'PASS' ELSE 'FAIL' END,
    CONCAT(@pii, ' staging column(s) matching a blocked or restricted name');

/* --- 10. Freshness ------------------------------------------------- */
DECLARE @stalest int = (SELECT MAX(MinutesSinceLoad) FROM ctl.vwFreshness);
INSERT INTO @results (Check_, Result, Detail)
SELECT
    'Incremental loads are recent',
    CASE WHEN @stalest IS NULL THEN 'FAIL'
         WHEN @stalest <= 1440 THEN 'PASS' ELSE 'FAIL' END,
    CONCAT('stalest incremental table loaded ',
           ISNULL(CAST(@stalest AS varchar(20)), 'never'),
           ' minutes ago (expect under 1440)');

/* --- Results ------------------------------------------------------- */
SELECT Seq, Check_ AS [Check], Result, Detail FROM @results ORDER BY Seq;

SELECT
    SUM(CASE WHEN Result = 'PASS' THEN 1 ELSE 0 END) AS Passed,
    SUM(CASE WHEN Result = 'FAIL' THEN 1 ELSE 0 END) AS Failed
FROM @results;
