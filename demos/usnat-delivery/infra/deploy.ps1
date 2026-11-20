[CmdletBinding()]
param(
    [string]$ResourceGroup = "rg-msdocs-knowledge-search",
    [Parameter(Mandatory)][string]$Location,
    [Parameter(Mandatory)][string]$SearchLocation,
    [Parameter(Mandatory)][string]$AiServicesLocation,
    [Parameter(Mandatory)][string]$SearchEndpointSuffix,
    [Parameter(Mandatory)][string]$AiServicesEndpointSuffix,
    [Parameter(Mandatory)][string]$SearchAudience,
    [Parameter(Mandatory)][string]$StorageAudience,
    [Parameter(Mandatory)][string]$CognitiveServicesTokenScope,
    [Parameter(Mandatory)][string]$ContentSourceOrigin,
    [Parameter(Mandatory)][string]$SearchSkuName,
    [Parameter(Mandatory)][string]$AppServicePlanSku,
    [Parameter(Mandatory)][string]$EmbeddingDeploymentSku,
    [Parameter(Mandatory)][int]$EmbeddingDeploymentCapacity,
    [Parameter(Mandatory)][string]$ChatDeploymentSku,
    [Parameter(Mandatory)][int]$ChatDeploymentCapacity,
    [Parameter(Mandatory)][string]$ChatModelName,
    [Parameter(Mandatory)][string]$ChatModelVersion,
    [Parameter(Mandatory)][string]$McpApiVersion,
    [Parameter(Mandatory)][string]$AoaiApiVersion,
    [ValidatePattern("^[a-z0-9-]{3,18}$")]
    [string]$NamePrefix = "msdocskbv2",
    [string]$EntraAppDisplayName = "msdocs-search-app-admin",
    [string]$EmbeddingDeploymentName = "text-embedding-3-large",
    [string]$EmbeddingModelName = "text-embedding-3-large",
    [string]$EmbeddingModelVersion = "1",
    [string]$ChatDeploymentName = "gpt-chat",
    [string]$StorageSkuName = "Standard_LRS",
    [string]$SitemapNameFilter = "azure_en-us",
    [string]$EntraAuthorityHost = "",
    [string]$MicrosoftGraphEndpoint = "",
    [string]$ResourceManagerEndpoint = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$templateFile = Join-Path $PSScriptRoot "main.bicep"

function Invoke-AzJson {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $json = & az @Arguments --only-show-errors --output json
    if ($LASTEXITCODE -ne 0) {
        throw "Azure CLI command failed."
    }
    if ([string]::IsNullOrWhiteSpace(($json -join ""))) {
        return $null
    }
    return (($json -join "`n") | ConvertFrom-Json)
}

function Invoke-Az {
    param([Parameter(Mandatory)][string[]]$Arguments)

    & az @Arguments --only-show-errors --output none
    if ($LASTEXITCODE -ne 0) {
        throw "Azure CLI command failed."
    }
}

function New-RandomSecret {
    param([int]$ByteCount = 48)

    $bytes = New-Object byte[] $ByteCount
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    return [Convert]::ToBase64String($bytes)
}

function New-DeploymentArchive {
    param(
        [Parameter(Mandatory)][string]$SourceDirectory,
        [Parameter(Mandatory)][string]$DestinationPath
    )

    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $sourceRoot = (Get-Item -LiteralPath $SourceDirectory).FullName.TrimEnd("\")
    $archive = [System.IO.Compression.ZipFile]::Open(
        $DestinationPath,
        [System.IO.Compression.ZipArchiveMode]::Create
    )
    try {
        Get-ChildItem -LiteralPath $sourceRoot -Recurse -File | ForEach-Object {
            $entryName = $_.FullName.Substring($sourceRoot.Length).TrimStart("\").Replace("\", "/")
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $archive,
                $_.FullName,
                $entryName,
                [System.IO.Compression.CompressionLevel]::Optimal
            ) | Out-Null
        }
    }
    finally {
        $archive.Dispose()
    }
}

function Wait-ForHealth {
    param(
        [Parameter(Mandatory)][string]$Url,
        [int]$Attempts = 60,
        [int]$DelaySeconds = 10
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 20
            if ($response.StatusCode -eq 200) {
                return
            }
        }
        catch {
            if ($attempt -eq $Attempts) {
                throw "Health check failed after $Attempts attempts: $Url"
            }
        }
        Start-Sleep -Seconds $DelaySeconds
    }
}

function Assert-HttpsUrl {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Value
    )

    $uri = $null
    if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -ne "https") {
        throw "$Name must be an absolute HTTPS URL."
    }
}

