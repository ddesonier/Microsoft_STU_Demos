#Requires -Version 7.0
<#
.SYNOPSIS
    Declaratively provisions Microsoft Graph resources from a JSON template, using
    Microsoft Graph PowerShell and Invoke-MgGraphRequest. The engine is domain-agnostic:
    the same script drives Microsoft Entra templates (e.g. collaboration.json,
    hardening.json) or any other resource exposed by Microsoft Graph.

.DESCRIPTION
    Imports a template describing Microsoft Graph resources (URI/Method/Body plus exists/
    create/configure/assert/roleScope blocks) and applies them idempotently:
    creates objects that do not exist, configures them, and reports every action
    (Created / Found / Applied / Skipped / WhatIf / Error).

    The engine carries no domain knowledge - every resource, URL, and payload lives in the
    template. Partners (for templates that declare a 'partners' forEach) can be supplied as
    domains or tenant IDs (single or array); domains are resolved to tenant IDs via the
    OpenID Connect metadata endpoint.

.PARAMETER TemplatePath
    Path to the JSON template to apply. Required. The file must exist and contain valid
    JSON; it is validated before any Graph connection is made.

.PARAMETER Domain
    One or more partner domains (e.g. contoso.com). Resolved to tenant IDs via OIDC.

.PARAMETER TenantId
    One or more partner tenant IDs (GUIDs). Used directly.

.PARAMETER Environment
    Graph/cloud environment. One of: Global, USGov, USGovDoD, Custom. Default Global.

.PARAMETER AccessToken
    A pre-acquired Microsoft Graph access token (SecureString) to authenticate with, instead of
    Connect-MgGraph's interactive/device-code sign-in. Use this wherever Graph PowerShell's own
    sign-in is unavailable or broken (e.g. Azure Cloud Shell in some clouds). Acquire the token
    however you like - an app registration with the template's Microsoft Graph *application*
    permissions (via Azure CLI, a certificate, or a managed identity) is the recommended source.
    The token's audience must match the selected -Environment's Graph endpoint. The script
    decodes the token's scopes/roles and validates them against the template before running.
    See ReadMEs/CloudShellDeployment.md for token-acquisition recipes.

.PARAMETER WhatIf
    Standard. Shows what would be created/modified without committing changes.

.EXAMPLE
    .\Apply-GraphTemplate.ps1 -TemplatePath .\collaboration.json -Domain fabrikam.com -WhatIf

.EXAMPLE
    .\Apply-GraphTemplate.ps1 -TemplatePath .\hardening.json -TenantId 1111...,2222... -Environment USGov

.EXAMPLE
    # Authenticate with a pre-acquired Graph token (e.g. from an app registration via Azure CLI):
    $tok = az account get-access-token --resource https://graph.microsoft.com --query accessToken -o tsv
    .\Apply-GraphTemplate.ps1 -TemplatePath ./hardening.json -AccessToken (ConvertTo-SecureString $tok -AsPlainText -Force)
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [Parameter(Mandatory = $true)]
    [string] $TemplatePath,

    [string[]] $Domain,

    [string[]] $TenantId,

    [ValidateSet('Global', 'USGov', 'USGovDoD', 'Custom')]
    [string] $Environment = 'Global',

    [string] $CustomGraphEndpoint,

    [string] $CustomLoginHost,

    [string] $CustomMgEnvironment = 'Global',

    [securestring] $AccessToken,

    [switch] $StopOnError
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Environment / endpoint map
# ---------------------------------------------------------------------------
$EnvMap = @{
    Global   = @{ GraphEndpoint = 'https://graph.microsoft.com';         Login = 'login.microsoftonline.com'; MgEnvironment = 'Global'   }
    USGov    = @{ GraphEndpoint = 'https://graph.microsoft.us';           Login = 'login.microsoftonline.us';  MgEnvironment = 'USGov'    }
    USGovDoD = @{ GraphEndpoint = 'https://dod-graph.microsoft.us';       Login = 'login.microsoftonline.us';  MgEnvironment = 'USGovDoD' }
}
if ($Environment -eq 'Custom') {
    if (-not $CustomGraphEndpoint -or -not $CustomLoginHost) {
        throw "-Environment Custom requires -CustomGraphEndpoint and -CustomLoginHost (e.g. an Azure Government/sovereign or AGC endpoint)."
    }
    $EnvMap['Custom'] = @{ GraphEndpoint = $CustomGraphEndpoint.TrimEnd('/'); Login = $CustomLoginHost; MgEnvironment = $CustomMgEnvironment }
}
$EnvCfg       = $EnvMap[$Environment]
$GraphEndpoint = $EnvCfg.GraphEndpoint
$LoginHost     = $EnvCfg.Login

# ---------------------------------------------------------------------------
# Console reporting helpers
# ---------------------------------------------------------------------------
$script:Summary = [ordered]@{ Created = 0; Found = 0; Applied = 0; Skipped = 0; WhatIf = 0; Asserted = 0; Warning = 0; Error = 0 }

