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

## Step 2 — Publish it, with a login

**Decided: Azure Static Web Apps with Entra sign-in.** Free tier, real
work-account login, revocable per person, and it stays inside Azure.

### What to ask for

Someone with rights to create Azure resources does this once. The free
tier costs nothing.

> Please create an **Azure Static Web App**:
>
> - Any resource group; region nearest us
> - **Hosting plan: Free**
> - **Deployment source: Other** (not GitHub — the page is generated on a
>   server, not built from the repo)
>
> Once created, from the app's **Overview** blade please send me:
>
> 1. The **URL** (like `https://<name>.azurestaticapps.net`)
> 2. The **deployment token** — *Manage deployment token* on that blade
>
> Then under **Role management**, please add the people who should have
> access. Everyone else is refused at sign-in.

The deployment token is a credential — it can publish to the site.
Treat it like a password.

### Publishing

```powershell
.\Publish-StaticWebApp.ps1 -File C:\reports\dashboard.html `
  -AppName <name> -DeploymentToken "..."
```

The upload includes a `staticwebapp.config.json` that requires
authentication on every route. **That file is what enforces the login** —
without it the site is public whatever the portal shows.

To have every scheduled build publish, set the token once for the
account the task runs as:

```powershell
[Environment]::SetEnvironmentVariable("AUS_SWA_TOKEN", "...", "User")
```

### If the deploy fails

The script posts a zip to the Static Web App deployment endpoint. That
avoids the SWA CLI, which needs Node.js — one more install on a machine
where installing things is the problem. It is a **less well-trodden
route** than the CLI or GitHub Actions, and it is the one part of this
project I have not been able to test.

If it does not work, **Azure App Service on the free tier reaches the
same place** — a URL with Entra sign-in — over a much better documented
deployment API:

> Please create an **App Service** (Free F1, Windows) and enable
> **Authentication** with Microsoft Entra ID, restricted to our tenant.
> Send me the **publish profile** from the Overview blade.

Deployment there is a ZIP POST to `/api/zipdeploy` with the publish
profile credentials — the standard, heavily documented Kudu endpoint.
Say the word and I will write it; it is about twenty lines.

---

## Before you turn it on

The page carries no personal data and no bank details — aggregated daily
counts, affiliate names, loan averages — but it is commercially
sensitive. With Entra sign-in that is handled: only people you add under
Role management can see it.

Worth confirming the sign-in actually works before sharing the URL. Open
it in a private browser window; you should be asked to sign in rather
than shown the dashboard.

## What the URL will and will not be

**It will** be current to the last build, which the page states in its
own header.

**It will not** be live. The source runs about two minutes behind, and
the page is a snapshot. An hourly rebuild means an hourly figure —
which is why the header says "data as at" rather than implying
currency.
