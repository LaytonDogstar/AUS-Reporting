/*
    005 - Model layer
    ------------------------------------------------------------------
    Dimensions and facts over staging. Views, not tables: staging is
    indexed, volumes are moderate, and a view cannot go stale.

    If Power BI is used in DirectQuery rather than import mode, or
    agg.ApplicationFunnel becomes slow, materialise that one into a
    table and refresh it after each extract. Everything else is small.

    TIME. Staging holds UTC, because the source server runs UTC. The
    reporting day is AEST, fixed UTC+10, so every fact exposes three
    columns:
        <Event>Utc      what the source recorded
        <Event>Aest     the same instant shifted +10 hours
        <Event>DateKey  the AEST day, joining to dim.Date
    To switch to Sydney local time including AEDT, replace each
    DATEADD(HOUR, 10, ...) with
        TODATETIMEOFFSET(..., '+00:00') AT TIME ZONE 'AUS Eastern Standard Time'
    See 004_dim_date.sql.

    DE-DUPLICATION. Incremental staging tables can contain duplicate
    rows: a batch is committed before its watermark advances, so an
    interrupted run re-reads its last batch. Any view over an
    incremental target de-duplicates on the source primary key.
    Snapshot targets are replaced whole and carry a real primary key,
    so they need no de-duplication.

    Idempotent - safe to re-run.
*/
SET NOCOUNT ON;

IF SCHEMA_ID('dim') IS NULL EXEC('CREATE SCHEMA dim;');
IF SCHEMA_ID('fct') IS NULL EXEC('CREATE SCHEMA fct;');
IF SCHEMA_ID('agg') IS NULL EXEC('CREATE SCHEMA agg;');
GO

/* ==================================================================
   Dimensions
   ================================================================== */

CREATE OR ALTER VIEW dim.Affiliate AS
SELECT
    a.Id                        AS AffiliateId,
    a.Name                      AS AffiliateName,
    a.DisplayName               AS AffiliateDisplayName,
    a.AffiliateGroupId,
    g.Name                      AS AffiliateGroupName,
    g.XeroName                  AS AffiliateGroupXeroName,
    a.Commission,
    a.CommissionTypeId,
    a.PingTreeId,
    a.JourneyVersion,
    a.IsImmediateResponse,
    a.MarketingEnabled,
    a.IsInternalSource,
    a.MinimumLoanAmount,
    a.DefaultLoanAmount,
    a.SentLimit,
    a.IsActive,
    a.IsDeleted,
    /*  Genuine for Affiliates, unlike the other configuration tables
        whose DateCreated is a 2025-03-04 migration stamp.  */
    a.DateCreated               AS AffiliateCreatedUtc,
    a.DateModified              AS AffiliateModifiedUtc
FROM stg.Affiliates AS a
LEFT JOIN stg.AffiliateGroups AS g ON g.Id = a.AffiliateGroupId;
GO

CREATE OR ALTER VIEW dim.Lender AS
SELECT
    l.Id                        AS LenderId,
    l.Name                      AS LenderName,
    l.XeroName                  AS LenderXeroName,
    l.SentLimit,
    l.DeclineLimit,
    l.BsType,
    l.BankstatementAlias,
    l.ShowTaleFinScore,
    l.PaymentCycle,
    l.LastBillingDate,
    l.NextBillingDate,
    l.IsActive,
    l.IsDeleted,
    /*  2025-03-04 for almost every row - a bulk migration stamp, not
        when the lender relationship began. Do not date a relationship
        from this.  */
    l.DateCreated               AS MigrationStampUtc,
    l.DateModified              AS LenderModifiedUtc
FROM stg.Lenders AS l;
GO

/*  No personal data: names, email, mobile and date of birth are never
    extracted. City and StateCode are not identifying on their own.  */
CREATE OR ALTER VIEW dim.Lead AS
SELECT
    l.Id                                        AS LeadId,
    l.StateCode,
    l.City,
    l.DateCreated                               AS LeadCreatedUtc,
    DATEADD(HOUR, 10, l.DateCreated)            AS LeadCreatedAest,
    CAST(CONVERT(char(8), DATEADD(HOUR, 10, l.DateCreated), 112) AS int) AS LeadCreatedDateKey
FROM stg.Leads AS l;
GO

/* ==================================================================
   Facts
   ================================================================== */

/*  The spine: one row per lead application. Grain is
    LeadApplicationId, which is the key 25 tables across both source
    databases hang off.  */
