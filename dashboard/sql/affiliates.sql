/*
    affiliates - read by both builders (Python and PowerShell).

    {{AEST_SHIFT_HOURS}} is substituted before execution. The
    source server runs UTC; the reporting day is AEST.

    Read-only. Runs under READ UNCOMMITTED so it cannot block the
    replication subscriber it reads from.
*/
-- Names for the ids above. Password, CredfinSecretKey and
-- TalefinClientSecret live in this table and are never selected.
SELECT
    Id,
    Name,
    DisplayName,
    IsActive,
    IsDeleted,
    IsInternalSource
FROM dbo.Affiliates WITH (NOLOCK);
