/*
    002 - Staging tables
    ------------------------------------------------------------------
    One table per spec in tables.yml. Types mirror the source so a load
    never truncates or rounds silently.

    Columns are nullable apart from the key and the load stamp: the
    source has no foreign keys and few NOT NULL guarantees, and staging
    is not the place to start rejecting rows. Constraints belong in the
    model layer.

    The indexes here are the point of the whole exercise. Neither source
    database has an index on LeadApplicationId or on any date column, so
    every join and date filter against them is a full scan. These are the
    indexes we control.

    Snapshot targets get a primary key on Id: they are replaced whole, so
    the key holds. Incremental targets get a NON-unique clustered index
    instead, because an interrupted run re-reads its last batch and
    staging has to tolerate that. The model layer de-duplicates on Id.

    Generated from the discovery output. Idempotent - safe to re-run.
*/
SET NOCOUNT ON;

IF SCHEMA_ID('stg') IS NULL EXEC('CREATE SCHEMA stg;');
GO


/* ==================== Overflow ==================== */

-- Overflow.Leads  (snapshot)
IF OBJECT_ID('stg.Leads') IS NULL
BEGIN
    CREATE TABLE stg.[Leads] (
        [Id]            uniqueidentifier NOT NULL,
        [DateCreated]   datetime NULL,
        [City]          nvarchar(100) NULL,
        [StateCode]     nvarchar(20) NULL,
        [_LoadedUtc]    datetime2(0) NOT NULL CONSTRAINT [DF_Leads_LoadedUtc] DEFAULT SYSUTCDATETIME()
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.key_constraints
               WHERE name = 'PK_stg_Leads' AND parent_object_id = OBJECT_ID('stg.Leads'))
    ALTER TABLE stg.[Leads] ADD CONSTRAINT [PK_stg_Leads] PRIMARY KEY CLUSTERED ([Id]);
GO
IF IndexProperty(OBJECT_ID('stg.Leads'), 'IX_Leads_DateCreated', 'IndexID') IS NULL
    CREATE INDEX [IX_Leads_DateCreated] ON stg.[Leads] ([DateCreated]);
GO

-- Overflow.LeadApplications  (snapshot)
IF OBJECT_ID('stg.LeadApplications') IS NULL
BEGIN
    CREATE TABLE stg.[LeadApplications] (
        [Id]                     uniqueidentifier NOT NULL,
        [LeadId]                 uniqueidentifier NULL,
        [AffiliateId]            int NULL,
        [ReferenceId]            nvarchar(100) NULL,
        [StateCode]              nvarchar(20) NULL,
        [PostCode]               nvarchar(20) NULL,
        [ResidentialStatus]      varchar(35) NULL,
        [EmploymentStatus]       varchar(20) NULL,
        [EmploymentDuration]     varchar(25) NULL,
        [MonthlyIncome]          decimal(18,4) NULL,
        [PaymentFrequency]       varchar(15) NULL,
        [LoanAmount]             decimal(18,4) NULL,
        [LoanPurpose]            varchar(100) NULL,
        [SecuredLoans]           bit NULL,
        [MarketingConsent]       bit NULL,
        [TermsAgreed]            bit NULL,
        [IsAustralianResident]   bit NULL,
        [AntiHawking]            bit NULL,
        [DateCreated]            datetime NULL,
        [_LoadedUtc]             datetime2(0) NOT NULL CONSTRAINT [DF_LeadApplications_LoadedUtc] DEFAULT SYSUTCDATETIME()
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.key_constraints
               WHERE name = 'PK_stg_LeadApplications' AND parent_object_id = OBJECT_ID('stg.LeadApplications'))
    ALTER TABLE stg.[LeadApplications] ADD CONSTRAINT [PK_stg_LeadApplications] PRIMARY KEY CLUSTERED ([Id]);
GO
IF IndexProperty(OBJECT_ID('stg.LeadApplications'), 'IX_LeadApplications_LeadId', 'IndexID') IS NULL
    CREATE INDEX [IX_LeadApplications_LeadId] ON stg.[LeadApplications] ([LeadId]);
GO
IF IndexProperty(OBJECT_ID('stg.LeadApplications'), 'IX_LeadApplications_DateCreated', 'IndexID') IS NULL
    CREATE INDEX [IX_LeadApplications_DateCreated] ON stg.[LeadApplications] ([DateCreated]);
GO
IF IndexProperty(OBJECT_ID('stg.LeadApplications'), 'IX_LeadApplications_AffiliateId', 'IndexID') IS NULL
    CREATE INDEX [IX_LeadApplications_AffiliateId] ON stg.[LeadApplications] ([AffiliateId]);
