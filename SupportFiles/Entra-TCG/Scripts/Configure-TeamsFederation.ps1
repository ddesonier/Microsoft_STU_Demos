#Requires -Version 7.0
<#
.SYNOPSIS
    Configures Microsoft Teams external-access federation for a community of
    tenants, and reports (optionally sets) meeting-join / lobby settings.

.DESCRIPTION
    This is a SEPARATE companion to Apply-GraphTemplate.ps1 (collaboration.json),
    run after that template is applied. Teams federation and
    meeting policy live in the MicrosoftTeams PowerShell module (Cs* cmdlets),
    NOT in Microsoft Graph, so they cannot be driven by the JSON/Graph engine.

    - Federation (A): allow-lists the supplied community DOMAINS so tenant users
      can chat/call community users. AllowFederatedUsers is enabled and the
      allowed-domains list is REPLACED with exactly the community domains
      (closed federation). Run it on every community tenant.

    - Meeting join (D): by default only REPORTS the relevant meeting-policy
      settings (authenticated join is already a consequence of your cross-tenant
      inboundTrust). Pass -SetLobbyBypassForFederated to also admit trusted
      federated users without the lobby.

.PARAMETER Domain
    One or more community domains to federate with (e.g. contoso.com fabrikam.com).

.PARAMETER SetLobbyBypassForFederated
    Opt-in. Sets the meeting policy AutoAdmittedUsers to
    'EveryoneInSameAndFederatedCompany' so trusted federated users skip the lobby.

.PARAMETER MeetingPolicyIdentity
    Which meeting policy to read/set. Default 'Global'.

.PARAMETER WhatIf
    Passed through to the Cs* cmdlets; shows changes without committing.

.EXAMPLE
    .\Configure-TeamsFederation.ps1 -Domain contoso.com,fabrikam.com -WhatIf

.EXAMPLE
    .\Configure-TeamsFederation.ps1 -Domain contoso.com -SetLobbyBypassForFederated
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [Parameter(Mandatory = $true)]
    [string[]] $Domain,

    [switch] $SetLobbyBypassForFederated,

    [string] $MeetingPolicyIdentity = 'Global'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Action {
    param(
        [ValidateSet('Set', 'Found', 'Report', 'Skipped', 'WhatIf', 'Error', 'Info')]
        [string] $Kind,
        [string] $Message
    )
    $color = switch ($Kind) {
        'Set'     { 'Green' }
        'Found'   { 'Cyan' }
        'Report'  { 'Blue' }
        'Skipped' { 'DarkGray' }
        'WhatIf'  { 'Magenta' }
        'Error'   { 'Red' }
        default   { 'Gray' }
    }
    $tag = ('[{0}]' -f $Kind).PadRight(9)
    Write-Host $tag -ForegroundColor $color -NoNewline
    Write-Host " $Message"
}

Write-Host ""
Write-Host "=== Teams Community Federation ===" -ForegroundColor White
Write-Host "Domains : $($Domain -join ', ')"
Write-Host "WhatIf  : $([bool]$WhatIfPreference)"
Write-Host ""

# ---- Module + connect ----
if (-not (Get-Module MicrosoftTeams -ListAvailable)) {
    throw "MicrosoftTeams module not found. Install-Module MicrosoftTeams -Scope CurrentUser"
}
Import-Module MicrosoftTeams -ErrorAction Stop
if (-not (Get-CsTenant -ErrorAction SilentlyContinue)) {
    Write-Action Info "Connecting to Microsoft Teams ..."
    Connect-MicrosoftTeams | Out-Null
}

# ---------------------------------------------------------------------------
# A. Federation (external access) - allow-list the community domains
# ---------------------------------------------------------------------------
Write-Action Info "Building allowed-domains list for federation ..."
$patterns = foreach ($d in $Domain) {
    $d = $d.Trim()
    if ($d) { New-CsEdgeDomainPattern -Domain $d }
}
$allowList = New-CsEdgeAllowList -AllowedDomain @($patterns)

# Check current federation state
$fed = $null
try {
    $fed = Get-CsTenantFederationConfiguration
    Write-Action Report "Current AllowFederatedUsers = $($fed.AllowFederatedUsers)"
} catch { Write-Action Info "Could not read current federation configuration." }