function Write-Action {
    param(
        [ValidateSet('Created', 'Found', 'Applied', 'Skipped', 'WhatIf', 'Asserted', 'Warning', 'Error', 'Info')]
        [string] $Kind,
        [string] $Message
    )
    $color = switch ($Kind) {
        'Created'  { 'Green' }
        'Found'    { 'Cyan' }
        'Applied'  { 'Green' }
        'Skipped'  { 'DarkGray' }
        'WhatIf'   { 'Magenta' }
        'Asserted' { 'Blue' }
        'Warning'  { 'Yellow' }
        'Error'    { 'Red' }
        default    { 'Gray' }
    }
    if ($script:Summary.Contains($Kind)) { $script:Summary[$Kind]++ }
    $tag = ('[{0}]' -f $Kind).PadRight(11)
    Write-Host $tag -ForegroundColor $color -NoNewline
    Write-Host " $Message"
}

# ---------------------------------------------------------------------------
# OIDC tenant-ID resolution
# ---------------------------------------------------------------------------
function Resolve-TenantIdFromDomain {
    param([string] $DomainName)
    $url = "https://$LoginHost/$DomainName/v2.0/.well-known/openid-configuration"
    try {
        $meta = Invoke-RestMethod -Method GET -Uri $url -ErrorAction Stop
    } catch {
        throw "Could not resolve tenant ID for domain '$DomainName' via OIDC metadata ($url): $($_.Exception.Message)"
    }
    # issuer looks like https://login.microsoftonline.com/<tenantId>/v2.0
    if ($meta.issuer -match '([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})') {
        return $Matches[1]
    }
    throw "OIDC metadata for '$DomainName' did not contain a tenant GUID (issuer: $($meta.issuer))."
}

function Build-Partners {
    $list = [System.Collections.Generic.List[object]]::new()
    if ($Domain) {
        foreach ($d in $Domain) {
            $d = $d.Trim()
            if (-not $d) { continue }
            Write-Action Info "Resolving tenant ID for domain '$d' ..."
            $tid = Resolve-TenantIdFromDomain -DomainName $d
            $name = ($d -split '\.')[0]
            $name = $name.Substring(0,1).ToUpper() + $name.Substring(1)
            $list.Add([pscustomobject]@{ tenantId = $tid; displayName = $name; domain = $d })
            Write-Action Info "  '$d' -> $tid ($name)"
        }
    }
    if ($TenantId) {
        foreach ($t in $TenantId) {
            $t = $t.Trim()
            if (-not $t) { continue }
            if ($t -notmatch '^[0-9a-fA-F-]{36}$') { throw "TenantId '$t' is not a valid GUID." }
            $list.Add([pscustomobject]@{ tenantId = $t; displayName = "Tenant-$($t.Substring(0,8))"; domain = $null })
        }
    }
    return $list
}

# ---------------------------------------------------------------------------
# JMESPath-lite: resolve a small subset of match/capture expressions
#   supported: seg.seg2 , seg[0] , value[?prop] , value[?prop]|[0]
# ---------------------------------------------------------------------------
function Resolve-Path {
    param([object] $Obj, [string] $Expr)
    if ([string]::IsNullOrWhiteSpace($Expr)) { return $Obj }
    $current = $Obj
    # split on pipe first (only "|[0]" style supported)
    $pipeParts = $Expr -split '\|'
    foreach ($part in $pipeParts) {
        $part = $part.Trim()
        # tokenize dotted path with optional [..]
        foreach ($seg in ($part -split '\.')) {
            if ([string]::IsNullOrWhiteSpace($seg)) { continue }
            if ($null -eq $current) { return $null }
            # equality filter: name[?prop=='value']  (single-quoted literal)
            if ($seg -match "^([A-Za-z0-9_]+)\[\?([A-Za-z0-9_]+)\s*==\s*'(.*)'\]$") {
                $prop = $Matches[1]; $pred = $Matches[2]; $lit = $Matches[3]
                $coll = if ($prop) { $current.$prop } else { $current }
                $current = @($coll | Where-Object { $_.PSObject.Properties.Name -contains $pred -and $_.$pred -eq $lit })
                continue
            }
            # filter: name[?prop]  (truthy)
            if ($seg -match '^([A-Za-z0-9_]+)\[\?([A-Za-z0-9_]+)\]$') {
                $prop = $Matches[1]; $pred = $Matches[2]
                $coll = if ($prop) { $current.$prop } else { $current }
                $current = @($coll | Where-Object { $_.PSObject.Properties.Name -contains $pred -and $_.$pred })
                continue
            }
            # index: name[0]  or bare [0]
            if ($seg -match '^([A-Za-z0-9_]*)\[(\d+)\]$') {
                $prop = $Matches[1]; $idx = [int]$Matches[2]
                $coll = if ($prop) { $current.$prop } else { $current }
                $current = if ($null -ne $coll -and @($coll).Count -gt $idx) { @($coll)[$idx] } else { $null }
                continue
            }
            # plain property
            $current = $current.$seg
        }
    }
    return $current
}