$cloud = Invoke-AzJson @("cloud", "show")
if ([string]::IsNullOrWhiteSpace($EntraAuthorityHost)) {
    $EntraAuthorityHost = $cloud.endpoints.activeDirectory
}
if ([string]::IsNullOrWhiteSpace($MicrosoftGraphEndpoint)) {
    $MicrosoftGraphEndpoint = $cloud.endpoints.microsoftGraphResourceId
}
if ([string]::IsNullOrWhiteSpace($ResourceManagerEndpoint)) {
    $ResourceManagerEndpoint = $cloud.endpoints.resourceManager
}
foreach ($requiredEndpoint in @{
    EntraAuthorityHost = $EntraAuthorityHost
    MicrosoftGraphEndpoint = $MicrosoftGraphEndpoint
    ResourceManagerEndpoint = $ResourceManagerEndpoint
}.GetEnumerator()) {
    if ([string]::IsNullOrWhiteSpace($requiredEndpoint.Value)) {
        throw "The active Azure cloud profile does not expose $($requiredEndpoint.Key); pass it explicitly."
    }
}

$EntraAuthorityHost = $EntraAuthorityHost.TrimEnd("/")
$MicrosoftGraphEndpoint = $MicrosoftGraphEndpoint.TrimEnd("/")
$ResourceManagerEndpoint = $ResourceManagerEndpoint.TrimEnd("/")
$ContentSourceOrigin = $ContentSourceOrigin.TrimEnd("/")
$SearchEndpointSuffix = $SearchEndpointSuffix.TrimStart(".")
$AiServicesEndpointSuffix = $AiServicesEndpointSuffix.TrimStart(".")
$SearchAudience = $SearchAudience.TrimEnd("/")
$StorageAudience = $StorageAudience.TrimEnd("/")
$SearchTokenScope = "$SearchAudience/.default"
if ($SearchEndpointSuffix -notmatch "^[A-Za-z0-9.-]+$") {
    throw "SearchEndpointSuffix must be a DNS suffix without a scheme or path."
}
if ($AiServicesEndpointSuffix -notmatch "^[A-Za-z0-9.-]+$") {
    throw "AiServicesEndpointSuffix must be a DNS suffix without a scheme or path."
}
Assert-HttpsUrl "EntraAuthorityHost" $EntraAuthorityHost
Assert-HttpsUrl "MicrosoftGraphEndpoint" $MicrosoftGraphEndpoint
Assert-HttpsUrl "ResourceManagerEndpoint" $ResourceManagerEndpoint
Assert-HttpsUrl "SearchAudience" $SearchAudience
Assert-HttpsUrl "StorageAudience" $StorageAudience
Assert-HttpsUrl "CognitiveServicesTokenScope" $CognitiveServicesTokenScope
Assert-HttpsUrl "ContentSourceOrigin" $ContentSourceOrigin
$contentSourceUri = [Uri]$ContentSourceOrigin
if ($contentSourceUri.Scheme -ne "https" -or [string]::IsNullOrWhiteSpace($contentSourceUri.Host)) {
    throw "ContentSourceOrigin must be an absolute HTTPS origin."
}
$contentSourceHost = $contentSourceUri.Host
$sitemapIndex = "$ContentSourceOrigin/_sitemaps/sitemapindex.xml"
$allowedPrefixes = "$ContentSourceOrigin/en-us/azure/"