$currentDomains = if ($fed -and $fed.AllowedDomains -and $fed.AllowedDomains.AllowedDomain) {
    @($fed.AllowedDomains.AllowedDomain | ForEach-Object { $_.Domain } | Where-Object { $_ }) | Sort-Object -Unique
} else { @() }

$targetDomains = @($Domain | ForEach-Object { $_.Trim() } | Where-Object { $_ }) | Sort-Object -Unique

$isAlreadyConfigured = ($fed -ne $null) -and 
    ([bool]$fed.AllowFederatedUsers -eq $true) -and 
    (@($currentDomains).Count -eq @($targetDomains).Count) -and 
    (-not (Compare-Object @($currentDomains) @($targetDomains)))

if ($isAlreadyConfigured) {
    Write-Action Found "Federation already enabled with specified allowed domains ($($Domain -join ', '))."
} else {
    if ($PSCmdlet.ShouldProcess("TenantFederationConfiguration", "enable federation + replace allowed domains with community list")) {
        Set-CsTenantFederationConfiguration `
            -AllowFederatedUsers $true `
            -AllowedDomains $allowList `
            -ErrorAction Stop
        Write-Action Set "Federation enabled; allowed domains = $($Domain -join ', ') (closed allow-list)"
        Write-Action Info "Pausing 15 seconds for Teams backend federation settings to propagate..."
        Start-Sleep -Seconds 15
    } else {
        Write-Action WhatIf "Would enable federation and set allowed domains to: $($Domain -join ', ')"
    }
}

# ---------------------------------------------------------------------------
# D. Meeting join / lobby - report (and optionally set lobby bypass)
# ---------------------------------------------------------------------------
Write-Host ""
Write-Action Info "Meeting policy '$MeetingPolicyIdentity' - relevant join/lobby settings:"
$mp = Get-CsTeamsMeetingPolicy -Identity $MeetingPolicyIdentity
$fields = 'AutoAdmittedUsers', 'AllowAnonymousUsersToJoinMeeting',
          'AllowAnonymousUsersToStartMeeting', 'AllowPSTNUsersToBypassLobby',
          'AllowExternalNonTrustedMeetingChat'
foreach ($f in $fields) {
    if ($mp.PSObject.Properties.Name -contains $f) {
        Write-Action Report ("  {0} = {1}" -f $f, $mp.$f)
    }
}
Write-Host ""
Write-Action Info "Authenticated join for community members is already enabled by your"
Write-Action Info "cross-tenant inboundTrust (they join as themselves, MFA/device honored)."

if ($SetLobbyBypassForFederated) {
    if ($mp.AutoAdmittedUsers -eq 'EveryoneInSameAndFederatedCompany') {
        Write-Action Found "Lobby bypass already set to EveryoneInSameAndFederatedCompany."
    } else {
        if ($PSCmdlet.ShouldProcess("TeamsMeetingPolicy:$MeetingPolicyIdentity", "set AutoAdmittedUsers = EveryoneInSameAndFederatedCompany")) {
            $maxRetries = 5
            for ($attempt = 1; $attempt -le $maxRetries; $attempt++) {
                try {
                    Set-CsTeamsMeetingPolicy -Identity $MeetingPolicyIdentity `
                        -AutoAdmittedUsers 'EveryoneInSameAndFederatedCompany' -ErrorAction Stop
                    Write-Action Set "Lobby bypass: trusted same+federated company users auto-admitted."
                    break
                } catch {
                    if ($attempt -lt $maxRetries) {
                        Write-Action Info "Teams backend session propagation delay ($($_)); retrying in 5 seconds (attempt $attempt/$maxRetries)..."
                        Start-Sleep -Seconds 5
                    } else {
                        throw $_
                    }
                }
            }
        } else {
            Write-Action WhatIf "Would set AutoAdmittedUsers = EveryoneInSameAndFederatedCompany"
        }
    }
} else {
    Write-Action Skipped "Lobby unchanged (pass -SetLobbyBypassForFederated to admit federated users automatically)."
}

Write-Host ""
Write-Host "Done. Note: federation changes can take time to propagate across Teams." -ForegroundColor White
Write-Host ""
