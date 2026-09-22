# Getting a URL

Two steps, and only the second needs anything from anyone else.

| Step | Needs | Gives you |
|---|---|---|
| 1. Schedule the build | Nothing | A file on FLOWWEB4 that refreshes itself |
| 2. Publish it | One Azure storage account | A URL |

---

## Step 1 — Schedule it

On FLOWWEB4, in the `dashboard` folder.

**Save the password once.** A scheduled task cannot answer a prompt:

```powershell
.\Save-DashboardCredential.ps1
```

It is encrypted with Windows DPAPI, which ties it to **this Windows
account on this machine**. Copied elsewhere, or read as another user, it
decrypts to nothing.

**Register the task:**

```powershell
.\Register-DashboardTask.ps1 -Minutes 60 -Out C:\reports\dashboard.html
```

Runs as the account you registered it under — which must be the same one
that saved the credential, or it will start and fail to decrypt. It runs
whether or not anyone is logged on.

**Check it:**

```powershell
Start-ScheduledTask -TaskName "AUS Reporting Dashboard"
Get-ScheduledTaskInfo -TaskName "AUS Reporting Dashboard"
```

`LastTaskResult` of `0` is success.

To stop it: `.\Register-DashboardTask.ps1 -Remove`

> Rebuilding faster than every 5 minutes is refused. The source is about
> two minutes behind live, so there is nothing fresher to fetch.

---

## Step 2 — Publish it

### What to ask for

Someone with rights to create Azure resources needs to do this once. It
costs pennies a month.

> Please create a **storage account** in the same subscription and region
> as `fw04-sqlreporting01`, with:
>
> - **Static website** enabled (Settings → Static website → Enabled,
>   index document `index.html`). This creates a `$web` container and
>   gives a primary endpoint URL.
> - A **container SAS** for `$web` with **Write** and **Create**
>   permission only, expiring in 12 months.
>
> Then send me the primary endpoint URL and the SAS URL.

Nothing else is needed. No VM, no app service, no deployment pipeline.

### Point the build at it

```powershell
.\Register-DashboardTask.ps1 -Minutes 60 `
  -Out C:\reports\dashboard.html `
  -PublishSasUrl "https://<account>.blob.core.windows.net/`$web?sv=...&sig=..."
```

Each run writes the file locally **and** uploads it. If the upload
fails, the local file is still written and the error says why.

The URL is then the static website endpoint, something like:

```
https://<account>.z8.web.core.windows.net/
```

---

## Read this before turning it on

**A static website endpoint is public.** Anyone with the URL can read
it, with no login. The page carries no personal data and no bank
details — aggregated daily counts, affiliate names, loan averages — but
it is commercially sensitive, and an unlisted URL is not access control.

Three ways to handle that, in increasing order of effort:

| Option | Effort | Access control |
|---|---|---|
| Static website endpoint | None | **None.** Anyone with the link |
| Private container + read SAS in the link | Minutes | The link *is* the credential; expires; can be revoked |
| **Azure Static Web Apps with Entra login** | An hour | Real accounts, real sign-in, revocable per person |

**Azure Static Web Apps is the right answer** if this will be shared
beyond a couple of people: free tier, sign in with existing work
accounts, and it stays inside Azure. The upload step differs — it uses a
deployment token rather than a blob SAS — so say the word and I will add
it.

The blob route is the fastest way to have a working URL today. Just
decide it deliberately rather than by default.

---

## What the URL will and will not be

**It will** be current to the last build, which the page states in its
own header.

**It will not** be live. The source runs about two minutes behind, and
the page is a snapshot. An hourly rebuild means an hourly figure —
which is why the header says "data as at" rather than implying
currency.
