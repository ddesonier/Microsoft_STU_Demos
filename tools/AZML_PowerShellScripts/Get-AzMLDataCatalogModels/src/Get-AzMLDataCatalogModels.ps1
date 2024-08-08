<#
.SYNOPSIS
    Retrieves model information from an Azure Machine Learning registry based on specified parameters.
.DESCRIPTION
    The Get-ModelsInfoByModelName function queries an Azure Machine Learning registry to retrieve details about models.
    It takes three mandatory parameters:
    - $ResourceGroup: The Azure resource group where the registry is located.
    - $Registry: The name of the Azure Machine Learning registry.
    - $ModelArray: An array containing model names.

    The function performs the following steps:
    1. Retrieves a list of models from the specified registry.
    2. For each model:
        - If a "latest version" exists, retrieves detailed information using 'az ml model show'.
        - If no version information is available, adds the model to the investigation list.
    3. Writes the model information to a CSV file named "models_<timestamp>.csv".
    4. Writes the investigation list to a CSV file named "investigate_<timestamp>.csv".

.PARAMETER ResourceGroup
    Specifies the Azure resource group where the registry is located.
.PARAMETER Registry
    Specifies the name of the Azure Machine Learning registry.
.PARAMETER ModelArray
    An array containing model names to query.
.PARAMETER FilePath
    Path where files will be written.

.OUTPUTS
    The function generates two CSV files:
    - "models_<timestamp>.csv": Contains detailed model information.
    - "investigate_<timestamp>.csv": Lists models without version information.

.PREREQUISITES
    Before using the Get-ModelsInfoByModelName function, ensure the following prerequisites are met:

    1. Azure CLI: Make sure you have the Azure Command-Line Interface (CLI) installed on your system.
       You can download it from the official Azure CLI website: https://docs.microsoft.com/en-us/cli/azure/install-azure-cli

    2. Azure Subscription: You need an active Azure subscription to work with Azure resources.
       If you don't have one, sign up for a free Azure account: https://azure.com/free

    3. Resource Group and Registry:
       - Create an Azure resource group where your Azure Machine Learning registry will reside.
       - Set up an Azure Machine Learning registry within your resource group.
       - Note the names of the resource group and registry for use as parameters in the function.

    4. Model Names:
       - Prepare an array of model names that you want to query using the function.
       - Ensure that these model names exist in your Azure Machine Learning registry.

    5. Permissions:
       - Ensure that you have appropriate permissions to access the specified resource group and registry.
       - You should be able to execute Azure CLI commands and retrieve model information.

.EXAMPLE
    Get-ModelsInfoByModelName -ResourceGroup "myResourceGroup" -Registry "myRegistry" -ModelArray @("Model1", "Model2")

    Retrieves model details for the specified models in the given registry.

    Registries azure-openai, azureml, azureml-meta, azureml-mistral. azureml-msr, nvidia-ai, HuggingFace, azureml-restricted, azureml-cohere
#>



function Get-ModelsInfoByModelName {
    [CmdletBinding()]
    param (
        [Parameter(Mandatory=$true)]
        [ValidateNotNullOrEmpty()]
        [string]$ResourceGroup,

        [Parameter(Mandatory=$true)]
        [ValidateNotNullOrEmpty()]
        [string]$Registry,

        [Parameter(Mandatory=$true)]
        [ValidateNotNullOrEmpty()]
        [Array]$ModelArray,

        [Parameter(Mandatory=$true)]
        [ValidateNotNullOrEmpty()]
        [string]$MDLFilePath,

        [Parameter(Mandatory=$true)]
        [ValidateNotNullOrEmpty()]
        [string]$INVFIlePath
    )

    $AllModels = @()    #all models for given registry
    $ModelInfo = @()    #az ml model show output
    $investigate = @()  #Models without Version numbers
    $progress = 1

    $mdlsReg = az ml model list --resource-group $ResourceGroup --registry-name $Registry | ConvertFrom-Json
    foreach ($model in $mdlsReg) {
        Write-Output ("Model " + $progress++ + " of " + $ModelArray.count)
        Write-Output ("Registry: " + $Registry)
        Write-Output ("Model Name: " + $model.name)
        if ($model."latest version") {
            Write-Output ("Version: " + $model."latest version")
            $ModelInfo = az ml model show --name $model.name --version $model."latest version" --resource-group $ResourceGroup --registry-name $Registry | ConvertFrom-Json
        } else {
            $investigate += $model
            Write-Output ("No Version for Model Named: " + $model.name)
            Write-Output ("Model in Registry: " + $Registry)
        }
        $AllModels += $ModelInfo

    }
    $AllModels | Export-Csv $MDLFilePath -Append -NoTypeInformation -Encoding UTF8
    $investigate | Export-Csv $INVFIlePath -Append -NoTypeInformation -Encoding UTF8
}


<#
.SYNOPSIS
    Retrieves a list of models from an Azure Machine Learning registry.
.DESCRIPTION
    The Get-ModelsByRegistry function allows you to query an Azure Machine Learning registry and retrieve information about the available models.
    It takes two mandatory parameters: $ResourceGroup (representing the resource group) and $Registry (representing the registry name).
    Upon execution, the function uses the Azure CLI command 'az ml model list', converts the result from JSON format, and returns the list of models.
.PARAMETER ResourceGroup
    Specifies the Azure resource group where the registry is located.
.PARAMETER Registry
    Specifies the name of the Azure Machine Learning registry.
.EXAMPLE
    Get-ModelsByRegistry -ResourceGroup "myResourceGroup" -Registry "myRegistry"
    Retrieves the list of models from the specified registry.
#>
function Get-ModelsByRegistry {
    [CmdletBinding()]
    param (
        [Parameter(Mandatory=$true)]
        [ValidateNotNullOrEmpty()]
        [string]$ResourceGroup,

        [Parameter(Mandatory=$true)]
        [ValidateNotNullOrEmpty()]
        [string]$Registry
    )

    $mdlsReg = az ml model list --resource-group $ResourceGroup --registry-name $Registry | ConvertFrom-Json
    return $mdlsReg
}

# Usage example
#$registries= @('azureml')
$registries= @('azure-openai','azureml','azureml-meta','azureml-mistral','azureml-msr','nvidia-ai', 'azureml-restricted','azureml-cohere')
$cnt = 0
$rg = 'Rg-amltest'
$mdlfilename = "C:\tmp\" + "models" + $(Get-Date -Format "MMddyyyy_HHmm") + ".csv"
$invfilename = "C:\tmp\" + "investigate" + $(Get-Date -Format "MMddyyyy_HHmm") + ".csv"
foreach ($registry in $registries)
{
    $mmd = Get-ModelsByRegistry -ResourceGroup $rg -Registry $registry
    Write-Output ($registry + "  Count = " + $mmd.count)
    $cnt += $mmd.count
    ModelsInfoByModelName -ResourceGroup $rg -Registry $registry -ModelArray $mmd -MDLFilePath $mdlfilename -INVFIlePath $invfilename
}
Write-Output ("Model Count For these registries: " + $cnt)