$deploymentParameters = @(
    "namePrefix=$NamePrefix",
    "appLocation=$Location",
    "searchLocation=$SearchLocation",
    "aiServicesLocation=$AiServicesLocation",
    "searchEndpointSuffix=$SearchEndpointSuffix",
    "aiServicesEndpointSuffix=$AiServicesEndpointSuffix",
    "searchSkuName=$SearchSkuName",
    "appServicePlanSku=$AppServicePlanSku",
    "storageSkuName=$StorageSkuName",
    "embeddingDeploymentName=$EmbeddingDeploymentName",
    "embeddingModelName=$EmbeddingModelName",
    "embeddingModelVersion=$EmbeddingModelVersion",
    "embeddingDeploymentSku=$EmbeddingDeploymentSku",
    "embeddingDeploymentCapacity=$EmbeddingDeploymentCapacity",
    "chatDeploymentName=$ChatDeploymentName",
    "chatModelName=$ChatModelName",
    "chatModelVersion=$ChatModelVersion",
    "chatDeploymentSku=$ChatDeploymentSku",
    "chatDeploymentCapacity=$ChatDeploymentCapacity",
    "mcpApiVersion=$McpApiVersion"
)

$resourceGroupExists = & az group exists --name $ResourceGroup
if ($LASTEXITCODE -ne 0) {
    throw "Failed to check whether resource group '$ResourceGroup' exists."
}
if ($resourceGroupExists -ne "true") {
    Write-Host "Creating resource group $ResourceGroup..."
    Invoke-Az @("group", "create", "--name", $ResourceGroup, "--location", $Location)
}
else {
    Write-Host "Using existing resource group $ResourceGroup..."
}

Write-Host "Running full-stack quota, capacity, model-access, and name preflight..."
$requiredProviders = @(
    "Microsoft.Authorization",
    "Microsoft.CognitiveServices",
    "Microsoft.Network",
    "Microsoft.Search",
    "Microsoft.Storage",
    "Microsoft.Web"
)
foreach ($provider in $requiredProviders) {
    $state = & az provider show --namespace $provider --query registrationState -o tsv
    if ($LASTEXITCODE -ne 0 -or $state -ne "Registered") {
        throw "Required resource provider '$provider' is not registered."
    }
}
$validateArguments = @(
    "deployment", "group", "validate",
    "--resource-group", $ResourceGroup,
    "--template-file", $templateFile,
    "--parameters"
) + $deploymentParameters
Invoke-Az $validateArguments

Write-Host "Deploying isolated Azure infrastructure..."
$deploymentArguments = @(
    "deployment", "group", "create",
    "--name", "msdocs-knowledge-stack",
    "--resource-group", $ResourceGroup,
    "--template-file", $templateFile,
    "--parameters"
) + $deploymentParameters
$deployment = Invoke-AzJson $deploymentArguments

$outputs = $deployment.properties.outputs
$adminAppName = $outputs.adminAppName.value
$adminAppUrl = $outputs.adminAppUrl.value
$knowledgeAppName = $outputs.knowledgeAppName.value
$knowledgeAppUrl = $outputs.knowledgeAppUrl.value

Write-Host "Linking the Blob private endpoint to private DNS..."
$dnsZoneGroupBody = @{
    properties = @{
        privateDnsZoneConfigs = @(
            @{
                name = "blob"
                properties = @{
                    privateDnsZoneId = $outputs.blobPrivateDnsZoneId.value
                }
            }
        )
    }
} | ConvertTo-Json -Depth 6 -Compress
$dnsZoneGroupBody = $dnsZoneGroupBody.Replace('"', '\"')
$dnsZoneGroupArgs = @(
    "rest",
    "--method", "PUT",
    "--uri", "$ResourceManagerEndpoint$($outputs.blobPrivateEndpointId.value)/privateDnsZoneGroups/default?api-version=2024-05-01",
    "--headers", "Content-Type=application/json",
    "--body", $dnsZoneGroupBody
)
$dnsZoneGroupLinked = $false
for ($attempt = 1; $attempt -le 12; $attempt++) {
    & az @dnsZoneGroupArgs --only-show-errors --output none
    if ($LASTEXITCODE -eq 0) {
        $dnsZoneGroupLinked = $true
        break
    }
    if ($attempt -lt 12) {
        Start-Sleep -Seconds 10
    }
}
if (-not $dnsZoneGroupLinked) {
    throw "Azure did not accept the Blob private DNS zone group after 12 attempts."
}