# ---------------------------------------------------------------------------
# Token substitution: {{ ... }} against a context dictionary
# ---------------------------------------------------------------------------
function Resolve-Tokens {
    param(
        [object]    $InputObject,
        [hashtable] $Context,
        [switch]    $Tolerant  # in WhatIf, leave a marker instead of throwing
    )
    if ($InputObject -is [string]) {
        $result = [regex]::Replace($InputObject, '\{\{\s*([^}]+?)\s*\}\}', {
            param($m)
            $key = $m.Groups[1].Value.Trim()
            $val = Get-ContextValue -Context $Context -Key $key
            if ($null -eq $val) {
                if ($Tolerant) { return "<unresolved:$key>" }
                throw "Unresolved template token '{{$key}}'."
            }
            return [string]$val
        })
        return $result
    }
    elseif ($InputObject -is [System.Collections.IDictionary]) {
        $out = @{}
        foreach ($k in $InputObject.Keys) { $out[$k] = Resolve-Tokens -InputObject $InputObject[$k] -Context $Context -Tolerant:$Tolerant }
        return $out
    }
    elseif ($InputObject -is [System.Management.Automation.PSCustomObject]) {
        $out = @{}
        foreach ($p in $InputObject.PSObject.Properties) { $out[$p.Name] = Resolve-Tokens -InputObject $p.Value -Context $Context -Tolerant:$Tolerant }
        return $out
    }
    elseif ($InputObject -is [System.Collections.IEnumerable] -and $InputObject -isnot [string]) {
        $arr = @($InputObject | ForEach-Object { Resolve-Tokens -InputObject $_ -Context $Context -Tolerant:$Tolerant })
        return ,$arr
    }
    else { return $InputObject }
}

function Get-ContextValue {
    param([hashtable] $Context, [string] $Key)
    # dotted key: obj.id  /  partner.tenantId  /  roleScope.lookup.resourceId
    $parts = $Key -split '\.'
    $root  = $parts[0]
    if (-not $Context.ContainsKey($root)) { return $null }
    $cur = $Context[$root]
    for ($i = 1; $i -lt $parts.Count; $i++) {
        if ($null -eq $cur) { return $null }
        $seg = $parts[$i]
        if ($cur -is [System.Collections.IDictionary]) { $cur = $cur[$seg] }
        elseif ($cur.PSObject.Properties.Name -contains $seg) { $cur = $cur.$seg }
        else { return $null }
    }
    return $cur
}

# ---------------------------------------------------------------------------
# Graph call wrapper: returns @{ Ok; Status; Body } without throwing on
# expected (e.g. 404) statuses.
# ---------------------------------------------------------------------------
function Invoke-Graph {
    param(
        [string] $Method,
        [string] $Uri,
        [object] $Body,
        [int[]]  $ExpectedStatuses = @()
    )
    $params = @{ Method = $Method; Uri = $Uri; OutputType = 'PSObject' }
    if ($Body) { $params.Body = ($Body | ConvertTo-Json -Depth 40); $params.ContentType = 'application/json' }
    try {
        $resp = Invoke-MgGraphRequest @params
        return @{ Ok = $true; Status = 200; Body = $resp }
    } catch {
        $status = $null
        $ex = $_.Exception
        if ($ex.PSObject.Properties.Name -contains 'Response' -and $ex.Response) {
            try { $status = [int]$ex.Response.StatusCode } catch {}
        }
        $detail = "$($_.ErrorDetails.Message) $($ex.Message)"
        if ($null -eq $status -and $_.ErrorDetails.Message -match '"code"') { $status = 400 }
        if ($ExpectedStatuses -contains $status) {
            return @{ Ok = $false; Status = $status; Body = $null; Detail = $detail }
        }
        throw
    }
}

# Returns $true when an error looks like "already in desired state" / conflict,
# which several idempotent PUT/POST endpoints raise instead of succeeding.
function Test-ConflictError {
    param([string] $Message, [int] $Status)
    if ($Status -eq 409) { return $true }
    if ([string]::IsNullOrEmpty($Message)) { return $false }
    $signatures = @(
        'Request_MultipleObjectsWithSameKeyValue',
        'conflicting object',
        'already exists',
        'ObjectConflict',
        'A conflicting object'
    )
    foreach ($s in $signatures) { if ($Message -match [regex]::Escape($s)) { return $true } }
    return $false
}