GO

-- Overflow.LeadApplicationAccepts  (snapshot)
IF OBJECT_ID('stg.LeadApplicationAccepts') IS NULL
BEGIN
    CREATE TABLE stg.[LeadApplicationAccepts] (
        [Id]                  uniqueidentifier NOT NULL,
        [LeadId]              uniqueidentifier NULL,
        [AffiliateId]         int NULL,
        [LeadApplicationId]   uniqueidentifier NULL,
        [LenderTierId]        int NULL,
        [DateCreated]         datetime NULL,
        [_LoadedUtc]          datetime2(0) NOT NULL CONSTRAINT [DF_LeadApplicationAccepts_LoadedUtc] DEFAULT SYSUTCDATETIME()
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.key_constraints
               WHERE name = 'PK_stg_LeadApplicationAccepts' AND parent_object_id = OBJECT_ID('stg.LeadApplicationAccepts'))
    ALTER TABLE stg.[LeadApplicationAccepts] ADD CONSTRAINT [PK_stg_LeadApplicationAccepts] PRIMARY KEY CLUSTERED ([Id]);
GO
IF IndexProperty(OBJECT_ID('stg.LeadApplicationAccepts'), 'IX_LeadApplicationAccepts_LeadApplicationId', 'IndexID') IS NULL
    CREATE INDEX [IX_LeadApplicationAccepts_LeadApplicationId] ON stg.[LeadApplicationAccepts] ([LeadApplicationId]);
GO
IF IndexProperty(OBJECT_ID('stg.LeadApplicationAccepts'), 'IX_LeadApplicationAccepts_LeadId', 'IndexID') IS NULL
    CREATE INDEX [IX_LeadApplicationAccepts_LeadId] ON stg.[LeadApplicationAccepts] ([LeadId]);
GO
IF IndexProperty(OBJECT_ID('stg.LeadApplicationAccepts'), 'IX_LeadApplicationAccepts_DateCreated', 'IndexID') IS NULL
    CREATE INDEX [IX_LeadApplicationAccepts_DateCreated] ON stg.[LeadApplicationAccepts] ([DateCreated]);
GO
IF IndexProperty(OBJECT_ID('stg.LeadApplicationAccepts'), 'IX_LeadApplicationAccepts_AffiliateId', 'IndexID') IS NULL
    CREATE INDEX [IX_LeadApplicationAccepts_AffiliateId] ON stg.[LeadApplicationAccepts] ([AffiliateId]);
GO

-- Overflow.Affiliates  (snapshot)
IF OBJECT_ID('stg.Affiliates') IS NULL
BEGIN
    CREATE TABLE stg.[Affiliates] (
        [Id]                    int NOT NULL,
        [AffiliateGroupId]      int NULL,
        [Name]                  nvarchar(100) NULL,
        [DisplayName]           nvarchar(100) NULL,
        [Commission]            decimal(18,4) NULL,
        [CommissionTypeId]      int NULL,
        [PingTreeId]            int NULL,
        [JourneyVersion]        int NULL,
        [IsImmediateResponse]   bit NULL,
        [MarketingEnabled]      bit NULL,
        [IsInternalSource]      bit NULL,
        [MinimumLoanAmount]     int NULL,
        [DefaultLoanAmount]     int NULL,
        [SentLimit]             int NULL,
        [IsActive]              bit NULL,
        [IsDeleted]             bit NULL,
        [DateCreated]           datetime NULL,
        [DateModified]          datetime NULL,
        [_LoadedUtc]            datetime2(0) NOT NULL CONSTRAINT [DF_Affiliates_LoadedUtc] DEFAULT SYSUTCDATETIME()
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.key_constraints
               WHERE name = 'PK_stg_Affiliates' AND parent_object_id = OBJECT_ID('stg.Affiliates'))
    ALTER TABLE stg.[Affiliates] ADD CONSTRAINT [PK_stg_Affiliates] PRIMARY KEY CLUSTERED ([Id]);
GO
IF IndexProperty(OBJECT_ID('stg.Affiliates'), 'IX_Affiliates_DateCreated', 'IndexID') IS NULL
    CREATE INDEX [IX_Affiliates_DateCreated] ON stg.[Affiliates] ([DateCreated]);
GO

