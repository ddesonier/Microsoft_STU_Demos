#Requires -Version 7.0
<#
.SYNOPSIS
    Starts cross-tenant synchronization provisioning jobs for community partners,
    once every partner tenant has applied Apply-GraphTemplate.ps1 with collaboration.json.

.DESCRIPTION
    The final step of cross-tenant sync (validate credentials -> start job) cannot
    run inside Apply-GraphTemplate.ps1 (collaboration.json) because it has a CROSS-TENANT precondition:
    validateCredentials only succeeds after the PARTNER (target) tenant has set
    userSyncInbound.isSyncAllowed = true for you - i.e. they've run the script too.

    This script is safe to run repeatedly as community tenants come online. For
    each partner it:
      1. Finds the "CTS - <Partner>" service principal + its sync job (from Apply).
      2. Calls validateCredentials against the target tenant.
      3. If validation succeeds (partner is ready), starts the job.
      4. If validation fails (partner not ready yet), reports and SKIPS - not an error.

.PARAMETER Domain
    One or more partner domains. Resolved to tenant IDs via OIDC (same as Apply).

.PARAMETER TenantId
    One or more partner tenant IDs (GUIDs).

.PARAMETER Environment
    Graph/cloud environment: Global, USGov, USGovDoD, Custom. Default Global.

.PARAMETER CustomGraphEndpoint
    Required when -Environment Custom. Graph base URL for a sovereign/AGC cloud.

.PARAMETER CustomLoginHost
    Required when -Environment Custom. Login host for a sovereign/AGC cloud.

.PARAMETER CustomMgEnvironment
    Microsoft.Graph environment name to connect with when -Environment Custom. Default Global.

.PARAMETER WhatIf
    Shows what would validate/start without committing.

.EXAMPLE
    .\Start-CommunitySync.ps1 -Domain fabrikam.com -WhatIf

.EXAMPLE
    .\Start-CommunitySync.ps1 -TenantId 1111...,2222...
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [string[]] $Domain,
    [string[]] $TenantId,

    [ValidateSet('Global', 'USGov', 'USGovDoD', 'Custom')]
    [string] $Environment = 'Global',

    [string] $CustomGraphEndpoint,

    [string] $CustomLoginHost,

    [string] $CustomMgEnvironment = 'Global'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$EnvMap = @{
    Global   = @{ GraphEndpoint = 'https://graph.microsoft.com';            Login = 'login.microsoftonline.com'; MgEnvironment = 'Global'   }
    USGov    = @{ GraphEndpoint = 'https://graph.microsoft.us';              Login = 'login.microsoftonline.us';  MgEnvironment = 'USGov'    }
    USGovDoD = @{ GraphEndpoint = 'https://dod-graph.microsoft.us';          Login = 'login.microsoftonline.us';  MgEnvironment = 'USGovDoD' }
}
if ($Environment -eq 'Custom') {
    if (-not $CustomGraphEndpoint -or -not $CustomLoginHost) {
        throw "-Environment Custom requires -CustomGraphEndpoint and -CustomLoginHost (e.g. an Azure Government/sovereign or AGC endpoint)."
    }
    $EnvMap['Custom'] = @{ GraphEndpoint = $CustomGraphEndpoint.TrimEnd('/'); Login = $CustomLoginHost; MgEnvironment = $CustomMgEnvironment }
}
$EnvCfg        = $EnvMap[$Environment]
$GraphEndpoint = $EnvCfg.GraphEndpoint
$LoginHost     = $EnvCfg.Login

$script:Summary = [ordered]@{ Started = 0; AlreadyRunning = 0; NotReady = 0; WhatIf = 0; Error = 0 }

function Write-Action {
    param(
        [ValidateSet('Started', 'AlreadyRunning', 'NotReady', 'WhatIf', 'Error', 'Info')]
        [string] $Kind,
        [string] $Message
    )
    $color = switch ($Kind) {
        'Started'        { 'Green' }
        'AlreadyRunning' { 'Cyan' }
        'NotReady'       { 'Yellow' }
        'WhatIf'         { 'Magenta' }
        'Error'          { 'Red' }
        default          { 'Gray' }
    }
    if ($script:Summary.Contains($Kind)) { $script:Summary[$Kind]++ }
    $tag = ('[{0}]' -f $Kind).PadRight(16)
    Write-Host $tag -ForegroundColor $color -NoNewline
    Write-Host " $Message"
}

function Resolve-TenantIdFromDomain {
    param([string] $DomainName)
    $url = "https://$LoginHost/$DomainName/v2.0/.well-known/openid-configuration"
    $meta = Invoke-RestMethod -Method GET -Uri $url -ErrorAction Stop
    if ($meta.issuer -match '([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})') { return $Matches[1] }
    throw "OIDC metadata for '$DomainName' did not contain a tenant GUID."
}