# ---------------------------------------------------------------------------
# Per-resource processing
# ---------------------------------------------------------------------------
function Invoke-Resource {
    param([object] $Res, [hashtable] $BaseCtx)

    $ctx = @{}
    foreach ($k in $BaseCtx.Keys) { $ctx[$k] = $BaseCtx[$k] }

    $displayName = [string](Resolve-Tokens -InputObject $Res.name -Context $ctx -Tolerant)
    $mode = $Res.mode

    # ---- requires ---- skip this resource if a prerequisite context value is absent
    # (e.g. a sync step that needs {{spId}} when the sync app was intentionally skipped)
    if ($Res.PSObject.Properties.Name -contains 'requires') {
        foreach ($req in @($Res.requires)) {
            if (-not $ctx.ContainsKey($req) -or $null -eq $ctx[$req] -or [string]::IsNullOrEmpty([string]$ctx[$req])) {
                Write-Action Skipped "$displayName - skipped (prerequisite '$req' not available)"
                return $ctx
            }
        }
    }

    # ---- exists ---- (supports a single lookup object OR an ordered array of
    # lookups tried in sequence; first one that yields a match wins)
    $found = $null
    if ($Res.PSObject.Properties.Name -contains 'exists') {
        $lookups = @($Res.exists)
        foreach ($ex in $lookups) {
            $uri = [string](Resolve-Tokens -InputObject $ex.uri -Context $ctx -Tolerant:([bool]$WhatIfPreference))
            if ($uri -match '<unresolved:') {
                Write-Action WhatIf "$displayName - exists check skipped (depends on an object not yet created)"
                Write-Verbose "$displayName : skipped exists GET $uri"
                return $ctx
            }
            $expected = @()
            if ($ex.PSObject.Properties.Name -contains 'notFoundStatuses') { $expected = @($ex.notFoundStatuses) }
            else { $expected = @(404) }
            Write-Verbose "$displayName : $($ex.method) $uri"
            $r = Invoke-Graph -Method $ex.method -Uri $uri -ExpectedStatuses $expected
            $hit = $null
            if ($r.Ok) {
                if ($ex.PSObject.Properties.Name -contains 'match') {
                    $matchExpr = [string](Resolve-Tokens -InputObject $ex.match -Context $ctx -Tolerant)
                    $hit = Resolve-Path -Obj $r.Body -Expr $matchExpr
                }
                else { $hit = $r.Body }
            }
            if ($hit) {
                $found = $hit
                $ctx['exists'] = $found
                if ($ex.PSObject.Properties.Name -contains 'capture') {
                    foreach ($cp in $ex.capture.PSObject.Properties) { $ctx[$cp.Name] = Resolve-Path -Obj $r.Body -Expr $cp.Value }
                }
                break
            }
        }
    }

    # ---- assert mode ----
    if ($mode -eq 'assert') {
        if (-not $found) { Write-Action Skipped "$displayName - precondition clear (no restrictive policy found)"; return $ctx }
        $pred = $Res.assert.predicate
        $raw  = Resolve-Path -Obj $found -Expr $pred.path
        if (($pred.PSObject.Properties.Name -contains 'decode') -and $pred.decode -eq 'json' -and $raw) { $raw = ($raw | ConvertFrom-Json) }
        $sel = if ($raw) { Resolve-Path -Obj $raw -Expr $pred.select } else { $null }
        $isEmpty = ($null -eq $sel) -or (@($sel).Count -eq 0)
        if ($pred.mustBe -eq 'emptyOrAbsent' -and $isEmpty) {
            Write-Action Asserted "$displayName - OK (no invite allow-list)"
            return $ctx
        }
        # predicate failed - warn only (relaxing is commercial-cloud only and out of scope)
        $msgCtx = @{} + $ctx; $msgCtx['value'] = ($sel -join ', ')
        $msg = [string](Resolve-Tokens -InputObject $Res.assert.onFail.message -Context $msgCtx -Tolerant)
        Write-Action Warning $msg
        return $ctx
    }

    # ---- create (create-if-missing) ----
    $obj = $found
    if ($mode -eq 'create') {
        if ($found) {
            Write-Action Found "$displayName - already exists"
            if ($Res.create.PSObject.Properties.Name -contains 'capture') {
                # nothing to capture from create; try exists body already handled
            }
        } else {
            $cr = $Res.create
            # optional guard: detect pre-existing objects we must NOT duplicate or
            # touch (e.g. a manually-configured cross-tenant sync app). If the guard
            # GET returns a match, warn with remediation and skip creation.
            if ($cr.PSObject.Properties.Name -contains 'guard') {
                $g = $cr.guard
                $guri = [string](Resolve-Tokens -InputObject $g.uri -Context $ctx -Tolerant:([bool]$WhatIfPreference))
                if ($guri -notmatch '<unresolved:') {
                    Write-Verbose "$displayName : guard GET $guri"
                    $gr = Invoke-Graph -Method 'GET' -Uri $guri -ExpectedStatuses @(404)
                    $gmatch = $null
                    if ($gr.Ok) { $gmatch = if ($g.PSObject.Properties.Name -contains 'match') { Resolve-Path -Obj $gr.Body -Expr $g.match } else { $gr.Body } }
                    if ($gmatch) {
                        $gmsg = if ($g.PSObject.Properties.Name -contains 'message') { [string](Resolve-Tokens -InputObject $g.message -Context $ctx -Tolerant) } else { "$displayName - existing object(s) detected; skipping create to avoid duplicating or modifying unmanaged configuration." }
                        Write-Action Warning $gmsg
                        return $ctx
                    }
                }
            }
            $uri  = [string](Resolve-Tokens -InputObject $cr.uri -Context $ctx)
            $body = if ($cr.PSObject.Properties.Name -contains 'body') { Resolve-Tokens -InputObject $cr.body -Context $ctx -Tolerant:([bool]$WhatIfPreference) } else { $null }
            Write-Verbose "$displayName : $($cr.method) $uri"
            if ($PSCmdlet.ShouldProcess($displayName, "create")) {
                # optional retry-with-backoff for transient failures (e.g. directory
                # replication lag when a just-created object isn't visible yet)
                $retries = if ($cr.PSObject.Properties.Name -contains 'retryCount') { [int]$cr.retryCount } else { 0 }
                $delay   = if ($cr.PSObject.Properties.Name -contains 'retryDelaySeconds') { [int]$cr.retryDelaySeconds } else { 15 }
                $attempt = 0
                $r = $null
                while ($true) {
                    try {
                        $r = Invoke-Graph -Method $cr.method -Uri $uri -Body $body
                        break
                    } catch {
                        if ($attempt -ge $retries) { throw }
                        $attempt++
                        Write-Action Info "$displayName - attempt $attempt failed; waiting ${delay}s for directory replication (e.g. newly created groups)..."
                        Start-Sleep -Seconds $delay
                    }
                }
                $obj = $r.Body
                Write-Action Created "$displayName"
                if ($cr.PSObject.Properties.Name -contains 'capture') {
                    foreach ($cp in $cr.capture.PSObject.Properties) { $ctx[$cp.Name] = Resolve-Path -Obj $r.Body -Expr $cp.Value }
                }
            } else {
                Write-Action WhatIf "$displayName - would create"
            }
        }
        if ($obj) { $ctx['exists'] = $obj }
    }

    # store captureAs (found or created object) and register it for global promotion
    if ($Res.PSObject.Properties.Name -contains 'captureAs' -and $obj) {
        $ctx[$Res.captureAs] = $obj
        if ($script:DynamicCaptureKeys -notcontains $Res.captureAs) { $script:DynamicCaptureKeys += $Res.captureAs }
    }

    # ---- configure (idempotent PATCH/PUT) ----
    if ($Res.PSObject.Properties.Name -contains 'configure' -and $mode -ne 'assert') {
        Invoke-Mutation -Block $Res.configure -Ctx $ctx -Target $displayName -Verb 'Applied' -ActionText 'configure'
    }

    # ---- roleScope (lookup + POST) ----
    if ($Res.PSObject.Properties.Name -contains 'roleScope') {
        $rs = $Res.roleScope
        $lk = $rs.lookup
        $luri = [string](Resolve-Tokens -InputObject $lk.uri -Context $ctx -Tolerant:([bool]$WhatIfPreference))
        if ($WhatIfPreference) {
            Write-Action WhatIf "$displayName - would add resource role scope"
            Write-Verbose "$displayName : lookup GET $luri"
        } else {
            # poll until resource appears
            $resolved = $null
            for ($i = 0; $i -lt 12 -and -not $resolved; $i++) {
                $lr = Invoke-Graph -Method $lk.method -Uri $luri
                $resolved = Resolve-Path -Obj $lr.Body -Expr $lk.waitUntil
                if (-not $resolved) { Start-Sleep -Seconds 5 }
            }
            if (-not $resolved) { Write-Action Error "$displayName - resource role lookup timed out"; throw "roleScope lookup timeout for '$displayName'." }
            # rebuild nested roleScope.lookup context for token {{roleScope.lookup.<name>}}
            $lookupCap = @{}
            foreach ($cp in $lk.capture.PSObject.Properties) { $lookupCap[$cp.Name] = Resolve-Path -Obj $lr.Body -Expr $cp.Value }
            $ctx['roleScope'] = @{ lookup = $lookupCap }
            $uri  = [string](Resolve-Tokens -InputObject $rs.uri -Context $ctx)
            $body = Resolve-Tokens -InputObject $rs.body -Context $ctx
            Write-Verbose "$displayName : $($rs.method) $uri"
            if ($PSCmdlet.ShouldProcess($displayName, "add role scope")) {
                Invoke-Graph -Method $rs.method -Uri $uri -Body $body -ExpectedStatuses @(400) | Out-Null
                Write-Action Applied "$displayName - resource role scope added"
            }
        }
    }

    return $ctx
}

