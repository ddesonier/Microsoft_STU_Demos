# Get-AzMLDataCatalogModels

This repository contains a PowerShell function called `Get-AzMLDataCatalogModels` that provides convenient functions for interacting with Azure Machine Learning (AML) registries. Whether you're managing models, querying registry information, or investigating model versions, these functions simplify the process.

## Table of Contents
- [Introduction](#introduction)
- [Functions](#functions)
  - [Get-ModelsInfoByModelName](#get-modelsinfobymodelname)
  - [Get-ModelsByRegistry](#get-modelsbyregistry)
- [Prerequisites](#prerequisites)
- [Usage](#usage)
- [Contributing](#contributing)
- [License](#license)

## Introduction
Azure Machine Learning provides a powerful platform for managing and deploying machine learning models. This function aims to streamline common tasks related to AML registries, making it easier for data scientists and developers to work with models.

## Functions

### Get-ModelsInfoByModelName
- Retrieves detailed information about models based on specified parameters.
- Steps:
  - Retrieves a list of models from the specified AML registry.
  - For each model:
    - If a "latest version" exists, retrieves detailed information using the `az ml model show` command.
    - If no version information is available, adds the model to the investigation list.
  - Writes the model information to a CSV file named `models_<timestamp>.csv`.
  - Writes the investigation list to a CSV file named `investigate_<timestamp>.csv`.
- Parameters:
  - `$ResourceGroup`: Azure resource group where the registry is located.
  - `$Registry`: Name of the AML registry.
  - `$ModelArray`: Array containing model names to query.
  - `$FilePath`: Path where files will be written.

### Get-ModelsByRegistry
- Retrieves a list of models from an AML registry.
- Parameters:
  - `$ResourceGroup`: Azure resource group where the registry is located.
  - `$Registry`: Name of the AML registry.

## Prerequisites
Before using this function, ensure the following prerequisites are met:
1. **Install Azure CLI**: Download it from the official Azure CLI website: [Install Azure CLI](https://docs.microsoft.com/en-us/cli/azure/install-azure-cli)
2. **Active Azure Subscription**: Sign up for a free Azure account if needed: [Azure Free Account](https://azure.com/free)
3. **Set Up AML Registry or Workspace**: Create an Azure Machine Learning registry or Workspace within your resource group.
4. **Prepare Model Names**: Have an array of model names that exist in your AML registry.

## Usage
1. Clone this repository or download the `Get-AzMLDataCatalogModels.ps1` file.
2. Use the functions as described in the introduction.

## Contributing
Contributions are welcome! If you find any issues or want to enhance these functions, feel free to submit pull requests.

---

Feel free to adapt this README to match your project's specifics. Happy coding! 🚀