-- Overflow.AffiliateGroups  (snapshot)
IF OBJECT_ID('stg.AffiliateGroups') IS NULL
BEGIN
    CREATE TABLE stg.[AffiliateGroups] (
        [Id]                int NOT NULL,
        [Name]              nvarchar(50) NULL,
        [XeroName]          nvarchar(50) NULL,
        [PaymentCycle]      int NULL,
        [LastPaymentDate]   datetime NULL,
        [NextPaymentDate]   datetime NULL,
        [IsActive]          bit NULL,
        [IsDeleted]         bit NULL,
        [DateCreated]       datetime NULL,
        [DateModified]      datetime NULL,
        [_LoadedUtc]        datetime2(0) NOT NULL CONSTRAINT [DF_AffiliateGroups_LoadedUtc] DEFAULT SYSUTCDATETIME()
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.key_constraints
               WHERE name = 'PK_stg_AffiliateGroups' AND parent_object_id = OBJECT_ID('stg.AffiliateGroups'))
    ALTER TABLE stg.[AffiliateGroups] ADD CONSTRAINT [PK_stg_AffiliateGroups] PRIMARY KEY CLUSTERED ([Id]);
GO
IF IndexProperty(OBJECT_ID('stg.AffiliateGroups'), 'IX_AffiliateGroups_DateCreated', 'IndexID') IS NULL
    CREATE INDEX [IX_AffiliateGroups_DateCreated] ON stg.[AffiliateGroups] ([DateCreated]);
GO

-- Overflow.Lenders  (snapshot)
IF OBJECT_ID('stg.Lenders') IS NULL
BEGIN
    CREATE TABLE stg.[Lenders] (
        [Id]                   int NOT NULL,
        [Name]                 nvarchar(50) NULL,
        [SentLimit]            int NULL,
        [DeclineLimit]         int NULL,
        [BsType]               int NULL,
        [BankstatementAlias]   varchar(150) NULL,
        [ShowTaleFinScore]     bit NULL,
        [XeroName]             nvarchar(50) NULL,
        [PaymentCycle]         int NULL,
        [LastBillingDate]      datetime NULL,
        [NextBillingDate]      datetime NULL,
        [IsActive]             bit NULL,
        [IsDeleted]            bit NULL,
        [DateCreated]          datetime NULL,
        [DateModified]         datetime NULL,
        [_LoadedUtc]           datetime2(0) NOT NULL CONSTRAINT [DF_Lenders_LoadedUtc] DEFAULT SYSUTCDATETIME()
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.key_constraints
               WHERE name = 'PK_stg_Lenders' AND parent_object_id = OBJECT_ID('stg.Lenders'))
    ALTER TABLE stg.[Lenders] ADD CONSTRAINT [PK_stg_Lenders] PRIMARY KEY CLUSTERED ([Id]);
GO
IF IndexProperty(OBJECT_ID('stg.Lenders'), 'IX_Lenders_DateCreated', 'IndexID') IS NULL
    CREATE INDEX [IX_Lenders_DateCreated] ON stg.[Lenders] ([DateCreated]);
GO


/* ==================== OverflowReporting ==================== */

-- OverflowReporting.LeadApplicationStages  (incremental)
IF OBJECT_ID('stg.LeadApplicationStages') IS NULL
BEGIN
    CREATE TABLE stg.[LeadApplicationStages] (
        [Id]                  bigint NOT NULL,
        [LeadApplicationId]   uniqueidentifier NULL,
        [AffiliateId]         int NULL,
        [StageId]             int NULL,
        [DateCreated]         datetime NULL,
        [_LoadedUtc]          datetime2(0) NOT NULL CONSTRAINT [DF_LeadApplicationStages_LoadedUtc] DEFAULT SYSUTCDATETIME()
    );
END
GO
IF IndexProperty(OBJECT_ID('stg.LeadApplicationStages'), 'CIX_LeadApplicationStages_Id', 'IndexID') IS NULL
    CREATE CLUSTERED INDEX [CIX_LeadApplicationStages_Id] ON stg.[LeadApplicationStages] ([Id]);
GO
IF IndexProperty(OBJECT_ID('stg.LeadApplicationStages'), 'IX_LeadApplicationStages_LeadApplicationId', 'IndexID') IS NULL
    CREATE INDEX [IX_LeadApplicationStages_LeadApplicationId] ON stg.[LeadApplicationStages] ([LeadApplicationId]);
GO
IF IndexProperty(OBJECT_ID('stg.LeadApplicationStages'), 'IX_LeadApplicationStages_DateCreated', 'IndexID') IS NULL
    CREATE INDEX [IX_LeadApplicationStages_DateCreated] ON stg.[LeadApplicationStages] ([DateCreated]);
GO
IF IndexProperty(OBJECT_ID('stg.LeadApplicationStages'), 'IX_LeadApplicationStages_AffiliateId', 'IndexID') IS NULL
    CREATE INDEX [IX_LeadApplicationStages_AffiliateId] ON stg.[LeadApplicationStages] ([AffiliateId]);
GO