function Invoke-Mutation {
    param([object] $Block, [hashtable] $Ctx, [string] $Target, [string] $Verb, [string] $ActionText)
    $uri  = [string](Resolve-Tokens -InputObject $Block.uri -Context $Ctx -Tolerant:([bool]$WhatIfPreference))
    $body = if ($Block.PSObject.Properties.Name -contains 'body') { Resolve-Tokens -InputObject $Block.body -Context $Ctx -Tolerant:([bool]$WhatIfPreference) } else { $null }
    $skipOnConflict = ($Block.PSObject.Properties.Name -contains 'skipOnConflict') -and $Block.skipOnConflict
    Write-Verbose "$Target : $($Block.method) $uri"
    if ($PSCmdlet.ShouldProcess($Target, $ActionText)) {
        try {
            Invoke-Graph -Method $Block.method -Uri $uri -Body $body | Out-Null
            Write-Action $Verb "$Target - $ActionText"
        } catch {
            $status = 0; try { $status = [int]$_.Exception.Response.StatusCode } catch {}
            $detail = "$($_.ErrorDetails.Message) $($_.Exception.Message)"
            if ($skipOnConflict -and (Test-ConflictError -Message $detail -Status $status)) {
                Write-Action Skipped "$Target - already in desired state (conflict), no change needed"
            } else {
                throw
            }
        }
    } else {
        Write-Action WhatIf "$Target - would $ActionText"
    }
}