function Build-Partners {
    $list = [System.Collections.Generic.List[object]]::new()
    if ($Domain) {
        foreach ($d in $Domain) {
            $d = $d.Trim(); if (-not $d) { continue }
            $tid = Resolve-TenantIdFromDomain -DomainName $d
            $name = ($d -split '\.')[0]; $name = $name.Substring(0,1).ToUpper() + $name.Substring(1)
            $list.Add([pscustomobject]@{ tenantId = $tid; displayName = $name })
        }
    }
    if ($TenantId) {
        foreach ($t in $TenantId) {
            $t = $t.Trim(); if (-not $t) { continue }
            if ($t -notmatch '^[0-9a-fA-F-]{36}$') { throw "TenantId '$t' is not a valid GUID." }
            $list.Add([pscustomobject]@{ tenantId = $t; displayName = "Tenant-$($t.Substring(0,8))" })
        }
    }
    if ($list.Count -eq 0) { throw "Provide at least one -Domain or -TenantId." }
    return $list
}

Write-Host ""
Write-Host "=== Start Community Cross-Tenant Sync ===" -ForegroundColor White
Write-Host "Environment : $Environment ($GraphEndpoint)"
Write-Host "WhatIf      : $([bool]$WhatIfPreference)"
Write-Host ""

Import-Module Microsoft.Graph.Authentication -ErrorAction Stop
$scopes = @('Application.Read.All', 'Synchronization.ReadWrite.All', 'Directory.Read.All')
Write-Action Info "Connecting to Graph ($($EnvCfg.MgEnvironment)) ..."
Connect-MgGraph -Environment $EnvCfg.MgEnvironment -Scopes $scopes -NoWelcome

$partners = Build-Partners

foreach ($p in $partners) {
    $label = "$($p.displayName) ($($p.tenantId))"
    try {
        # 1. Locate the CTS service principal created by Apply-GraphTemplate.ps1 (collaboration.json)
        $spFilter = "displayName eq 'CTS - $($p.displayName)'"
        $spResp = Invoke-MgGraphRequest -Method GET -OutputType PSObject `
            -Uri "$GraphEndpoint/v1.0/servicePrincipals?`$filter=$spFilter"
        $sp = if ($spResp.value) { @($spResp.value)[0] } else { $null }
        if (-not $sp) { Write-Action Error "$label - no 'CTS - $($p.displayName)' service principal found (apply collaboration.json with Apply-GraphTemplate.ps1 first)."; continue }
        $spId = $sp.id

        # 2. Locate the sync job
        $jobResp = Invoke-MgGraphRequest -Method GET -OutputType PSObject `
            -Uri "$GraphEndpoint/v1.0/servicePrincipals/$spId/synchronization/jobs"
        $job = if ($jobResp.value) { @($jobResp.value)[0] } else { $null }
        if (-not $job) { Write-Action Error "$label - no synchronization job on the CTS app."; continue }
        $jobId = $job.id

        # Is it already running?
        $statusCode = $null
        try { $statusCode = $job.status.code } catch { $statusCode = $null }
        if ($statusCode -in @('Active', 'InProgress')) {
            Write-Action AlreadyRunning "$label - job already active."
            continue
        }

        # 3. Validate credentials (fails until partner allowed inbound sync)
        $valBody = @{
            useSavedCredentials = $false
            templateId          = 'Azure2Azure'
            credentials         = @(
                @{ key = 'CompanyId';          value = $p.tenantId },
                @{ key = 'AuthenticationType'; value = 'SyncPolicy' }
            )
        }
        $ready = $false
        try {
            Invoke-MgGraphRequest -Method POST `
                -Uri "$GraphEndpoint/v1.0/servicePrincipals/$spId/synchronization/jobs/validateCredentials" `
                -Body ($valBody | ConvertTo-Json -Depth 10) -ContentType 'application/json' | Out-Null
            $ready = $true
        } catch {
            $ready = $false
        }
        if (-not $ready) {
            Write-Action NotReady "$label - partner has not enabled inbound sync yet; skipping. Re-run later."
            continue
        }

        # 4. Start the job
        if ($PSCmdlet.ShouldProcess($label, "start synchronization job")) {
            Invoke-MgGraphRequest -Method POST `
                -Uri "$GraphEndpoint/v1.0/servicePrincipals/$spId/synchronization/jobs/$jobId/start" | Out-Null
            Write-Action Started "$label - provisioning job started."
        } else {
            Write-Action WhatIf "$label - would start provisioning job (partner is ready)."
        }
    } catch {
        Write-Action Error "$label - $($_.Exception.Message)"
    }
}

Write-Host ""
Write-Host "=== Summary ===" -ForegroundColor White
foreach ($k in $script:Summary.Keys) { Write-Host ("  {0,-15}: {1}" -f $k, $script:Summary[$k]) }
Write-Host ""
if ($script:Summary['NotReady'] -gt 0) {
    Write-Host "Some partners were not ready. Re-run this script after they onboard." -ForegroundColor Yellow
}
