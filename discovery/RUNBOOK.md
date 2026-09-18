# Runbook: running the discovery pack

Step-by-step. Follow it in order.

**You cannot break anything with this pack.** Every script is read-only —
no writes, no schema changes, no deletes. The worst possible outcome is
an error message. If something looks wrong, stop and ask; nothing will
have been altered.

---

## Before you start

You need:

- Access to **FLOWWEB4** (RDP or Bastion — either is fine)
- Your **SQL login and password** for `fw04-sqlreporting01`
- About 5 minutes

Optional but sensible: open SSMS first and connect to
`fw04-sqlreporting01.database.windows.net` the way you normally would.
If that works, the pack will work. If it does not, fix that first —
nothing below will succeed until it does.

---

## Step 1 — Get the files onto FLOWWEB4

**Option A: download from GitHub (if the VM has internet access)**

Open a browser on the VM and go to:

```
https://github.com/LaytonDogstar/AUS-Reporting/archive/refs/heads/claude/code-review-tsyca6.zip
```

That downloads a ZIP. Right-click it → **Extract All** → extract to
`C:\discovery` (or anywhere you like — just remember where).

You will end up with a folder named something like
`AUS-Reporting-claude-code-review-tsyca6`, containing a `discovery`
folder. That `discovery` folder is what you want.

> If the repo has been made private, this URL will ask you to sign in.
> Sign in as `LaytonDogstar` and it will work.

**Option B: copy from your own machine (if the VM has no internet)**

Download the ZIP on your local machine, extract it, then copy the
`discovery` folder into the RDP session (Ctrl+C on your machine, Ctrl+V
inside the remote desktop). This needs clipboard sharing enabled in the
RDP client, which it is by default.

---

## Step 2 — Open the right PowerShell

Press the **Windows key** and type `powershell`.

Open **"Windows PowerShell"** — the one with the **blue** icon.

> Do **not** open "PowerShell 7" (black icon) or "PowerShell ISE". The
> script needs Windows PowerShell 5.1. If you open the wrong one it will
> stop immediately and tell you so — it will not do anything strange.

---

## Step 3 — Go to the folder and unblock the files

Windows blocks scripts that came from the internet. This clears that
flag for these files only:

```powershell
cd C:\discovery\AUS-Reporting-claude-code-review-tsyca6\discovery
Get-ChildItem -Recurse | Unblock-File
```

Adjust the path if you extracted somewhere else. If you are unsure of
the path, navigate to the folder in File Explorer, click the address
bar, copy it, and use that.

Then allow scripts to run **in this window only**:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

> `-Scope Process` means this applies only to the PowerShell window you
> have open right now. It changes nothing on the machine and reverts the
> moment you close the window.

---

## Step 4 — Run it against the reporting database

```powershell
.\run-discovery.ps1 -ServerName fw04-sqlreporting01.database.windows.net -Database OverflowReporting -SkipDateCoverage
```

It will ask for:

1. **SQL login** — your username, the same one you use in SSMS
2. **Password** — your password

> **The password will not appear as you type it.** No dots, no asterisks,
> nothing. That is normal and means it is working. Type it and press
> Enter.

You should then see each script run with a green `ok`. The whole thing
takes seconds with `-SkipDateCoverage`.

---

## Step 5 — Run it again against the other database

Azure SQL cannot switch databases on one connection, so this is a second
run:

```powershell
.\run-discovery.ps1 -ServerName fw04-sqlreporting01.database.windows.net -Database Overflow -SkipDateCoverage
```

Same prompts, same credentials.

---

## Step 6 — Check what you got

```powershell
Get-ChildItem .\output -Recurse -Filter *.csv | Select-Object Directory, Name, Length
```

You should have two folders under `output\`, one per database, each with
about a dozen CSVs plus `_run_summary.csv`.

Open `_run_summary.csv` — it lists every script, how many rows it
returned, and any errors. If a row says `ERROR:`, copy that text and
send it to me; it tells me exactly what to fix.

---

## Step 7 — Have a quick look before sending

Open `06_sensitive_columns_set01.csv`. It lists **column names only** —
no data. Confirm that is all you see.

This takes thirty seconds and means nobody has to wonder afterwards
whether customer data left the VM.

---

## Step 8 — Send the output back

Zip it:

```powershell
Compress-Archive -Path .\output\* -DestinationPath $env:USERPROFILE\Desktop\discovery-output.zip
```

The ZIP lands on the desktop. Copy it back to your machine through the
RDP clipboard, or email it to yourself, then send it on.

---

## If something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| `cannot be loaded because running scripts is disabled` | Execution policy | Re-run the `Set-ExecutionPolicy` line in Step 3 |
| `This script needs Windows PowerShell 5.1` | Wrong PowerShell | Close it, open the **blue** Windows PowerShell (Step 2) |
| `Login failed for user` | Wrong username or password | Check them in SSMS first |
| `Cannot open server ... requested by the login` | Firewall — your IP is not allow-listed | Run it from FLOWWEB4, not your local machine |
| `The server was not found or was not accessible` | Server name typo, or no route | Check the name against SSMS |
| `Invalid object name` or similar in `_run_summary.csv` | A script needs adjusting for this schema | Send me the error text |

Anything else: send me the red text. Because everything is read-only, a
failure means "that script did not run", never "something changed".

---

## Optional: the slower script

`05_date_coverage.sql` is excluded by `-SkipDateCoverage`. It is the only
script that reads data (date columns only — no names, no bank details)
and the only slow one, because it scans for earliest and latest dates.

Once we have seen the table sizes from the first run, drop the flag to
include it:

```powershell
.\run-discovery.ps1 -ServerName fw04-sqlreporting01.database.windows.net -Database OverflowReporting
```

Do that on the **reporting replica**, never live.

---

## Doing it by hand instead

If PowerShell is blocked on the VM, the SQL files work fine on their own:

1. Open SSMS, connect to `fw04-sqlreporting01.database.windows.net`
2. Pick `OverflowReporting` in the database dropdown
3. Open `00_server_context.sql`, press **F5**
4. Right-click each result grid → **Save Results As** → CSV
5. Repeat for `01` through `08`, then switch database and do it again

Slower, same result.