# ---------------------------------------------------------------------------
# Cross-resource capture stores
#   $script:Captured        : global singletons (group, catalog, accessPackage)
#   $script:PartnerCaptured : tenantId -> @{ spId; appRoleId; jobId }
# ---------------------------------------------------------------------------
$script:Captured        = @{}
$script:PartnerCaptured = @{}
$script:GlobalCaptureKeys  = @('group', 'catalog', 'accessPackage')
$script:DynamicCaptureKeys = @()   # any captureAs name declared by a resource
$script:PartnerCaptureKeys = @('spId', 'appRoleId', 'jobId')

function Merge-Captured {
    param([hashtable] $Ctx, [object] $Partner)
    foreach ($k in ($script:GlobalCaptureKeys + $script:DynamicCaptureKeys | Select-Object -Unique)) {
        if ($Ctx.ContainsKey($k) -and $Ctx[$k]) { $script:Captured[$k] = $Ctx[$k] }
    }
    if ($Partner) {
        $tid = $Partner.tenantId
        if (-not $script:PartnerCaptured.ContainsKey($tid)) { $script:PartnerCaptured[$tid] = @{} }
        foreach ($k in $script:PartnerCaptureKeys) {
            if ($Ctx.ContainsKey($k) -and $Ctx[$k]) { $script:PartnerCaptured[$tid][$k] = $Ctx[$k] }
        }
    }
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Entra Template Provisioner ===" -ForegroundColor White

if (-not (Test-Path $TemplatePath)) { throw "Template not found: $TemplatePath" }
try {
    $template = Get-Content $TemplatePath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
} catch {
    throw "Template '$TemplatePath' is not valid JSON: $($_.Exception.Message)"
}
if (-not ($template.PSObject.Properties.Name -contains 'resources') -or $null -eq $template.resources) {
    throw "Template '$TemplatePath' is missing a 'resources' array."
}

# Normalize graphScopes into a delegated set and an application (app-role) set. Accepts either
# the legacy flat array (treated as delegated only) or an object { delegated:[], application:[] }.
# Delegated mode requests the delegated set; an app-only access token is validated against the
# application set. This keeps app-only-only permissions (e.g. Directory.ReadWrite.All, which
# substitutes the delegated GroupSettings.ReadWrite.All) out of the delegated consent request.
$delegatedScopes  = @()
$applicationRoles = @()
if ($template.PSObject.Properties.Name -contains 'graphScopes' -and $null -ne $template.graphScopes) {
    $gs = $template.graphScopes
    if ($gs -is [System.Array] -or $gs -is [string]) {
        $delegatedScopes = @($gs)
    }
    else {
        if ($gs.PSObject.Properties.Name -contains 'delegated')   { $delegatedScopes  = @($gs.delegated) }
        if ($gs.PSObject.Properties.Name -contains 'application') { $applicationRoles = @($gs.application) }
    }
}

$tplName = if ($template.PSObject.Properties.Name -contains 'template') { $template.template } else { [IO.Path]::GetFileNameWithoutExtension($TemplatePath) }
$tplVer  = if ($template.PSObject.Properties.Name -contains 'version') { $template.version } else { 'n/a' }
$tplDate = if ($template.PSObject.Properties.Name -contains 'updated') { $template.updated } else { 'n/a' }
Write-Host "Template    : $tplName v$tplVer (updated $tplDate)"
Write-Host "File        : $TemplatePath"
Write-Host "Environment : $Environment ($GraphEndpoint)"
Write-Host "WhatIf      : $([bool]$WhatIfPreference)"
if ($template.PSObject.Properties.Name -contains 'description') { Write-Host "Description : $($template.description)" -ForegroundColor DarkGray }
Write-Host ""

# Build partner list from inputs
$partners = Build-Partners
Write-Host ""
Write-Action Info ("Partners: {0}" -f (($partners | ForEach-Object { "$($_.displayName)=$($_.tenantId)" }) -join '; '))

# Connect
if (-not (Get-Module Microsoft.Graph.Authentication -ListAvailable)) {
    throw "Microsoft.Graph.Authentication module not found. Install-Module Microsoft.Graph -Scope CurrentUser"
}
Import-Module Microsoft.Graph.Authentication -ErrorAction Stop

# Decode the payload (claims) of a JWT without validation. Used to read the granted
# delegated scopes ('scp') or application roles ('roles') from an access token.
function Get-JwtClaims {
    param([string] $Jwt)
    try {
        $payload = $Jwt.Split('.')[1]
        switch ($payload.Length % 4) { 2 { $payload += '==' } 3 { $payload += '=' } }
        $json = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($payload.Replace('-', '+').Replace('_', '/')))
        return ($json | ConvertFrom-Json)
    } catch { return $null }
}