Write-Host "Configuring Entra authentication for $adminAppName..."
$entraApps = Invoke-AzJson @("ad", "app", "list", "--display-name", $EntraAppDisplayName)
$entraApp = @($entraApps | Where-Object { $_.displayName -eq $EntraAppDisplayName })
if ($entraApp.Count -ne 1) {
    throw "Expected exactly one Entra app named '$EntraAppDisplayName'; found $($entraApp.Count)."
}
$entraApp = $entraApp[0]

$servicePrincipal = Invoke-AzJson @("ad", "sp", "show", "--id", $entraApp.appId)
Invoke-Az @(
    "rest", "--method", "PATCH",
    "--uri", "$MicrosoftGraphEndpoint/v1.0/servicePrincipals/$($servicePrincipal.id)",
    "--headers", "Content-Type=application/json",
    "--body", '{"appRoleAssignmentRequired":true}'
)

$adminCallback = "$adminAppUrl/admin/auth/callback"
$redirectUris = @($entraApp.web.redirectUris) + $adminCallback
$redirectUris = @($redirectUris | Where-Object { $_ } | Sort-Object -Unique)
& az ad app update --id $entraApp.id --web-redirect-uris $redirectUris --only-show-errors --output none
if ($LASTEXITCODE -ne 0) {
    throw "Failed to update Entra redirect URIs."
}

$existingSettings = Invoke-AzJson @(
    "webapp", "config", "appsettings", "list",
    "--resource-group", $ResourceGroup,
    "--name", $adminAppName
)
$entraSecretSetting = @($existingSettings | Where-Object { $_.name -eq "ENTRA_CLIENT_SECRET" })
$flaskSecretSetting = @($existingSettings | Where-Object { $_.name -eq "FLASK_SECRET_KEY" })
$entraSecret = if ($entraSecretSetting.Count -gt 0) { $entraSecretSetting[0].value } else { $null }
$flaskSecret = if ($flaskSecretSetting.Count -gt 0) { $flaskSecretSetting[0].value } else { $null }

if ([string]::IsNullOrWhiteSpace($entraSecret)) {
    $credential = Invoke-AzJson @(
        "ad", "app", "credential", "reset",
        "--id", $entraApp.id,
        "--append",
        "--display-name", "$adminAppName deployment",
        "--years", "1"
    )
    $entraSecret = $credential.password
}
if ([string]::IsNullOrWhiteSpace($flaskSecret)) {
    $flaskSecret = New-RandomSecret
}

