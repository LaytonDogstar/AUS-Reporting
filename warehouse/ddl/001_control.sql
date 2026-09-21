/*
    001 - Control tables
    ------------------------------------------------------------------
    Run this before 002_staging.sql.

    The watermark lives in the warehouse, not the source. The source is
    read-only, and keeping "what we have loaded" next to the loaded data
    means the two cannot disagree after a failed run.

    Idempotent - safe to re-run.
*/
SET NOCOUNT ON;

IF SCHEMA_ID('ctl') IS NULL EXEC('CREATE SCHEMA ctl;');
GO

IF OBJECT_ID('ctl.ExtractWatermark') IS NULL
BEGIN
    CREATE TABLE ctl.ExtractWatermark (
        TargetTable  sysname       NOT NULL
            CONSTRAINT PK_ExtractWatermark PRIMARY KEY CLUSTERED,
        LastValue    bigint        NOT NULL,
        RowsLoaded   bigint        NOT NULL CONSTRAINT DF_ExtractWatermark_Rows DEFAULT (0),
        LastRunUtc   datetime2(0)  NOT NULL CONSTRAINT DF_ExtractWatermark_Run  DEFAULT SYSUTCDATETIME()
    );
END
GO

/*  Load history, for answering "when did this last run and did it work".
    Written by the extract; never read by it.  */
IF OBJECT_ID('ctl.ExtractRun') IS NULL
BEGIN
    CREATE TABLE ctl.ExtractRun (
        RunId        bigint IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_ExtractRun PRIMARY KEY CLUSTERED,
        StartedUtc   datetime2(0)  NOT NULL CONSTRAINT DF_ExtractRun_Started DEFAULT SYSUTCDATETIME(),
        FinishedUtc  datetime2(0)  NULL,
        TargetTable  sysname       NOT NULL,
        Mode         varchar(16)   NOT NULL,
        Rows         bigint        NULL,
        Batches      int           NULL,
        Status       varchar(16)   NOT NULL,
        ErrorText    nvarchar(4000) NULL
    );
END
GO

IF IndexProperty(OBJECT_ID('ctl.ExtractRun'), 'IX_ExtractRun_StartedUtc', 'IndexID') IS NULL
    CREATE INDEX IX_ExtractRun_StartedUtc ON ctl.ExtractRun (StartedUtc DESC);
GO

/*  Freshness at a glance. The source runs about two minutes behind live,
    so anything built on this warehouse should show "data as at" rather
    than implying it is current.  */
CREATE OR ALTER VIEW ctl.vwFreshness AS
SELECT
    w.TargetTable,
    w.LastValue,
    w.RowsLoaded,
    w.LastRunUtc,
    DATEDIFF(MINUTE, w.LastRunUtc, SYSUTCDATETIME()) AS MinutesSinceLoad
FROM ctl.ExtractWatermark AS w;
GO