# True when a required delegated scope is covered by the granted set: an exact match, a
# ReadWrite.* scope covering the matching Read.* requirement, or a broad Directory.* scope.
function Test-ScopeSatisfied {
    param([string] $Required, [string[]] $Granted)
    if ($Granted -contains $Required) { return $true }
    # write implies read for the same resource (e.g. Group.ReadWrite.All covers Group.Read.All)
    if ($Required -match '\.Read(\.[A-Za-z]+)?$') {
        $rw = ($Required -replace '\.Read(\.|$)', '.ReadWrite$1')
        if ($Granted -contains $rw) { return $true }
    }
    # broad Directory scopes cover the common group/application scopes
    $implied = @{
        'Directory.ReadWrite.All' = @('Group.ReadWrite.All', 'Group.Read.All', 'Application.ReadWrite.All', 'Application.Read.All', 'Directory.Read.All')
        'Directory.Read.All'      = @('Group.Read.All', 'Application.Read.All')
    }
    foreach ($g in $Granted) {
        if ($implied.ContainsKey($g) -and ($implied[$g] -contains $Required)) { return $true }
    }
    return $false
}

# In WhatIf, request read-only scopes only (no write consent needed to diff state)
$scopesToRequest = @($delegatedScopes)
if ($WhatIfPreference) {
    $readMap = @{
        'Policy.ReadWrite.Authorization'       = 'Policy.Read.All'
        'Policy.ReadWrite.B2BManagementPolicy' = 'Policy.Read.All'
        'Policy.ReadWrite.CrossTenantAccess'   = 'Policy.Read.All'
        'Group.ReadWrite.All'                  = 'Group.Read.All'
        'EntitlementManagement.ReadWrite.All'  = 'EntitlementManagement.Read.All'
        'Application.ReadWrite.All'            = 'Application.Read.All'
        'Synchronization.ReadWrite.All'        = 'Synchronization.Read.All'
        'AuditLog.Read.All'                    = 'AuditLog.Read.All'
    }
    $scopesToRequest = @($delegatedScopes | ForEach-Object { if ($readMap.ContainsKey($_)) { $readMap[$_] } else { $_ } } | Select-Object -Unique)
    Write-Action Info "WhatIf mode: requesting READ-ONLY scopes ($($scopesToRequest -join ', '))"
}

