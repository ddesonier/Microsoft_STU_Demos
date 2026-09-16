#Requires -Version 7.0
<#
.SYNOPSIS
    Sets up a certificate-based Microsoft Entra app registration that Apply-GraphTemplate.ps1 can
    use for app-only Microsoft Graph auth (via -AccessToken) entirely from Azure Cloud Shell — no
    extra Azure resources, no client secret, and no compute host.

.DESCRIPTION
    This is the minimal-footprint credential path for the engine. An app registration is just a
    directory object (no resource group, storage, or VM), so everything runs in Cloud Shell:

      1. Creates (or reuses) an app registration and its service principal.
      2. Ensures the app has a self-signed CERTIFICATE credential (no client secret is ever
         created), generated with openssl at a deterministic path under -CertDir (default the
         user's home). The private key stays local; only the public certificate is uploaded.
      3. Resolves the template's Microsoft Graph app-role IDs by name from graphScopes.application
         (the template is the single source of truth).
      4. Grants those app roles to the app's service principal (app-only admin consent) idempotently.
      5. Verifies the grant landed, retrying through directory replication lag.
      6. With -AcquireToken, signs in app-only with the certificate and RETURNS a Microsoft Graph
         access token as a SecureString, ready to pipe into Apply-GraphTemplate.ps1 -AccessToken.

    Requires the Azure CLI and a signed-in caller who is Global Administrator (or Privileged Role
    Administrator), because granting Microsoft Graph application permissions is privileged.

    Why certificate (not a managed identity) for Cloud Shell? A user-assigned managed identity must
    be attached to a compute host (VM / VMSS / Container Instance / Function) and Cloud Shell cannot
    carry one — that is an extra resource to run and manage. A certificate app registration needs no
    compute and works start-to-finish in Cloud Shell. See ReadMEs/CloudShellDeployment.md.

.PARAMETER TemplatePath
    Path to the JSON template whose application permission set to grant (e.g.
    ../Templates/hardening.json, or just hardening.json when files are uploaded flat to Cloud
    Shell). The app-role list is read from that template's graphScopes.application. Any template
    works — not limited to the two shipped ones.

.PARAMETER AppName
    Display name of the app registration to create or reuse. Default 'TCG Deployer'.

.PARAMETER Environment
    Cloud: Global (default), USGov, USGovDoD, or Custom. Selects the Azure CLI cloud, the Microsoft
    Graph endpoint used for role assignment, and the token audience. For Custom, also pass
    -CustomAzCloud and -CustomGraphEndpoint.

.PARAMETER CustomAzCloud
    Required with -Environment Custom. The Azure CLI cloud name to activate via 'az cloud set
    --name' (register it first with 'az cloud register' if it is not built in). Mirrors the
    engine's sovereign/AGC support.

.PARAMETER CustomGraphEndpoint
    Required with -Environment Custom. That cloud's Microsoft Graph endpoint (e.g.
    https://graph.microsoft.us or an AGC endpoint). Used as the token audience and the base URL
    for the app-role-assignment calls.

.PARAMETER CertPath
    Path to an existing certificate PEM (cert + private key) to reuse for sign-in. If omitted, a new
    self-signed certificate is generated with openssl at a deterministic path under -CertDir and its
    path is reported. Reuse it in later sessions by passing -CertPath along with -AcquireToken.

.PARAMETER CertDir
    Directory for the generated key/cert/PEM files (named from the app display name, e.g.
    tcg-deployer.pem). Default: the current user's home directory.

.PARAMETER CertDays
    Validity period in days for the generated self-signed certificate (default 7). This is a
    privileged deployment credential, so keep it short — just long enough to cover your rollout
    window. Increase only if a single credential must span a longer deployment.

.PARAMETER NewCert
    Force generation of a new self-signed certificate credential even if -CertPath points to one.

.PARAMETER AcquireToken
    After setup, sign in app-only with the certificate and emit a Microsoft Graph access token as a
    SecureString on the pipeline (the script's only pipeline output). NOTE: this switches the active
    Azure CLI session to the service principal; run `az login` again to return as yourself.

.PARAMETER MaxVerifyAttempts
    How many times to re-check that all app roles are assigned before giving up (default 12).

.PARAMETER VerifyDelaySeconds
    Delay between verification attempts, to ride out directory replication lag (default 10).

.EXAMPLE
    # One-time setup (run as Global Administrator in Cloud Shell):
    ./Initialize-TcgDeployerApp.ps1 -TemplatePath ../Templates/hardening.json

.EXAMPLE
    # Setup and get a token in one go, then run the engine:
    $token = ./Initialize-TcgDeployerApp.ps1 -TemplatePath ../Templates/hardening.json -AcquireToken
    ./Apply-GraphTemplate.ps1 -TemplatePath ../Templates/hardening.json -AccessToken $token -WhatIf

.EXAMPLE
    # Files uploaded flat to Cloud Shell home, later session, reuse the app + certificate (USGov):
    $token = ./Initialize-TcgDeployerApp.ps1 -TemplatePath ~/collaboration.json -Environment USGov `
        -CertPath ~/tcg-deployer.pem -AcquireToken

.EXAMPLE
    # Custom / sovereign / AGC cloud (register the cloud with 'az cloud register' first):
    $token = ./Initialize-TcgDeployerApp.ps1 -TemplatePath ../Templates/hardening.json -Environment Custom `
        -CustomAzCloud MyAgcCloud -CustomGraphEndpoint https://graph.example.gov -AcquireToken
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [Parameter(Mandatory = $true)]
    [string] $TemplatePath,

    [string] $AppName = 'TCG Deployer',

    [ValidateSet('Global', 'USGov', 'USGovDoD', 'Custom')]
    [string] $Environment = 'Global',

    [string] $CustomAzCloud,

    [string] $CustomGraphEndpoint,

    [string] $CertPath,

    [string] $CertDir = $HOME,

    [int] $CertDays = 7,

    [switch] $NewCert,

    [switch] $AcquireToken,

    [int] $MaxVerifyAttempts = 12,

    [int] $VerifyDelaySeconds = 10
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Well-known Microsoft Graph resource app ID (identical across clouds).
$GraphAppId = '00000003-0000-0000-c000-000000000000'

# Cloud -> (Azure CLI cloud name, Microsoft Graph endpoint host).
$CloudMap = @{
    Global   = @{ AzCloud = 'AzureCloud';         Graph = 'https://graph.microsoft.com' }
    USGov    = @{ AzCloud = 'AzureUSGovernment';   Graph = 'https://graph.microsoft.us' }
    USGovDoD = @{ AzCloud = 'AzureUSGovernment';   Graph = 'https://dod-graph.microsoft.us' }
}
if ($Environment -eq 'Custom') {
    if (-not $CustomAzCloud -or -not $CustomGraphEndpoint) {
        throw "-Environment Custom requires -CustomAzCloud (a cloud name registered with 'az cloud register'/'az cloud set') and -CustomGraphEndpoint (that cloud's Microsoft Graph endpoint, e.g. an Azure Government/sovereign or AGC endpoint)."
    }
    $CloudMap['Custom'] = @{ AzCloud = $CustomAzCloud; Graph = $CustomGraphEndpoint.TrimEnd('/') }
}
$Cloud     = $CloudMap[$Environment]
$GraphHost = $Cloud.Graph

# ---------------------------------------------------------------------------
# Console reporting helpers (all go to the host, NOT the pipeline, so -AcquireToken's
# SecureString is the only pipeline output).
# ---------------------------------------------------------------------------
function Write-Step { param([string] $Message) Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok   { param([string] $Message) Write-Host "    [ok]   $Message" -ForegroundColor Green }
function Write-Info { param([string] $Message) Write-Host "    [info] $Message" -ForegroundColor DarkGray }
function Write-Warn { param([string] $Message) Write-Host "    [warn] $Message" -ForegroundColor Yellow }

function Write-CleanupGuidance {
    # Post-deployment cleanup: this app + certificate is a privileged, throwaway deployment
    # credential. The recommended teardown is to delete the whole app registration so nothing
    # high-privilege lingers (removes the SP and every credential in one step).
    param([string] $AppId, [string] $CertFile)
    Write-Host ""
    Write-Host "Post-deployment cleanup (do this once the deployment is complete):" -ForegroundColor Yellow
    Write-Host "  # Recommended - delete the whole throwaway app registration (removes SP + all creds):"
    Write-Host ("  az ad app delete --id {0}" -f $AppId)
    if ($CertFile) {
        $base = [IO.Path]::Combine([IO.Path]::GetDirectoryName($CertFile), [IO.Path]::GetFileNameWithoutExtension($CertFile))
        Write-Host "  # And delete the local key/cert/PEM files from Cloud Shell:"
        Write-Host ("  rm -f {0}.key {0}.crt {0}.pem" -f $base)
    }
    Write-Host "  # (If you must keep the app, remove just its certificate instead:)"
    Write-Host ("  #   az ad app credential list --id {0} --cert --query '[].keyId' -o tsv" -f $AppId)
    Write-Host ("  #   az ad app credential delete --id {0} --key-id <keyId> --cert" -f $AppId)
}

function Get-JsonPayload {
    # Extracts the JSON value out of az stdout, tolerant of any non-JSON preamble/appendix (e.g. a
    # credential-protection notice or WARNING: line that az may print to stdout on some versions).
    # Returns $null when there is no JSON object/array present.
    param([string] $Text)
    if (-not $Text) { return $null }
    $startObj = $Text.IndexOf('{'); $startArr = $Text.IndexOf('[')
    $candidates = @($startObj, $startArr) | Where-Object { $_ -ge 0 }
    if (-not $candidates) { return $null }
    $start = ($candidates | Measure-Object -Minimum).Minimum
    $open  = $Text[$start]
    $close = if ($open -eq '{') { '}' } else { ']' }
    $end   = $Text.LastIndexOf($close)
    if ($end -lt $start) { return $null }
    return $Text.Substring($start, $end - $start + 1)
}

function Invoke-Az {
    # Runs the Azure CLI and returns parsed JSON (or raw text with -Raw). Throws on failure.
    param([Parameter(Mandatory)][string[]] $CliArgs, [switch] $Raw)
    # Route stderr to its own temp file so az WARNING: text never merges into the stdout we parse;
    # --only-show-errors suppresses most warnings; Get-JsonPayload strips any residual preamble.
    $errFile = New-TemporaryFile
    try {
        $stdout = (& az @CliArgs --only-show-errors 2>$errFile | Out-String)
        $code = $LASTEXITCODE
        if ($code -ne 0) {
            $err = (Get-Content $errFile -Raw -ErrorAction SilentlyContinue)
            throw ("az {0} failed:`n{1}`n{2}" -f ($CliArgs -join ' '), $stdout.Trim(), ($err | Out-String).Trim())
        }
    }
    finally { Remove-Item $errFile -Force -ErrorAction SilentlyContinue }
    if ($Raw) { return $stdout.Trim() }
    $json = Get-JsonPayload -Text $stdout
    if (-not $json) { return $null }
    return $json | ConvertFrom-Json
}

function Invoke-GraphRest {
    # Wraps `az rest` against Microsoft Graph. Body is written to a temp file to avoid cross-platform
    # quoting problems. Returns parsed JSON, $null for empty, or an object with .Failed on HTTP error.
    param(
        [Parameter(Mandatory)][ValidateSet('GET', 'POST')][string] $Method,
        [Parameter(Mandatory)][string] $Url,
        [object] $Body
    )
    $azArgs = @('rest', '--method', $Method, '--url', $Url, '--resource', $GraphHost, '--only-show-errors')
    $tmp = $null
    if ($Body) {
        $tmp = New-TemporaryFile
        ($Body | ConvertTo-Json -Depth 10 -Compress) | Set-Content -Path $tmp -Encoding utf8
        $azArgs += @('--headers', 'Content-Type=application/json', '--body', "@$tmp")
    }
    $errFile = New-TemporaryFile
    try {
        $stdout = (& az @azArgs 2>$errFile | Out-String)
        $code = $LASTEXITCODE
        if ($code -ne 0) {
            $err = (Get-Content $errFile -Raw -ErrorAction SilentlyContinue)
            return [PSCustomObject]@{ Failed = $true; Text = ($stdout.Trim() + "`n" + ($err | Out-String).Trim()).Trim() }
        }
        $json = Get-JsonPayload -Text $stdout
        if (-not $json) { return $null }
        return $json | ConvertFrom-Json
    }
    finally {
        if ($tmp -and (Test-Path $tmp)) { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
        Remove-Item $errFile -Force -ErrorAction SilentlyContinue
    }
}

# ---------------------------------------------------------------------------
# 0. Preflight
# ---------------------------------------------------------------------------
Write-Step "Preflight: Azure CLI, cloud, and sign-in"
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw "Azure CLI ('az') was not found on PATH. Install it or run from Azure Cloud Shell."
}
Invoke-Az -CliArgs @('cloud', 'set', '--name', $Cloud.AzCloud) | Out-Null
Write-Ok ("Azure CLI cloud set to {0} (Graph endpoint {1})" -f $Cloud.AzCloud, $GraphHost)
$account = Invoke-Az -CliArgs @('account', 'show')
if (-not $account) { throw "No active Azure CLI login. Run 'az login' as your Global Administrator user, then re-run." }

# Read tenantId defensively (property shape varies by login type / az version).
$TenantId = $null
foreach ($p in @('tenantId', 'homeTenantId')) {
    if ($account.PSObject.Properties.Name -contains $p -and $account.$p) { $TenantId = $account.$p; break }
}
if (-not $TenantId) {
    $TenantId = Invoke-Az -Raw -CliArgs @('account', 'show', '--query', 'tenantId', '-o', 'tsv')
}
if (-not $TenantId) { throw "Could not determine the tenant ID from 'az account show'. Run 'az login' as your Global Administrator user and re-run." }

# Determine the signed-in principal type. Granting Graph app roles is authorized by the USER's
# directory role, so the CLI must be logged in as your user - NOT as a service principal (which is
# what 'az login --service-principal' / a prior -AcquireToken run leaves behind).
$userName = ''
$userType = ''
if ($account.PSObject.Properties.Name -contains 'user' -and $account.user) {
    if ($account.user.PSObject.Properties.Name -contains 'name' -and $account.user.name) { $userName = $account.user.name }
    if ($account.user.PSObject.Properties.Name -contains 'type' -and $account.user.type) { $userType = $account.user.type }
}
if ($userType -eq 'servicePrincipal') {
    throw ("Azure CLI is currently signed in as a SERVICE PRINCIPAL ({0}), not your user account. This happens after a prior -AcquireToken run (which logs in as the app). Granting Microsoft Graph app roles needs YOUR Global Administrator role, so run:`n    az login`nas your GA user, then re-run this script." -f $userName)
}
Write-Ok ("Signed in to tenant {0} as {1}" -f $TenantId, $userName)
Write-Info "Granting Graph application permissions requires Global Administrator (or Privileged Role Administrator)."

# ---------------------------------------------------------------------------
# 1. App-role set from the template (single source of truth)
# ---------------------------------------------------------------------------
if (-not (Test-Path $TemplatePath)) { throw "Template file not found: $TemplatePath" }
$templateFile = (Resolve-Path $TemplatePath).Path
try { $tpl = Get-Content $templateFile -Raw | ConvertFrom-Json } catch { throw "Template '$templateFile' is not valid JSON: $($_.Exception.Message)" }
# Friendly label for messages/recipes: prefer the template's own 'template' field, else the file base name.
$templateLabel = if ($tpl.PSObject.Properties.Name -contains 'template' -and $tpl.template) { $tpl.template } else { [IO.Path]::GetFileNameWithoutExtension($templateFile) }
Write-Step ("Reading app roles from template graphScopes.application ('{0}')" -f $templateLabel)
$WantRoles = @()
if ($tpl.PSObject.Properties.Name -contains 'graphScopes' -and $tpl.graphScopes -and ($tpl.graphScopes.PSObject.Properties.Name -contains 'application')) {
    $WantRoles = @($tpl.graphScopes.application)
}
if ($WantRoles.Count -eq 0) { throw "Template '$templateFile' does not declare a graphScopes.application list. Add the Microsoft Graph application (app-role) permissions there, then re-run." }
Write-Ok ("{0} app roles requested" -f $WantRoles.Count)

# ---------------------------------------------------------------------------
# 2. Microsoft Graph service principal and app-role ID resolution
# ---------------------------------------------------------------------------
Write-Step "Resolving the Microsoft Graph service principal and its app roles"
$graphSp = Invoke-Az -CliArgs @('ad', 'sp', 'show', '--id', $GraphAppId)
$graphSpId = $graphSp.id
$roleIndex = @{}
foreach ($ar in $graphSp.appRoles) {
    if ($ar.value -and ($ar.allowedMemberTypes -contains 'Application')) { $roleIndex[$ar.value] = $ar.id }
}
$resolved = @{}
$missingRoles = @()
foreach ($r in $WantRoles) {
    if ($roleIndex.ContainsKey($r)) { $resolved[$r] = $roleIndex[$r] } else { $missingRoles += $r }
}
if ($missingRoles.Count -gt 0) { throw ("Not Microsoft Graph *application* roles: {0}. Fix graphScopes.application in {1}." -f ($missingRoles -join ', '), $templateFile) }
Write-Ok ("Resolved {0} app roles" -f $resolved.Count)
$resolved.Keys | Sort-Object | ForEach-Object { Write-Info $_ }

# ---------------------------------------------------------------------------
# 3. Create or reuse the app registration + service principal
# ---------------------------------------------------------------------------
Write-Step ("Creating or reusing app registration '{0}'" -f $AppName)
$existingApps = Invoke-Az -CliArgs @('ad', 'app', 'list', '--display-name', $AppName, '--query', '[].{appId:appId,id:id}')
$appId = $null
if ($existingApps -and @($existingApps).Count -ge 1) {
    $appId = @($existingApps)[0].appId
    Write-Ok ("Reusing app registration (appId {0})" -f $appId)
    if (@($existingApps).Count -gt 1) { Write-Warn ("{0} apps share the name '{1}'; using the first. Pass a unique -AppName to disambiguate." -f @($existingApps).Count, $AppName) }
}
else {
    if ($PSCmdlet.ShouldProcess($AppName, "Create app registration")) {
        $app = Invoke-Az -CliArgs @('ad', 'app', 'create', '--display-name', $AppName)
        $appId = $app.appId
        Write-Ok ("Created app registration (appId {0})" -f $appId)
    }
    else { Write-Warn "Skipped app creation (-WhatIf). Cannot continue."; return }
}

# Ensure the service principal exists for this app.
$appSp = $null
try { $appSp = Invoke-Az -CliArgs @('ad', 'sp', 'show', '--id', $appId) } catch { $appSp = $null }
if (-not $appSp) {
    if ($PSCmdlet.ShouldProcess($appId, "Create service principal")) {
        $appSp = Invoke-Az -CliArgs @('ad', 'sp', 'create', '--id', $appId)
        Write-Ok "Created service principal for the app"
    }
}
else { Write-Ok "Service principal already exists" }
$appSpId = $appSp.id

# ---------------------------------------------------------------------------
# 4. Ensure a certificate credential (never a client secret)
# ---------------------------------------------------------------------------
# We generate the key pair ourselves with openssl at a DETERMINISTIC path (instead of az
# --create-cert, which writes a randomly-named PEM), so the path is predictable across runs and
# the private key never leaves this session — only the public certificate is uploaded to the app.
Write-Step "Ensuring a certificate credential on the app"
$haveUsableCert = ($CertPath -and (Test-Path $CertPath) -and -not $NewCert)
if ($haveUsableCert) {
    Write-Ok ("Reusing existing certificate PEM: {0}" -f $CertPath)
}
else {
    if ($PSCmdlet.ShouldProcess($appId, "Create self-signed certificate credential")) {
        if (-not (Get-Command openssl -ErrorAction SilentlyContinue)) {
            throw "openssl was not found on PATH. It is preinstalled in Azure Cloud Shell; install it or pass -CertPath to an existing PEM (cert + private key)."
        }
        # Deterministic file names derived from the app display name.
        $slug    = ($AppName.ToLower() -replace '[^a-z0-9]+', '-').Trim('-')
        if (-not $slug) { $slug = 'tcg-deployer' }
        $keyPath = Join-Path $CertDir "$slug.key"   # private key (stays local)
        $crtPath = Join-Path $CertDir "$slug.crt"   # public certificate (uploaded to the app)
        $pemPath = Join-Path $CertDir "$slug.pem"   # combined cert + key (for az login --certificate)

        Write-Info ("Generating a self-signed certificate at {0} (valid {1} days)" -f $pemPath, $CertDays)
        $subj = "/CN=$AppName"
        & openssl req -x509 -newkey rsa:2048 -keyout $keyPath -out $crtPath -days $CertDays -nodes -subj $subj 2>$null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $keyPath) -or -not (Test-Path $crtPath)) {
            throw "openssl failed to generate the certificate key pair (exit $LASTEXITCODE)."
        }
        # Combine into a single PEM (cert first, then private key) for certificate sign-in.
        Set-Content -Path $pemPath -Value ((Get-Content $crtPath -Raw) + (Get-Content $keyPath -Raw)) -NoNewline
        try { & chmod 600 $pemPath $keyPath 2>$null } catch { }

        # Register only the PUBLIC certificate on the app; --append preserves any existing credentials.
        # Invoke-Az throws on a non-zero exit, so reaching the next line means registration succeeded;
        # this command may legitimately return little or no JSON on success, so we don't inspect $reg.
        Invoke-Az -CliArgs @('ad', 'app', 'credential', 'reset', '--id', $appId, '--cert', "@$crtPath", '--append') | Out-Null

        $CertPath = $pemPath
        Write-Ok ("Created and registered certificate. PEM (cert + key): {0}" -f $CertPath)
        Write-Info "The private key never left this session; only the public certificate was uploaded. No client secret was created."
    }
    else { Write-Warn "Skipped certificate creation (-WhatIf)." }
}

# ---------------------------------------------------------------------------
# 5. Grant the Graph app roles by creating appRoleAssignments on the app's service principal.
# This is a Microsoft Graph POST (no legacy Azure AD Graph, so it works in Cloud Shell). The
# Azure CLI's token carries Directory.AccessAsUser.All, so the call is authorized by the SIGNED-IN
# USER'S directory role — a Global Administrator (or Privileged Role Administrator) succeeds; a
# lesser role gets 403. We GET the existing assignments first and only POST what is missing, so
# re-runs never create duplicates.
# ---------------------------------------------------------------------------
Write-Step "Granting Microsoft Graph app roles to the app's service principal"
$assignUrl = "{0}/v1.0/servicePrincipals/{1}/appRoleAssignments" -f $GraphHost, $appSpId
$want = @($resolved.Values | Sort-Object -Unique)

# Read existing assignments so we only grant what's missing (idempotent; avoids duplicates).
$existing = Invoke-GraphRest -Method GET -Url $assignUrl
$existingRoleIds = @()
if ($existing -and -not ($existing.PSObject.Properties.Name -contains 'Failed') -and $existing.value) {
    $existingRoleIds = @($existing.value | ForEach-Object { $_.appRoleId } | Sort-Object -Unique)
}

foreach ($name in ($resolved.Keys | Sort-Object)) {
    $roleId = $resolved[$name]
    if ($existingRoleIds -contains $roleId) { Write-Info ("already granted: {0}" -f $name); continue }
    if ($PSCmdlet.ShouldProcess($name, "Grant Graph app role")) {
        $body = @{ principalId = $appSpId; resourceId = $graphSpId; appRoleId = $roleId }
        $res = Invoke-GraphRest -Method POST -Url $assignUrl -Body $body
        if ($res -and ($res.PSObject.Properties.Name -contains 'Failed')) {
            if ($res.Text -match 'already exists|Permission being assigned already exists') { Write-Info ("already granted: {0}" -f $name) }
            elseif ($res.Text -match 'Authorization_RequestDenied|Insufficient privileges|Forbidden') {
                throw ("Insufficient privileges to grant '{0}'. Creating Microsoft Graph app-role assignments requires the signed-in user to be an ACTIVE Global Administrator (or Privileged Role Administrator). Confirm your role is active (PIM: activate it first) and re-run.`n{1}" -f $name, $res.Text)
            }
            else { throw ("Failed to grant '{0}':`n{1}" -f $name, $res.Text) }
        }
        else { Write-Ok ("granted: {0}" -f $name) }
    }
}

# ---------------------------------------------------------------------------
# 6. Verify all roles are present (ride out directory replication lag).
# ---------------------------------------------------------------------------
Write-Step "Verifying all app roles are granted"
$have = @()
for ($i = 1; $i -le $MaxVerifyAttempts; $i++) {
    $cur = Invoke-GraphRest -Method GET -Url $assignUrl
    if ($cur -and -not ($cur.PSObject.Properties.Name -contains 'Failed') -and $cur.value) {
        $have = @($cur.value | ForEach-Object { $_.appRoleId } | Sort-Object -Unique)
    }
    $missing = @($want | Where-Object { $have -notcontains $_ })
    if ($missing.Count -eq 0) { break }
    Write-Info ("{0}/{1} roles present (attempt {2}/{3}); waiting {4}s" -f ($want.Count - $missing.Count), $want.Count, $i, $MaxVerifyAttempts, $VerifyDelaySeconds)
    Start-Sleep -Seconds $VerifyDelaySeconds
}
$missing = @($want | Where-Object { $have -notcontains $_ })
if ($missing.Count -gt 0) {
    Write-Warn ("Only {0}/{1} roles visible so far. Usually replication lag — re-run (idempotent) and re-check." -f ($want.Count - $missing.Count), $want.Count)
    if ($AcquireToken) { Write-Warn "Skipping token acquisition: a token minted now may not carry all roles yet." ; return }
}
else { Write-Ok ("All {0} app roles granted and verified." -f $want.Count) }

# ---------------------------------------------------------------------------
# 7. Summary + run-time recipe
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "App registration is ready." -ForegroundColor Green
Write-Host ("  appId       : {0}" -f $appId)
Write-Host ("  tenantId    : {0}" -f $TenantId)
Write-Host ("  certificate : {0}" -f $(if ($CertPath) { $CertPath } else { '(none created)' }))
Write-Host ("  template    : {0}   environment: {1}   Graph: {2}" -f $templateLabel, $Environment, $GraphHost)
Write-Host ""

if (-not $AcquireToken) {
    Write-Host "To mint a token and run the engine (all in Cloud Shell):" -ForegroundColor Cyan
    Write-Host @"
  az login --service-principal -u $appId --tenant $TenantId --certificate $CertPath --allow-no-subscriptions | Out-Null
  `$tokenText = az account get-access-token --resource $GraphHost --query accessToken -o tsv
  `$token = ConvertTo-SecureString `$tokenText -AsPlainText -Force; `$tokenText = `$null
  ./Apply-GraphTemplate.ps1 -TemplatePath $TemplatePath -Environment $Environment -AccessToken `$token -WhatIf

  # Or do setup + token in one step: `$token = ./Initialize-TcgDeployerApp.ps1 -TemplatePath $TemplatePath -AcquireToken
"@
    Write-Info "No client secret was created. The certificate PEM above is the app's only credential."
    Write-CleanupGuidance -AppId $appId -CertFile $CertPath
    return
}

# ---------------------------------------------------------------------------
# 8. -AcquireToken: sign in app-only with the cert and RETURN a SecureString Graph token.
# A just-registered certificate can lag replication to the token endpoint (AADSTS700027:
# "key was not found"), so retry the cert sign-in + token with backoff.
# ---------------------------------------------------------------------------
Write-Step "Acquiring a Microsoft Graph access token app-only (certificate sign-in)"
if (-not $CertPath -or -not (Test-Path $CertPath)) { throw "No certificate PEM available to sign in with. Re-run without -CertPath to create one, or pass -CertPath to an existing PEM." }

$tokenText = $null
$authMax = [Math]::Max(6, $MaxVerifyAttempts)
for ($i = 1; $i -le $authMax; $i++) {
    try {
        Invoke-Az -CliArgs @('login', '--service-principal', '-u', $appId, '--tenant', $TenantId, '--certificate', $CertPath, '--allow-no-subscriptions') | Out-Null
        $tokenText = Invoke-Az -Raw -CliArgs @('account', 'get-access-token', '--resource', $GraphHost, '--query', 'accessToken', '-o', 'tsv')
        $tokenText = ($tokenText | Out-String).Trim()
        if ($tokenText) { break }
        throw "Empty token returned."
    }
    catch {
        $m = $_.Exception.Message
        if ($m -match 'AADSTS700027|key was not found|not registered on application') {
            if ($i -lt $authMax) {
                Write-Info ("Certificate not yet visible at the token endpoint (replication lag); retry {0}/{1} in {2}s..." -f $i, $authMax, $VerifyDelaySeconds)
                Start-Sleep -Seconds $VerifyDelaySeconds
                continue
            }
            throw ("The certificate is still not recognized after {0} attempts (AADSTS700027). This is usually replication lag right after registering a new certificate — wait a minute and re-run with -AcquireToken -CertPath {1}.`n{2}" -f $authMax, $CertPath, $m)
        }
        throw
    }
}
Write-Warn "Azure CLI is now signed in AS THE SERVICE PRINCIPAL. Run 'az login' to return to your own account."
if (-not $tokenText) { throw "Failed to acquire an access token from the certificate sign-in." }
$secureToken = ConvertTo-SecureString $tokenText -AsPlainText -Force
$tokenText = $null
Write-Ok "Token acquired (SecureString). Pipe it into Apply-GraphTemplate.ps1 -AccessToken."
Write-Host ""
Write-Host "Example:" -ForegroundColor Cyan
Write-Host "  `$token = ./Initialize-TcgDeployerApp.ps1 -TemplatePath $TemplatePath -AcquireToken"
Write-Host "  ./Apply-GraphTemplate.ps1 -TemplatePath $TemplatePath -Environment $Environment -AccessToken `$token -WhatIf"
Write-Host ""
Write-Info "Tokens are short-lived (~60-90 min). Re-run with -AcquireToken -CertPath $CertPath to mint a fresh one."
Write-CleanupGuidance -AppId $appId -CertFile $CertPath

# The SecureString is the ONLY pipeline output.
$secureToken
