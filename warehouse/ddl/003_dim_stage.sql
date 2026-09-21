/*
    003 - dim.Stage
    ------------------------------------------------------------------
    Hand-maintained. There is nothing upstream to extract it from:
    StageId has no lookup table anywhere in either source database.

    The labels below are the hypotheses from STAGES.md, marked with
    their confidence. UNCONFIRMED labels must not be published on a
    dashboard - the IsLabelConfirmed flag exists so a report can show
    "Stage 5" rather than a guess until someone who built the journey
    signs it off.

    FunnelOrder comes from observed journey paths and is established,
    even where the label is not. NULL means the stage sits outside the
    linear funnel.

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
        (1,  N'Application created',              10,  NULL, 1, 1, 1,
             N'Settled. Exactly one per application (4,443,774 / 4,443,774) and matches Overflow.LeadApplications to 0.00%. Use as the funnel denominator.'),
        (2,  N'UNCONFIRMED - stage 2',            15,  NULL, 0, 1, 1,
             N'Weak. Repeats 1.97x per application, 8 of 19 affiliates, can occur at position 1, and absent from all 20 sampled journeys. Volume matches no table.'),
        (3,  N'UNCONFIRMED - pre-check request',  20,     4, 0, 1, 1,
             N'Likely the salary pre-check request. Only 7 of 19 affiliates emit it and Affiliates.EnableSalaryPreCheck is a per-affiliate flag. Added 2022-03-09.'),
        (4,  N'UNCONFIRMED - pre-check response', 30,     3, 0, 1, 1,
             N'Response to stage 3. 17 of 192,802 requests unanswered.'),
        (5,  N'UNCONFIRMED - stage 5',            40,  NULL, 0, 1, 1,
             N'Weak. Added 2021-11-04, 17 of 19 affiliates, average position 2.75. Volume matches no table. Possibly terms agreed or landing page reached.'),
        (6,  N'UNCONFIRMED - single-affiliate',  NULL,  NULL, 0, 0, 0,
             N'One affiliate only, 1,258 events over four years, last seen 2026-08-26. Excluded from the funnel.'),
        (7,  N'Bank statement retrieval',         50,  NULL, 0, 1, 1,
             N'Strong. 1,705,395 against BankStatementRetrievals at 1,705,256 - 0.01%.'),
        (8,  N'UNCONFIRMED - lender call sent',   60,     9, 0, 1, 1,
             N'Likely the PingTree ping/post. Matches LeadMetadata (4,093,364) to 0.01%. Recurs after stage 10, consistent with trying successive lenders.'),
        (9,  N'UNCONFIRMED - lender call returned', 70,   8, 0, 1, 1,
             N'Response to stage 8. 220 of 4,093,727 unanswered - a reliability metric.'),
        (10, N'UNCONFIRMED - decline / next lender', 80, NULL, 0, 1, 1,
             N'Reasonable. Repeats 1.89x per application and sits between 8->9 cycles, so it looks like a retry boundary.'),
        (11, N'Never used',                      NULL,  NULL, 1, 0, 0,
             N'Allocated in code and never emitted. Nothing here is purged, so this is not a retired stage.'),
        (12, N'UNCONFIRMED - sold to lender',    100,  NULL, 0, 1, 1,
             N'Plausible. 1,242,741 against LeadApplicationAccepts at 1,269,729 - 2.17%. A terminal outcome; see 13.'),
        (13, N'UNCONFIRMED - not sold',          100,  NULL, 0, 1, 1,
             N'Plausible. The other terminal outcome: latest average position (9.30), follows the retry loop, and no sampled journey contained both 12 and 13. Same FunnelOrder as 12 because they compete.'),
        (14, N'Retired experiment',              NULL,  NULL, 1, 0, 0,
             N'7 events, 2022-11-21 to 2023-02-16.'),
        (15, N'Never used',                      NULL,  NULL, 1, 0, 0,
             N'Allocated in code and never emitted.'),
        (16, N'Retired experiment',              NULL,  NULL, 1, 0, 0,
             N'134 events, 2023-04-05 to 2023-05-16.')
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