if ($AccessToken) {
    # Convert the SecureString to plaintext transiently to decode the token's scopes/roles.
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($AccessToken)
    try { $plainToken = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }

    $claims = Get-JwtClaims -Jwt $plainToken
    $granted = @()
    if ($claims) {
        if ($claims.PSObject.Properties.Name -contains 'roles' -and $claims.roles) { $granted += @($claims.roles) }
        if ($claims.PSObject.Properties.Name -contains 'scp'   -and $claims.scp)   { $granted += @([string]$claims.scp -split '\s+') }
    }
    $granted = @($granted | Where-Object { $_ } | Select-Object -Unique)
    $isAppOnly = [bool]($claims -and ($claims.PSObject.Properties.Name -contains 'roles') -and $claims.roles)
    $plainToken = $null

    Write-Action Info "Authenticating with the supplied access token ($($EnvCfg.MgEnvironment))."
    # Graph SDK v2 takes a SecureString for -AccessToken; v1 takes a plain string.
    $authMod = Get-Module Microsoft.Graph.Authentication -ListAvailable | Sort-Object Version -Descending | Select-Object -First 1
    if ($authMod -and $authMod.Version.Major -ge 2) {
        Connect-MgGraph -AccessToken $AccessToken -Environment $EnvCfg.MgEnvironment -NoWelcome
    } else {
        $bstr2 = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($AccessToken)
        try { Connect-MgGraph -AccessToken ([System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr2)) -Environment $EnvCfg.MgEnvironment -NoWelcome }
        finally { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr2) }
    }
    $grantedCtx = Get-MgContext

    # A decodable token that carries no Graph scopes AND no app roles is unusable (wrong
    # audience, expired, or an app with no Graph permissions / missing admin consent).
    if (@($granted).Count -eq 0) {
        throw @(
            "The supplied access token carries no Microsoft Graph scopes or application roles.",
            "Likely causes: wrong audience (must be this environment's Graph endpoint, $GraphEndpoint),",
            "an expired token, or an app registration with no Microsoft Graph permissions / missing admin consent.",
            "See ReadMEs/CloudShellDeployment.md for how to acquire a suitable token."
        ) -join [Environment]::NewLine
    }

    Write-Action Info ("Token principal: {0}; Graph {1}: {2}" -f `
        ($(if ($isAppOnly) { 'application (app-only)' } else { 'delegated (user)' })), `
        ($(if ($isAppOnly) { 'roles' } else { 'scopes' })), ($granted -join ', '))

    # Validate against the set that matches the token type: an app-only token is checked against
    # the template's application (app-role) set; a delegated token against the delegated set.
    if ($isAppOnly) {
        $requiredForToken = if ($applicationRoles.Count) { $applicationRoles } else { $delegatedScopes }
    } else {
        $requiredForToken = $delegatedScopes
    }
    $unsatisfied = @($requiredForToken | Where-Object { -not (Test-ScopeSatisfied -Required $_ -Granted $granted) })
    if ($unsatisfied.Count) {
        $kind = if ($isAppOnly) { 'application role' } else { 'delegated scope' }
        if ($isAppOnly -and -not $applicationRoles.Count) {
            Write-Action Info "NOTE: template declares no application role set; comparing app-only token against delegated names. Some app roles differ (e.g. GroupSettings.ReadWrite.All -> Directory.ReadWrite.All). Could not pre-verify: $($unsatisfied -join ', '). Proceeding; genuinely missing permissions surface as per-resource errors."
        } else {
            Write-Action Info "WARNING: the token is missing Microsoft Graph $kind(s) required by this template: $($unsatisfied -join ', '). Acquire a token with these permissions (see ReadMEs/CloudShellDeployment.md) or expect per-resource errors."
        }
    }
} else {
    Write-Action Info "Connecting to Graph ($($EnvCfg.MgEnvironment)) with $($scopesToRequest.Count) scopes ..."
    Connect-MgGraph -Environment $EnvCfg.MgEnvironment -Scopes $scopesToRequest -NoWelcome
    $grantedCtx = Get-MgContext
    $missing = @($scopesToRequest | Where-Object { -not (Test-ScopeSatisfied -Required $_ -Granted @($grantedCtx.Scopes)) })
    if ($missing.Count) { Write-Action Info "WARNING: not all scopes appear consented. Missing: $($missing -join ', ')" }
}

# Base context (global)
$globalCtx = @{ 'graph-endpoint' = $GraphEndpoint }

$total = $template.resources.Count
$idx = 0
foreach ($res in $template.resources) {
    $idx++
    $pct = [int](($idx / $total) * 100)
    $label = [string](Resolve-Tokens -InputObject $res.name -Context (@{ 'graph-endpoint'=$GraphEndpoint } + (@{ partner = ($partners | Select-Object -First 1) })) -Tolerant)
    Write-Progress -Activity "Applying template ($idx/$total)" -Status $label -PercentComplete $pct

    $isForEach = $res.PSObject.Properties.Name -contains 'forEach'
    try {
        if ($isForEach) {
            $srcName = $res.forEach.source   # "partners"
            $varName = $res.forEach.var      # "partner"
            $items = if ($srcName -eq 'partners') { $partners } else { @() }
            if (-not $items -or @($items).Count -eq 0) {
                Write-Action Skipped "$($res.name) - no items in '$srcName'"
                continue
            }
            foreach ($item in $items) {
                $ctx = @{} + $globalCtx
                $ctx[$varName] = $item
                # merge global singleton captures + this partner's captures
                foreach ($k in $script:Captured.Keys) { $ctx[$k] = $script:Captured[$k] }
                if ($script:PartnerCaptured.ContainsKey($item.tenantId)) {
                    foreach ($k in $script:PartnerCaptured[$item.tenantId].Keys) { $ctx[$k] = $script:PartnerCaptured[$item.tenantId][$k] }
                }
                $result = Invoke-Resource -Res $res -BaseCtx $ctx
                Merge-Captured -Ctx $result -Partner $item
            }
        } else {
            $ctx = @{} + $globalCtx
            foreach ($k in $script:Captured.Keys) { $ctx[$k] = $script:Captured[$k] }
            $result = Invoke-Resource -Res $res -BaseCtx $ctx
            Merge-Captured -Ctx $result
        }
    } catch {
        Write-Action Error "$($res.name): $($_.Exception.Message)"
        if ($StopOnError) { Write-Progress -Activity "Applying template" -Completed; throw }
        Write-Action Info "$($res.name) - continuing to next resource (use -StopOnError to halt)."
    }
}
Write-Progress -Activity "Applying template" -Completed

Write-Host ""
Write-Host "=== Summary ===" -ForegroundColor White
foreach ($k in $script:Summary.Keys) { Write-Host ("  {0,-9}: {1}" -f $k, $script:Summary[$k]) }
Write-Host ""
if ($script:Summary['Error'] -gt 0) {
    Write-Host "Completed with $($script:Summary['Error']) error(s)." -ForegroundColor Yellow
    $global:LASTEXITCODE = 1
}