Invoke-Az @(
    "webapp", "config", "appsettings", "set",
    "--resource-group", $ResourceGroup,
    "--name", $adminAppName,
    "--settings",
    "ENTRA_TENANT_ID=$($servicePrincipal.appOwnerOrganizationId)",
    "ENTRA_CLIENT_ID=$($entraApp.appId)",
    "ENTRA_CLIENT_SECRET=$entraSecret",
    "ENTRA_AUTHORITY_HOST=$EntraAuthorityHost",
    "AZURE_AUTHORITY_HOST=$EntraAuthorityHost",
    "FLASK_SECRET_KEY=$flaskSecret",
    "SCM_DO_BUILD_DURING_DEPLOYMENT=false",
    "ENABLE_ORYX_BUILD=false",
    "WEBSITES_CONTAINER_START_TIME_LIMIT=600",
    "SEARCH_ENDPOINT=$($outputs.searchEndpoint.value)",
    "SEARCH_INDEX_NAME=$($outputs.searchIndexName.value)",
    "SEMANTIC_CONFIG_NAME=$($outputs.semanticConfigurationName.value)",
    "KNOWLEDGE_SOURCE_NAME=$($outputs.knowledgeSourceName.value)",
    "KNOWLEDGE_BASE_NAME=$($outputs.knowledgeBaseName.value)",
    "MCP_API_VERSION=$($outputs.mcpApiVersion.value)",
    "SEARCH_AUDIENCE=$SearchAudience",
    "SEARCH_TOKEN_SCOPE=$SearchTokenScope",
    "BLOB_ACCOUNT_URL=$($outputs.blobAccountUrl.value)",
    "BLOB_CONTAINER_NAME=$($outputs.blobContainerName.value)",
    "STORAGE_AUDIENCE=$StorageAudience",
    "BLOB_READ_CONCURRENCY=12",
    "CONTENT_SOURCE_ORIGIN=$ContentSourceOrigin",
    "CONTENT_SOURCE_HOST=$contentSourceHost",
    "SITEMAP_INDEX=$sitemapIndex",
    "SITEMAP_NAME_FILTER=$SitemapNameFilter",
    "ALLOWED_PREFIXES=$allowedPrefixes",
    "AOAI_ENDPOINT=$($outputs.aiServicesEndpoint.value)",
    "AOAI_EMBEDDING_DEPLOYMENT=$($outputs.embeddingDeploymentName.value)",
    "EMBEDDING_DIMENSIONS=1024",
    "CHUNK_TARGET_CHARS=3500",
    "CHUNK_OVERLAP_CHARS=400",
    "AOAI_CHAT_DEPLOYMENT=$($outputs.chatDeploymentName.value)",
    "AOAI_API_VERSION=$AoaiApiVersion",
    "COGNITIVE_SERVICES_TOKEN_SCOPE=$CognitiveServicesTokenScope",
    "ANSWER_MAX_TOKENS=6000"
)

Invoke-Az @(
    "webapp", "config", "appsettings", "set",
    "--resource-group", $ResourceGroup,
    "--name", $knowledgeAppName,
    "--settings",
    "SCM_DO_BUILD_DURING_DEPLOYMENT=false",
    "ENABLE_ORYX_BUILD=false",
    "WEBSITES_CONTAINER_START_TIME_LIMIT=600",
    "AZURE_AUTHORITY_HOST=$EntraAuthorityHost",
    "SEARCH_ENDPOINT=$($outputs.searchEndpoint.value)",
    "SEARCH_INDEX_NAME=$($outputs.searchIndexName.value)",
    "SEMANTIC_CONFIG_NAME=$($outputs.semanticConfigurationName.value)",
    "KNOWLEDGE_SOURCE_NAME=$($outputs.knowledgeSourceName.value)",
    "KNOWLEDGE_BASE_NAME=$($outputs.knowledgeBaseName.value)",
    "MCP_API_VERSION=$($outputs.mcpApiVersion.value)",
    "SEARCH_AUDIENCE=$SearchAudience",
    "SEARCH_TOKEN_SCOPE=$SearchTokenScope",
    "AOAI_ENDPOINT=$($outputs.aiServicesEndpoint.value)",
    "AOAI_CHAT_DEPLOYMENT=$($outputs.chatDeploymentName.value)",
    "AOAI_API_VERSION=$AoaiApiVersion",
    "COGNITIVE_SERVICES_TOKEN_SCOPE=$CognitiveServicesTokenScope",
    "ANSWER_MAX_TOKENS=6000"
)

