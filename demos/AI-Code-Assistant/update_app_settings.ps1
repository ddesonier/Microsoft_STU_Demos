
# update_appsettings.ps1 -ResourceGroup "ai-coding-demo" -AppName "ai-assistant-new" -DotEnvPath ".env"

param(
    [Parameter(Mandatory=$true)]
    [string]$ResourceGroup,

    [Parameter(Mandatory=$true)]
    [string]$AppName,

    [string]$DotEnvPath = ".env"
)

# Read .env file and parse each line
Write-Host "Loading environment variables from $DotEnvPath..."
$lines = Get-Content $DotEnvPath
$appSettings = @()

foreach ($line in $lines) {
    # Skip empty or commented lines
    if ($line -and $line -notmatch '^#') {
        # Split once on '=' to allow values with '='
        $parts = $line -split '=', 2
        if ($parts.Count -eq 2) {
            $key = $parts[0].Trim()
            $value = $parts[1].Trim().Trim("'").Trim('"')
            $appSettings += "$key=$value"
        }
    }
}

if ($appSettings.Count -gt 0) {
    Write-Host "Updating Azure App Service settings..."
    az webapp config appsettings set `
        --resource-group $ResourceGroup `
        --name $AppName `
        --settings $appSettings
    Write-Host "App settings updated successfully!"
} else {
    Write-Host "No valid environment variables found to update."
}