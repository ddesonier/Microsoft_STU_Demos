# Running Apply-GraphTemplate.ps1 from Azure Cloud Shell

This guide runs the engine (`Apply-GraphTemplate.ps1`) from **Azure Cloud Shell** and stores
the project on an **Azure Files** share reached with **identity-based (Microsoft Entra) auth**.
It uses the script's **`-AccessToken`** parameter, which authenticates Microsoft Graph with a
**pre-acquired access token** instead of Graph PowerShell's own interactive/device-code sign-in.

> Why `-AccessToken`? Microsoft Graph PowerShell's device-code sign-in can be broken in some
> environments (notably non-public / sovereign clouds). `-AccessToken` sidesteps it entirely:
> you obtain a Microsoft Graph token by any means you like, pass it to the script as a
> `SecureString`, and the engine calls `Connect-MgGraph -AccessToken`. No Graph-PowerShell
> device code is involved.
>
> **The engine does not care how you got the token** — that decoupling is the point. The
> **recommended** source is a **dedicated app registration** with the template's Microsoft
> Graph *application* permissions, admin-consented, signing in **app-only** (no device code,
> no user interaction). This guide shows that path with the Azure CLI, plus certificate and
> managed-identity variants. The token's audience must match the selected
> `-Environment`'s Graph endpoint.

---

## Prerequisites