CREATE OR ALTER VIEW fct.LeadApplication AS
SELECT
    a.Id                                        AS LeadApplicationId,
    a.LeadId,
    a.AffiliateId,
    a.ReferenceId,
    a.DateCreated                               AS CreatedUtc,
    DATEADD(HOUR, 10, a.DateCreated)            AS CreatedAest,
    CAST(CONVERT(char(8), DATEADD(HOUR, 10, a.DateCreated), 112) AS int) AS CreatedDateKey,
    a.StateCode,
    a.PostCode,
    a.ResidentialStatus,
    a.EmploymentStatus,
    a.EmploymentDuration,
    a.PaymentFrequency,
    a.MonthlyIncome,
    a.LoanAmount,
    a.LoanPurpose,
    a.SecuredLoans,
    a.MarketingConsent,
    a.TermsAgreed,
    a.IsAustralianResident,
    a.AntiHawking,
    /*  NULLIF guards the divide; a zero or missing income yields NULL
        rather than an error or a misleading zero.  */
    CAST(a.LoanAmount / NULLIF(a.MonthlyIncome, 0) AS decimal(18,4)) AS LoanToMonthlyIncome
FROM stg.LeadApplications AS a;
GO

/*  Stage events, de-duplicated on the source Id. 19.8M rows.  */
CREATE OR ALTER VIEW fct.ApplicationStage AS
WITH deduped AS (
    SELECT
        s.Id,
        s.LeadApplicationId,
        s.AffiliateId,
        s.StageId,
        s.DateCreated,
        ROW_NUMBER() OVER (PARTITION BY s.Id ORDER BY s._LoadedUtc) AS rn
    FROM stg.LeadApplicationStages AS s
)
SELECT
    d.Id                                        AS StageEventId,
    d.LeadApplicationId,
    d.AffiliateId,
    d.StageId,
    st.StageLabel,
    st.FunnelOrder,
    st.IsLabelConfirmed,
    st.IncludeInFunnel,
    d.DateCreated                               AS StageUtc,
    DATEADD(HOUR, 10, d.DateCreated)            AS StageAest,
    CAST(CONVERT(char(8), DATEADD(HOUR, 10, d.DateCreated), 112) AS int) AS StageDateKey
FROM deduped AS d
LEFT JOIN dim.Stage AS st ON st.StageId = d.StageId
WHERE d.rn = 1;
GO

/*  The sale to a lender. This is the end of the funnel: lenders do not
    report funded outcomes back, so nothing downstream of the sale is
    measurable. Never label a metric built on this "funded".  */
CREATE OR ALTER VIEW fct.Accept AS
SELECT
    a.Id                                        AS AcceptId,
    a.LeadApplicationId,
    a.LeadId,
    a.AffiliateId,
    a.LenderTierId,
    a.DateCreated                               AS AcceptedUtc,
    DATEADD(HOUR, 10, a.DateCreated)            AS AcceptedAest,
    CAST(CONVERT(char(8), DATEADD(HOUR, 10, a.DateCreated), 112) AS int) AS AcceptedDateKey
FROM stg.LeadApplicationAccepts AS a;
GO

/* ==================================================================
   Aggregates
   ================================================================== */

/*  One row per application: how far it got and what happened.

    This is the expensive view - it groups 19.8M de-duplicated stage
    events. If it becomes slow, materialise it as a table refreshed
    after each extract.  */
CREATE OR ALTER VIEW agg.ApplicationFunnel AS
SELECT
    s.LeadApplicationId,
    /*  Stage events for one application should all carry the same
        affiliate; MIN collapses them rather than fanning the grain.  */
    MIN(s.AffiliateId)                          AS AffiliateId,
    COUNT(*)                                    AS StageEventCount,
    COUNT(DISTINCT s.StageId)                   AS DistinctStages,
    MAX(s.FunnelOrder)                          AS DeepestFunnelOrder,
    MIN(s.StageUtc)                             AS FirstStageUtc,
    MAX(s.StageUtc)                             AS LastStageUtc,
    DATEDIFF(SECOND, MIN(s.StageUtc), MAX(s.StageUtc)) AS JourneySeconds,
    CAST(CONVERT(char(8), DATEADD(HOUR, 10, MIN(s.StageUtc)), 112) AS int) AS FirstStageDateKey,
    MAX(CASE WHEN s.StageId = 7  THEN 1 ELSE 0 END) AS ReachedBankStatement,
    MAX(CASE WHEN s.StageId = 8  THEN 1 ELSE 0 END) AS LenderCallSent,
    MAX(CASE WHEN s.StageId = 9  THEN 1 ELSE 0 END) AS LenderCallReturned,
    MAX(CASE WHEN s.StageId = 12 THEN 1 ELSE 0 END) AS ReachedStage12,
    MAX(CASE WHEN s.StageId = 13 THEN 1 ELSE 0 END) AS ReachedStage13,
    /*  A lender call sent with no matching return. 220 in 4.09M
        overall - a free reliability metric.  */
    CAST(CASE WHEN MAX(CASE WHEN s.StageId = 8 THEN 1 ELSE 0 END) = 1
               AND MAX(CASE WHEN s.StageId = 9 THEN 1 ELSE 0 END) = 0
              THEN 1 ELSE 0 END AS bit)         AS LenderCallUnanswered
