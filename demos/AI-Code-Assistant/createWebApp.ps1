# Variables
$resourceGroup = "ai-coding-demo"
$appServicePlan = "ai-demo-appservice-plan"
$webAppName = "aiassistantdemopythoncode" # must be globally unique
$location = "eastus2" # or your preferred region
$subscription = "ME-MngEnv857415-ddesonier-1"

# 1. Create a resource group (if needed)
az group create --name $resourceGroup --location $location

# Set your subscription (optional, if you have more than one)
az account set --subscription $subscription

# 2. Create an App Service plan (Linux)
az appservice plan create --name $appServicePlan --resource-group $resourceGroup --sku B1 --is-linux

# 3. Create the Web App with Python runtime
az webapp create `
  --resource-group $resourceGroup `
  --plan $appServicePlan `
  --name $webAppName `
  --runtime "PYTHON|3.9"

# az webapp create --% --resource-group "ai-coding-demo" --plan "ai-demo-appservice-plan" --name "aiassistantdemopythoncode" --runtime "PYTHON|3.9"

# 4. Deploy your code (from the current directory)
az webapp deploy --resource-group $resourceGroup --name $webAppName --src-path .

# 5. Set environment variables (repeat --settings for each variable)
az webapp config appsettings set `
  --resource-group $resourceGroup `
  --name $webAppName `
  --settings `
    AZURE_OPENAI_ENDPOINT="https://<your-openai-endpoint>" `
    AZURE_OPENAI_CHATGPT_DEPLOYMENT="gpt-4-deployment-name" `
    AZURE_OPENAI_KEY="your-openai-api-key" `
    AZURE_OPENAI_API_VERSION="2024-04-01-preview" `
    AZURE_STORAGE_ACCOUNT_NAME="yourstorageaccount" `
    CONTAINER_NAME="yourcontainername" `
    AZURE_AI_SEARCH_ENDPOINT="https://<your-search-endpoint>" `
    AZURE_AI_SEARCH_KEY="your-search-api-key" `
    AZURE_AI_SEARCH_INDEX="your-index-name" `
    AZURE_AI_SEARCH_INDEXER="your-indexer-name"

# 6. Browse to your app
az webapp browse --resource-group $resourceGroup --name $webAppName