# PublicReports API

See `../PUBLIC-API.md` for the review of what the API offers and what it
does not.

## `Invoke-PublicReport.ps1`

Calls four endpoints and records what they return.

```powershell
.\Invoke-PublicReport.ps1
```

Credentials are typed at a prompt and held as a SecureString. They are
never stored, never written to disk, and never placed on the command
line, where PowerShell keeps a history. The script needs them in plain
text for one line — Basic authentication gives no alternative — and
clears them immediately after building the header.

Before authenticating it calls one endpoint **without** credentials, so
the answer means something. `401` or `403` is what we want. `200` means
a report named `PublicReport` is genuinely public, and that needs raising
before anything else proceeds.

Two files per endpoint:

| File | Contains | Shareable |
|---|---|---|
| `<name>.json` | the raw response | **No.** May hold real lead data |
| `<name>.shape.txt` | field names, types, numeric values; strings reduced to a type and a length | Yes |

The split exists because the question is what the reports return, not
what is in them. Field names and counts answer it; a lead's details are
not needed and should not leave the machine.

## Settling the accept definition

Run `discovery/adhoc/accept_candidates.sql` over the same date range the
script prints. It counts all three candidates — `LeadApplicationAccepts`,
`SellHistory` and the `Offer` stage — on both UTC and UTC+10 day
boundaries.

Whichever matches the API's lender lead count is the definition the
business already uses, which is the question blocking every conversion
figure. If the UTC and UTC+10 counts differ and one matches, that also
establishes which timezone the API reports in — something never pinned
down.