Write-Host "Building deployment package..."
$stagingRoot = Join-Path ([System.IO.Path]::GetTempPath()) "msdocs-search-$([Guid]::NewGuid().ToString('N'))"
$zipPath = "$stagingRoot.zip"
try {
    New-Item -ItemType Directory -Path $stagingRoot | Out-Null
    Copy-Item (Join-Path $repoRoot "requirements.txt") $stagingRoot
    foreach ($optionalRequirementsFile in @("requirements.in.txt", "requirements.lock.txt")) {
        $optionalRequirementsPath = Join-Path $repoRoot $optionalRequirementsFile
        if (Test-Path -LiteralPath $optionalRequirementsPath) {
            Copy-Item $optionalRequirementsPath $stagingRoot
        }
    }

    $wheelhouseSource = Join-Path $repoRoot "wheelhouse"
    $dependencyWheels = @(
        Get-ChildItem -LiteralPath $wheelhouseSource -Filter "*.whl" -File -ErrorAction SilentlyContinue
    )
    if ($dependencyWheels.Count -gt 0) {
        $wheelhouseDestination = Join-Path $stagingRoot "wheelhouse"
        New-Item -ItemType Directory -Path $wheelhouseDestination | Out-Null
        $dependencyWheels | Copy-Item -Destination $wheelhouseDestination

        $sitePackages = Join-Path $stagingRoot ".python_packages\lib\site-packages"
        $wheelPaths = @($dependencyWheels | ForEach-Object { $_.FullName })
        & python -m pip install `
            --no-deps `
            --target $sitePackages `
            --platform manylinux2014_x86_64 `
            --python-version 3.13 `
            --implementation cp `
            --only-binary=:all: `
            --quiet `
            @wheelPaths
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to vendor Linux Python dependencies into the deployment package."
        }
    }

    $appSource = Join-Path $repoRoot "app"
    Get-ChildItem -LiteralPath $appSource -Recurse -File |
        Where-Object {
            $_.FullName -notmatch "[\\/]out[\\/]" -and
            $_.FullName -notmatch "[\\/]__pycache__[\\/]" -and
            $_.Extension -ne ".pyc"
        } |
        ForEach-Object {
            $relativePath = $_.FullName.Substring($repoRoot.Length).TrimStart("\")
            $destination = Join-Path $stagingRoot $relativePath
            $destinationDirectory = Split-Path -Parent $destination
            if (-not (Test-Path $destinationDirectory)) {
                New-Item -ItemType Directory -Path $destinationDirectory -Force | Out-Null
            }
            Copy-Item $_.FullName $destination
        }

    New-DeploymentArchive -SourceDirectory $stagingRoot -DestinationPath $zipPath

    Write-Host "Deploying Admin/Crawler application..."
    Invoke-Az @(
        "webapp", "deploy",
        "--resource-group", $ResourceGroup,
        "--name", $adminAppName,
        "--src-path", $zipPath,
        "--type", "zip",
        "--clean", "true",
        "--restart", "true",
        "--track-status", "true",
        "--timeout", "1200000"
    )

    Write-Host "Deploying Knowledge Base application..."
    Invoke-Az @(
        "webapp", "deploy",
        "--resource-group", $ResourceGroup,
        "--name", $knowledgeAppName,
        "--src-path", $zipPath,
        "--type", "zip",
        "--clean", "true",
        "--restart", "true",
        "--track-status", "true",
        "--timeout", "1200000"
    )
}
finally {
    Remove-Item -LiteralPath $stagingRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
}

Write-Host "Waiting for Search provisioning and application health..."
Wait-ForHealth -Url "$adminAppUrl/healthz"
Wait-ForHealth -Url "$knowledgeAppUrl/healthz"

[PSCustomObject]@{
    ResourceGroup = $ResourceGroup
    AdminApp = $adminAppUrl
    KnowledgeApp = $knowledgeAppUrl
    SearchEndpoint = $outputs.searchEndpoint.value
    SearchIndex = $outputs.searchIndexName.value
    KnowledgeBase = $outputs.knowledgeBaseName.value
    StorageAccount = $outputs.storageAccountName.value
    AiServices = $outputs.aiServicesName.value
}