FROM fct.ApplicationStage AS s
WHERE s.IncludeInFunnel = 1
GROUP BY s.LeadApplicationId;
GO

/*  Funnel counts by affiliate, day and stage.

    PER AFFILIATE DELIBERATELY. The stage set varies by affiliate -
    stages 3 and 4 are emitted by only 7 of 19 active affiliates - so an
    affiliate that never emits a stage has not dropped out at it.
    Aggregate funnel percentages across affiliates are wrong.

    PctOfAffiliateStage1 is the reach relative to stage 1 for the SAME
    affiliate on the SAME day, which is the only comparison that means
    anything.  */
CREATE OR ALTER VIEW agg.StageFunnelByAffiliateDay AS
WITH counted AS (
    SELECT
        s.StageDateKey,
        s.AffiliateId,
        s.StageId,
        s.StageLabel,
        s.FunnelOrder,
        COUNT(DISTINCT s.LeadApplicationId) AS Applications,
        COUNT(*)                            AS StageEvents
    FROM fct.ApplicationStage AS s
    WHERE s.IncludeInFunnel = 1
    GROUP BY s.StageDateKey, s.AffiliateId, s.StageId, s.StageLabel, s.FunnelOrder
)
SELECT
    c.StageDateKey,
    c.AffiliateId,
    c.StageId,
    c.StageLabel,
    c.FunnelOrder,
    c.Applications,
    c.StageEvents,
    MAX(CASE WHEN c.StageId = 1 THEN c.Applications END)
        OVER (PARTITION BY c.StageDateKey, c.AffiliateId) AS AffiliateStage1Applications,
    CAST(100.0 * c.Applications / NULLIF(
        MAX(CASE WHEN c.StageId = 1 THEN c.Applications END)
            OVER (PARTITION BY c.StageDateKey, c.AffiliateId), 0)
        AS decimal(6,2))                     AS PctOfAffiliateStage1
FROM counted AS c;
GO

/*  Daily applications and sales. The headline view.

    "Conversion" here means sold to a lender, never funded.  */
CREATE OR ALTER VIEW agg.DailyPerformance AS
/*  Accepts are collapsed to one row per application BEFORE the join.
    Roughly 1% of applications have more than one accept, and joining
    fct.Accept directly would fan those rows out and inflate the
    application count - a wrong number that looks entirely plausible.  */
WITH accepts AS (
    SELECT
        LeadApplicationId,
        COUNT(*)            AS AcceptEvents,
        MIN(AcceptedUtc)    AS FirstAcceptedUtc
    FROM fct.Accept
    GROUP BY LeadApplicationId
)
SELECT
    a.CreatedDateKey                            AS DateKey,
    d.[Date]                                    AS AestDate,
    d.FinancialYearLabel,
    a.AffiliateId,
    af.AffiliateName,
    af.IsInternalSource,
    COUNT(*)                                    AS Applications,
    COUNT(DISTINCT a.LeadId)                    AS DistinctLeads,
    SUM(CASE WHEN ac.LeadApplicationId IS NOT NULL THEN 1 ELSE 0 END) AS ApplicationsSold,
    SUM(ISNULL(ac.AcceptEvents, 0))             AS AcceptEvents,
    CAST(100.0 * SUM(CASE WHEN ac.LeadApplicationId IS NOT NULL THEN 1 ELSE 0 END)
         / NULLIF(COUNT(*), 0) AS decimal(6,2)) AS SoldRatePct,
    AVG(a.LoanAmount)                           AS AvgLoanAmount,
    AVG(a.MonthlyIncome)                        AS AvgMonthlyIncome
FROM fct.LeadApplication AS a
JOIN dim.Date AS d            ON d.DateKey = a.CreatedDateKey
LEFT JOIN dim.Affiliate AS af ON af.AffiliateId = a.AffiliateId
LEFT JOIN accepts AS ac       ON ac.LeadApplicationId = a.LeadApplicationId
GROUP BY
    a.CreatedDateKey, d.[Date], d.FinancialYearLabel,
    a.AffiliateId, af.AffiliateName, af.IsInternalSource;
GO
