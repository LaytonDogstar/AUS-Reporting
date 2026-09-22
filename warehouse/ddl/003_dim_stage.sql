/*
    003 - dim.Stage
    ------------------------------------------------------------------
    Hand-maintained. There is nothing upstream to extract it from:
    StageId has no lookup table anywhere in either source database.

    Labels supplied by the development team, 22 Sep 2026, and confirmed
    against the observed data. FunnelOrder comes from the journey paths
    seen in the data, not from the label list.

    Three stages have NEVER been emitted in five years: OfferAccepted,
    RefreshBankStatement and NoPrimaryIncomeSource. OfferAccepted is the
    notable one - it is where lender acceptance would be recorded, and
    it corroborates that lenders do not report outcomes back.

    Idempotent - safe to re-run. Re-running resets labels to these
    values, so edit this file rather than the table.
*/
SET NOCOUNT ON;

IF SCHEMA_ID('dim') IS NULL EXEC('CREATE SCHEMA dim;');
GO

IF OBJECT_ID('dim.Stage') IS NULL
BEGIN
    CREATE TABLE dim.Stage (
        StageId           int          NOT NULL CONSTRAINT PK_dim_Stage PRIMARY KEY CLUSTERED,
        StageLabel        nvarchar(80) NOT NULL,
        FunnelOrder       int          NULL,
        PairedWithStageId int          NULL,   -- request/response partner
        IsLabelConfirmed  bit          NOT NULL,
        IsActive          bit          NOT NULL,
        IncludeInFunnel   bit          NOT NULL,
        Notes             nvarchar(400) NULL
    );
END
GO

/*  Ordered by the observed journey:
    1 -> [3 -> 4] -> 5 -> 7 -> [8 -> 9] -> 10 -> [8 -> 9]... -> 13 or 12  */
WITH seed (StageId, StageLabel, FunnelOrder, PairedWithStageId,
           IsLabelConfirmed, IsActive, IncludeInFunnel, Notes) AS (
    SELECT * FROM (VALUES
        (1,  N'Received',                 10,  NULL, 1, 1, 1,
             N'Exactly one per application (4,443,774 / 4,443,774) and matches Overflow.LeadApplications to 0.00%. The funnel denominator.'),
        (2,  N'Landed',                   20,  NULL, 1, 1, 1,
             N'140,624 applications (3.2%). Repeats 1.97x. Emitted by 8 of 19 active affiliates.'),
        (3,  N'AcceptedTC',               30,     4, 1, 1, 1,
             N'191,907 applications (4.3%). Only 7 of 19 affiliates emit it, so most journeys capture consent elsewhere - consistent with affiliate pages that already collect it.'),
        (4,  N'RequireBankStatement',     40,     3, 1, 1, 1,
             N'Follows AcceptedTC immediately; 17 of 192,802 did not reach it.'),
        (5,  N'CredfinLanded',            50,  NULL, 1, 1, 1,
             N'2,089,331 applications (47.0%). The Credfin bank-statement path. Parallel to ProvisoLanded.'),
        (6,  N'ProvisoLanded',            50,  NULL, 1, 1, 1,
             N'The Proviso path, the alternative provider to Credfin. One affiliate only, 1,229 applications since 2022.'),
        (7,  N'BankStatementExtracted',   60,  NULL, 1, 1, 1,
             N'1,681,621 applications (37.8%). Matches OverflowReporting.BankStatementRetrievals to 0.01%.'),
        (8,  N'BeginSell',                70,     9, 1, 1, 1,
             N'3,876,526 applications (87.2%). Paired with SellCompleted.'),
        (9,  N'SellCompleted',            80,     8, 1, 1, 1,
             N'220 of 4,093,727 BeginSell events never completed - a reliability metric, not a business outcome.'),
        (12, N'Interstitial',             85,  NULL, 1, 1, 1,
             N'1,230,891 applications (27.7%). A page shown after the sell process, not an outcome. Do not treat as a conversion.'),
        (10, N'Offer',                    90,  NULL, 1, 1, 1,
             N'356,061 applications (8.0%). Repeats 1.89x, so one application can receive several offers. The positive outcome.'),
        (13, N'Decline',                  90,  NULL, 1, 1, 1,
             N'692,347 applications (15.6%). The negative outcome, competing with Offer at the same funnel level.'),
        (11, N'OfferAccepted',           100,  NULL, 1, 0, 0,
             N'NEVER EMITTED in five years. This is where lender acceptance would be recorded; lenders do not report outcomes back, so it never fires. Excluded from the funnel so charts do not carry a permanent zero.'),
        (14, N'DuplicateBankstatement', NULL,  NULL, 1, 0, 0,
             N'Error condition, not funnel progress. 7 events, 2022-11-21 to 2023-02-16.'),
        (15, N'RefreshBankStatement',   NULL,  NULL, 1, 0, 0,
             N'NEVER EMITTED. Defined but unused.'),
        (16, N'BankstatementRetry',     NULL,  NULL, 1, 0, 0,
             N'Retry condition, not funnel progress. 134 events, 2023-04-05 to 2023-05-16.'),
        (17, N'NoPrimaryIncomeSource',  NULL,  NULL, 1, 0, 0,
             N'NEVER EMITTED. A decline reason that has never been recorded.')
    ) AS v (StageId, StageLabel, FunnelOrder, PairedWithStageId,
            IsLabelConfirmed, IsActive, IncludeInFunnel, Notes)
)
MERGE dim.Stage AS t
USING seed AS s ON t.StageId = s.StageId
WHEN MATCHED THEN UPDATE SET
     t.StageLabel        = s.StageLabel,
     t.FunnelOrder       = s.FunnelOrder,
     t.PairedWithStageId = s.PairedWithStageId,
     t.IsLabelConfirmed  = s.IsLabelConfirmed,
     t.IsActive          = s.IsActive,
     t.IncludeInFunnel   = s.IncludeInFunnel,
     t.Notes             = s.Notes
WHEN NOT MATCHED BY TARGET THEN INSERT
     (StageId, StageLabel, FunnelOrder, PairedWithStageId,
      IsLabelConfirmed, IsActive, IncludeInFunnel, Notes)
     VALUES (s.StageId, s.StageLabel, s.FunnelOrder, s.PairedWithStageId,
             s.IsLabelConfirmed, s.IsActive, s.IncludeInFunnel, s.Notes);
GO

/*  A stage appearing in the data but not here means the journey changed
    and dim.Stage needs updating. Worth checking after any release.  */
CREATE OR ALTER VIEW dim.vwUnknownStages AS
SELECT DISTINCT s.StageId
FROM stg.LeadApplicationStages AS s
LEFT JOIN dim.Stage AS d ON d.StageId = s.StageId
WHERE d.StageId IS NULL;
GO