- An Entra tenant where you hold the roles the template needs (Global Administrator, or the
  least-privilege combination listed in the template's README).
- Rights to create a resource group, a storage account, and a role assignment (Owner or
  Contributor + User Access Administrator on the target subscription/resource group).
- Cloud Shell has the **Azure CLI**, **PowerShell 7**, and the **Microsoft.Graph** module
  preinstalled — nothing to install.
- The template's licensing prerequisites still apply (Entra ID P1/P2 as documented per template).

---

## How the files flow

```
local repo ──(upload, identity-based)──▶ Azure Files share ──(download, identity-based)──▶ Cloud Shell ──▶ Apply-GraphTemplate.ps1 -AccessToken ──▶ Microsoft Graph
```

The share is the durable home for the script and templates. You push once from your machine
(or straight from Cloud Shell if you `git clone` there) and pull into each Cloud Shell session.

---

## Step 0 — Variables (run in a Cloud Shell **Bash** session)

Open Cloud Shell (https://shell.azure.com) and choose **Bash**.

```bash
RG="rg-entra-tcg"
LOCATION="eastus"                 # your region
STG="sttcg$RANDOM"                # must be globally unique; 3-24 lowercase alphanumerics
SHARE="entra-tcg"
```

---

## Step 1 — Create the storage account (identity-based; shared-key access disabled)

Disabling shared-key access forces **all** data access to go through Microsoft Entra identity.

```bash
az group create -n "$RG" -l "$LOCATION"

az storage account create \
  -g "$RG" -n "$STG" -l "$LOCATION" \
  --sku Standard_LRS --kind StorageV2 \
  --allow-shared-key-access false \
  --min-tls-version TLS1_2
```

---

## Step 2 — Grant yourself a data-plane role (identity-based file access)

`Storage File Data Privileged Contributor` grants full read/write to file shares in the account
using your Entra identity, without configuring share-level (NTFS) permissions — ideal for a
single-operator deployment. Scope it to just this storage account.

```bash
UPN=$(az ad signed-in-user show --query userPrincipalName -o tsv)
SCOPE=$(az storage account show -g "$RG" -n "$STG" --query id -o tsv)

az role assignment create \
  --assignee "$UPN" \
  --role "Storage File Data Privileged Contributor" \
  --scope "$SCOPE"
```

> Role assignments can take a minute or two to propagate.

---

## Step 3 — Create the Azure Files share (control-plane, no keys)

`az storage share-rm` is an ARM (control-plane) call authorized by your Entra login, so it works
even with shared-key access disabled.

```bash
az storage share-rm create \
  -g "$RG" --storage-account "$STG" -n "$SHARE" --quota 5
```

---

## Step 4 — Upload the project to the share (identity-based)

`--auth-mode login` uses your Entra token (the role from Step 2); `--backup-intent` is required
when acting as a privileged data contributor. Run this from wherever the repo lives — your
workstation with the Azure CLI, or Cloud Shell after a `git clone`.

```bash
az storage file upload-batch \
  --account-name "$STG" \
  --destination "$SHARE" \
  --source ./Entra-TCG \
  --auth-mode login --backup-intent
```

> If your Azure CLI rejects `--backup-intent`, use `--enable-file-backup-request-intent`
> (older builds name the flag differently).

---

## Step 5 — Get the files into Cloud Shell

Pick **one** of the two options below.

### Option A (recommended, fully identity-based, no keys) — download into Cloud Shell

Keeps shared-key access disabled. Pull the project into your Cloud Shell home directory:

```bash
az storage file download-batch \
  --account-name "$STG" \
  --source "$SHARE" \
  --destination ~/Entra-TCG \
  --auth-mode login --backup-intent
```

### Option B — mount the share as a folder in Cloud Shell (SMB)

Mounting Azure Files over SMB in the Cloud Shell Linux container is done with `mount -t cifs`.
Two ways to authenticate the mount:

1. **Microsoft Entra Kerberos (identity-based SMB).** Enable it once on the account:

   ```bash
   az storage account update -g "$RG" -n "$STG" --enable-files-aadkerb true
   ```

   Entra Kerberos SMB mounts are fully supported from **Entra-joined / hybrid-joined Windows**
   clients. From the Linux Cloud Shell container a Kerberos ticket is not readily available, so
   for Cloud Shell prefer **Option A** (identity-based data-plane) for reliability.

2. **Storage account key (temporary).** If you specifically need the share mounted in Cloud
   Shell, temporarily re-enable key access, mount, then disable it again:

   ```bash
   az storage account update -g "$RG" -n "$STG" --allow-shared-key-access true
   KEY=$(az storage account keys list -g "$RG" -n "$STG" --query "[0].value" -o tsv)

   sudo mkdir -p /mnt/entra-tcg
   sudo mount -t cifs "//$STG.file.core.windows.net/$SHARE" /mnt/entra-tcg \
     -o "username=$STG,password=$KEY,serverino,nosharesock,actimeo=30,mfsymlinks,uid=$(id -u),gid=$(id -g),file_mode=0700,dir_mode=0700"

   # ... work with /mnt/entra-tcg ...

   # When finished, restore identity-only access:
   az storage account update -g "$RG" -n "$STG" --allow-shared-key-access false
   ```

   The files are then under `/mnt/entra-tcg` instead of `~/Entra-TCG`.

---

## Step 6 — Run the engine with `-AccessToken`

You need (1) a **Microsoft Graph access token** and (2) to hand it to the script as a
`SecureString`. The recommended token source is the dedicated app registration from
[App-only service principal](#app-only-service-principal-recommended) below (set that up once
first). Switch to PowerShell:

```bash
pwsh
```

```powershell
cd ~/Entra-TCG/Scripts          # or /mnt/entra-tcg/Scripts if you mounted (Option B)

# Acquire an app-only Graph token from your dedicated app via the Azure CLI, as a SecureString.
# (az login --service-principal is non-interactive — no device code.)
az login --service-principal -u $env:APP_ID -p $env:APP_SECRET --tenant $env:TENANT_ID --allow-no-subscriptions | Out-Null
$tokenText = az account get-access-token --resource https://graph.microsoft.com --query accessToken -o tsv
$token = ConvertTo-SecureString $tokenText -AsPlainText -Force
$tokenText = $null

# Preview:
./Apply-GraphTemplate.ps1 -TemplatePath ../Templates/hardening.json -AccessToken $token -WhatIf

# Apply:
./Apply-GraphTemplate.ps1 -TemplatePath ../Templates/hardening.json -AccessToken $token

# Collaboration (partners come from -Domain / -TenantId):
./Apply-GraphTemplate.ps1 -TemplatePath ../Templates/collaboration.json -Domain fabrikam.com -AccessToken $token -WhatIf
```

The script decodes the token, prints whether it is app-only or delegated and the Graph
roles/scopes it carries, validates them against the template, then runs. Use the Graph endpoint
that matches `-Environment` when acquiring the token (see [Sovereign clouds](#sovereign--national-clouds)).

> Tokens are short-lived (typically ~60–90 min). For a long run, re-acquire the token and pass
> a fresh `SecureString`.

---

## How `-AccessToken` auth works

1. You acquire a Microsoft Graph access token by any means and pass it as a `SecureString`.
2. The engine decodes the token's claims (without validating its signature) to read the granted
   application **roles** (app-only) or delegated **scopes** (`scp`), and reports them.
3. It connects with `Connect-MgGraph -AccessToken` (SecureString on Graph SDK v2).
4. It validates the granted roles/scopes against the template's `graphScopes`:
   - **Hard stop** only if the token carries **no** Graph roles or scopes at all (wrong
     audience, expired, or an app with no Graph permissions / missing admin consent).
   - **App-only token:** any template scope it can't match by name is an **advisory** (the
     `graphScopes` are delegated names; some app roles differ — e.g.
     `GroupSettings.ReadWrite.All` → `Directory.ReadWrite.All`). Execution proceeds; a genuinely
     missing permission surfaces as a per-resource `[Error]`.
   - **Delegated token:** missing scopes produce a **warning**; execution proceeds.

The engine never runs `az`, never handles a client secret, and is not Cloud-Shell-specific —
`-AccessToken` works anywhere you can obtain a Graph token.

Under `-WhatIf` the engine only reads state and commits nothing; the token path is identical.

---

## App-only service principal (recommended)

A dedicated app registration with the template's Microsoft Graph **application** permissions,
admin-consented, signs in **app-only** — no device code, no interactive sign-in — which is ideal
when Graph PowerShell *and* device-code flow are unreliable in your cloud.

### One-time setup (run in Cloud Shell **Bash**, as a Global Administrator)

`jq` is preinstalled in Cloud Shell. This creates the app, adds the app roles **by name**
(resolved to their IDs from the Microsoft Graph service principal), grants admin consent, and
mints a secret.

> The role names below are the canonical **`graphScopes.application`** list from each template.
> If a template's application set changes, update it there first; the managed-identity script
> reads it directly, and these arrays should be kept in sync with it.

```bash
GRAPH_APPID="00000003-0000-0000-c000-000000000000"   # Microsoft Graph

# ---- Choose the app roles for the template you will run (from graphScopes.application) ----
# hardening.json:
ROLES=(Group.ReadWrite.All Policy.ReadWrite.Authorization Policy.ReadWrite.ConditionalAccess \
       Policy.Read.All Policy.ReadWrite.AuthenticationMethod AuthenticationContext.ReadWrite.All \
       Application.Read.All Directory.ReadWrite.All)
# collaboration.json (swap the array):
# ROLES=(Policy.ReadWrite.Authorization Policy.Read.All Policy.ReadWrite.CrossTenantAccess \
#        Group.ReadWrite.All EntitlementManagement.ReadWrite.All Application.ReadWrite.All \
#        Synchronization.ReadWrite.All AuditLog.Read.All)

APP_ID=$(az ad app create --display-name "TCG Deployer" --query appId -o tsv)
az ad sp create --id "$APP_ID" >/dev/null

ROLE_JSON=$(az ad sp show --id "$GRAPH_APPID" --query "appRoles" -o json)
for R in "${ROLES[@]}"; do
  RID=$(echo "$ROLE_JSON" | jq -r --arg v "$R" '.[] | select(.value==$v) | .id')
  if [ -z "$RID" ]; then echo "WARNING: no app role named $R"; continue; fi
  az ad app permission add --id "$APP_ID" --api "$GRAPH_APPID" --api-permissions "$RID=Role"
done

# Grant admin consent for all application permissions.
# REQUIRES an active Global Administrator (or Privileged Role Administrator) role — see note below.
# Consent can lag directory replication; if the verify line shows fewer roles than expected,
# wait ~30s and re-run THIS single command.
az ad app permission admin-consent --id "$APP_ID"

# Verify the grant took: this count should equal the number of entries in ROLES.
SP_OID=$(az ad sp show --id "$APP_ID" --query id -o tsv)
echo "Granted Graph app roles: $(az rest --method GET \
  --url "https://graph.microsoft.com/v1.0/servicePrincipals/$SP_OID/appRoleAssignments" \
  --query 'length(value)' -o tsv) of ${#ROLES[@]}"

# Create a client secret, and export the values pwsh will read as $env:*:
APP_SECRET=$(az ad app credential reset --id "$APP_ID" --append --query password -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)
export APP_ID TENANT_ID APP_SECRET

echo "APP_ID    : $APP_ID"
echo "TENANT_ID : $TENANT_ID"
echo "APP_SECRET: (exported for this session)"
```

> **Admin consent requires an active Global Administrator role.** Granting tenant-wide admin
> consent for Microsoft Graph **application** permissions is a privileged operation. Ensure you
> hold an **active Global Administrator** role assignment (or, at minimum, **Privileged Role
> Administrator**) before running the consent step — if the role is PIM-eligible, **activate it
> first**. The Azure CLI does **not** report this clearly: a caller without sufficient privilege
> sees a generic `Forbidden` / `Insufficient privileges to complete the operation` (HTTP 403),
> not a message naming the required role. If you hit that error, activate/obtain the Global
> Administrator role and re-run the `az ad app permission admin-consent` line.
>
> **If consent appears to do nothing** (the verify count is lower than the number of roles):
> that is usually directory replication lag, not a permission problem — wait ~30 seconds and
> re-run the single `admin-consent` command, then re-check the verify count.

> **Why `Directory.ReadWrite.All` for hardening?** The template's delegated
> `GroupSettings.ReadWrite.All` has **no application-permission equivalent**; creating the
> `Group.Unified` group-settings object app-only requires `Directory.ReadWrite.All`. That role
> is broad — grant it deliberately, or run the group-settings resource under a delegated
> identity instead.

Then acquire the token and run as shown in [Step 6](#step-6--run-the-engine-with--accesstoken).

### Certificate instead of a secret (better hygiene)

Create the app with a certificate credential and sign in with it — no secret to store:

```bash
az ad app credential reset --id "$APP_ID" --create-cert    # writes a .pem you keep safe
az login --service-principal -u "$APP_ID" --tenant "$TENANT_ID" \
  --certificate ~/.azure/<appId>.pem --allow-no-subscriptions
# then: az account get-access-token --resource https://graph.microsoft.com --query accessToken -o tsv
```

---

## Automated setup + token — `Initialize-TcgDeployerApp.ps1` (certificate, Cloud Shell)

`Scripts/Initialize-TcgDeployerApp.ps1` automates the whole certificate path above and runs entirely in
Cloud Shell — no extra Azure resources, no client secret, no compute. It creates (or reuses) the
app registration and service principal, ensures a **self-signed certificate** credential, reads
the template's app roles from **`graphScopes.application`** (single source of truth), grants them
to the app's service principal (app-only admin consent) with a **verify-and-retry loop** for
replication lag, and — with `-AcquireToken` — signs in app-only with the certificate and returns a
Microsoft Graph token as a **SecureString** ready for the engine. It takes a **`-TemplatePath`**
(like the engine), so it works with any template and with files uploaded flat to Cloud Shell home.

```powershell
# One-time setup (run as Global Administrator in Cloud Shell's pwsh):
./Initialize-TcgDeployerApp.ps1 -TemplatePath ../Templates/hardening.json

# Setup + token in one step, then run the engine:
$token = ./Initialize-TcgDeployerApp.ps1 -TemplatePath ../Templates/hardening.json -AcquireToken
./Apply-GraphTemplate.ps1 -TemplatePath ../Templates/hardening.json -AccessToken $token -WhatIf

# Files uploaded flat to Cloud Shell home; later session, reuse the app + certificate (sovereign cloud):
$token = ./Initialize-TcgDeployerApp.ps1 -TemplatePath ~/collaboration.json -Environment USGov `
    -CertPath ~/tcg-deployer.pem -AcquireToken
```

The engine validates an app-only token against the template's `graphScopes.application` set, so the
roles the script grants line up exactly with what the run needs. `-AcquireToken` switches the
active Azure CLI session to the service principal — run `az login` to return to your own account.

> **Why not a managed identity?** A user-assigned managed identity must be attached to a compute
> host (VM / VMSS / Container Instance / Function), and Cloud Shell cannot carry one — that is an
> extra resource to run and manage. The certificate app registration is just a directory object, so
> it needs no compute and works start-to-finish in Cloud Shell. (Cloud Shell's *system* identity is
> still usable ad hoc; see "Other token sources" below.)

---

## Other token sources

`-AccessToken` accepts a token from anywhere, as long as its audience is the environment's Graph
endpoint. Examples (each yields a raw token you wrap with `ConvertTo-SecureString ... -AsPlainText -Force`):

- **Cloud Shell managed identity** (no app registration; grant *the managed identity* the Graph
  app roles): `az account get-access-token --resource https://graph.microsoft.com` after
  `az login --identity`.
- **Delegated Azure CLI user token** (fallback): `az login` then
  `az account get-access-token --resource https://graph.microsoft.com`. This carries only the
  **Azure CLI app's** consented delegated scopes — see the caveat below.
- Any MSAL / app flow you already have.

### Scopes and the Azure CLI *user* token

If you use a delegated Azure CLI **user** token, be aware `az account get-access-token` returns a
token for the **Azure CLI first-party app** (`04b07795-8ddb-461a-bbee-02f9e1bf7b46`), a
Microsoft-owned multitenant public client. You cannot change what it requests; the token carries
only the Microsoft Graph **delegated permissions already consented to that app**, acquired
**silently — no admin-consent prompt**. High scopes like `EntitlementManagement.ReadWrite.All`
are not in its default consent, so the script will warn they're missing. Extending the CLI app's
consent (an `oauth2PermissionGrant` against its service principal) **elevates it for every user
in the tenant** — which is exactly why the **dedicated app-only service principal above is
recommended** instead.

The scope check understands coverage: a `*.ReadWrite.*` scope satisfies the matching `*.Read.*`
requirement, and broad `Directory.*` scopes satisfy the common group/application scopes.

---

## Sovereign / national clouds

Point the Azure CLI at the right cloud **before** acquiring the token, pass the matching
`-Environment` to the script, and request the token for **that cloud's Graph endpoint**:

```bash
az cloud set --name AzureUSGovernment        # USGov and USGovDoD both use this cloud
az login --service-principal -u "$APP_ID" -p "$APP_SECRET" --tenant "$TENANT_ID" --allow-no-subscriptions
```

```powershell
$tokenText = az account get-access-token --resource https://graph.microsoft.us --query accessToken -o tsv
$token = ConvertTo-SecureString $tokenText -AsPlainText -Force; $tokenText = $null
./Apply-GraphTemplate.ps1 -TemplatePath ../Templates/hardening.json -Environment USGov -AccessToken $token -WhatIf
```

Use `https://graph.microsoft.us` for USGov and `https://dod-graph.microsoft.us` for USGovDoD so
the **token audience matches the cloud**.

`Initialize-TcgDeployerApp.ps1` supports the same clouds. For **built-in** clouds pass `-Environment USGov`
/ `USGovDoD`. For a **custom / sovereign / AGC** cloud, register it in the Azure CLI first, then
pass `-Environment Custom` with `-CustomAzCloud` (the registered cloud name) and
`-CustomGraphEndpoint` (that cloud's Graph endpoint / token audience):

```powershell
# az cloud register --name MyAgcCloud ... (one-time, if not built in)
$token = ./Initialize-TcgDeployerApp.ps1 -TemplatePath ../Templates/hardening.json -Environment Custom `
    -CustomAzCloud MyAgcCloud -CustomGraphEndpoint https://graph.example.gov -AcquireToken
./Apply-GraphTemplate.ps1 -TemplatePath ../Templates/hardening.json -Environment Custom `
    -CustomGraphEndpoint https://graph.example.gov -CustomLoginHost login.example.gov -AccessToken $token -WhatIf
```

(For the **engine**, `-Environment Custom` takes `-CustomGraphEndpoint` / `-CustomLoginHost`; the
**deployer** takes `-CustomGraphEndpoint` / `-CustomAzCloud` because it drives the token through
the Azure CLI's registered cloud rather than Graph PowerShell.)

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `az ad app permission admin-consent` returns `Forbidden` / `Insufficient privileges` (HTTP 403) | The caller lacks rights to grant tenant-wide admin consent. Ensure you hold an **active Global Administrator** role (or Privileged Role Administrator); if it is PIM-eligible, **activate it first**, then re-run the consent line. The CLI does not name the required role. |
| Admin consent "succeeded" but roles missing (verify count too low) | Directory replication lag — wait ~30s and re-run `az ad app permission admin-consent --id "$APP_ID"`, then re-check the verify count. |
| Script throws "supplied access token carries no Microsoft Graph scopes or application roles" | Wrong audience (must be this environment's Graph endpoint), expired token, or an app with no Graph permissions / missing admin consent. Re-acquire against the correct `--resource`, and confirm app roles + admin consent. |
| `az login --service-principal failed` | Wrong app ID/secret/tenant, expired secret, or `az cloud` set to the wrong cloud. Verify the values and re-mint the secret with `az ad app credential reset`. |
| NOTE: could not pre-verify app permission for template scope(s) | App-only tokens: the template scopes are delegated names and some app roles differ (e.g. `GroupSettings.ReadWrite.All` → `Directory.ReadWrite.All`). Advisory only — the run proceeds. |
| WARNING: the token is missing Microsoft Graph scope(s) | Delegated token lacks a required scope. Prefer an app-only token from a dedicated app, or extend the CLI app's consent — see **Scopes and the Azure CLI user token**. |
| `403 Authorization_RequestDenied` during apply | The identity is missing a Graph permission (above) or the required Entra directory role. For app-only, confirm the app role is consented; for delegated, confirm your account's roles. |
| `Connect-MgGraph : ... AccessToken` type error | Old Microsoft.Graph — the engine handles v1 (string) and v2 (SecureString); `az upgrade` / update the module if needed. |
| `az storage file ... --backup-intent` not recognized | Older Azure CLI — use `--enable-file-backup-request-intent`, or `az upgrade`. |
| CIFS mount hangs/fails in Cloud Shell | Prefer Option A (identity-based download). SMB mount reliability in the container varies. |

---

## Cleanup

### Remove the deployment app + certificate (do this after deployment)

The `Initialize-TcgDeployerApp.ps1` app registration and its certificate are a **privileged, throwaway
deployment credential** (they carry the template's Graph application permissions). Once the
templates are applied, **delete the whole app registration** so nothing high-privilege lingers —
this removes the service principal and every credential in one step. The script prints these same
commands at the end of each run.

```bash
# Recommended — delete the whole throwaway app registration (removes its SP and all credentials):
az ad app delete --id <appId>

# And delete the local key/cert/PEM from Cloud Shell home:
rm -f ~/tcg-deployer.key ~/tcg-deployer.crt ~/tcg-deployer.pem

# (If you must keep the app, remove just its certificate instead:)
#   az ad app credential list --id <appId> --cert --query '[].keyId' -o tsv
#   az ad app credential delete --id <appId> --key-id <keyId> --cert
```

Deleting the deployment app does **not** affect anything the templates created in Entra.

### Remove the optional storage account (only if you used the Azure Files path)

```bash
az group delete -n "$RG" --yes --no-wait
```

This removes the storage account and file share. It does **not** touch anything the template
created in Entra.
