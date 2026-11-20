targetScope = 'resourceGroup'

@description('Azure region for App Service, networking, and private Blob storage.')
param appLocation string

@description('Azure region for Azure AI Search.')
param searchLocation string

@description('Azure region for Azure AI Services and the embedding deployment.')
param aiServicesLocation string

@description('DNS suffix for Search endpoints in the target cloud, without a leading dot.')
param searchEndpointSuffix string

@description('DNS suffix for Azure AI Services endpoints in the target cloud, without a leading dot.')
param aiServicesEndpointSuffix string

@description('Short workload prefix used in resource names.')
@minLength(3)
@maxLength(18)
param namePrefix string = 'msdocskb'

@description('Azure OpenAI embedding model deployment name.')
param embeddingDeploymentName string = 'text-embedding-3-large'

@description('Azure OpenAI embedding model name.')
param embeddingModelName string = 'text-embedding-3-large'

@description('Azure OpenAI embedding model version.')
param embeddingModelVersion string = '1'

@description('Embedding deployment SKU available in the target cloud.')
param embeddingDeploymentSku string

@description('Embedding deployment capacity.')
param embeddingDeploymentCapacity int

@description('Azure OpenAI chat model deployment name.')
param chatDeploymentName string = 'gpt-5-mini'

@description('Azure OpenAI chat model name.')
param chatModelName string = 'gpt-5-mini'

@description('Azure OpenAI chat model version.')
param chatModelVersion string

@description('Chat deployment SKU available in the target cloud.')
param chatDeploymentSku string

@description('Chat deployment capacity.')
param chatDeploymentCapacity int

@description('Azure AI Search index name.')
param searchIndexName string = 'msdocs-content-v2'

@description('Azure AI Search knowledge source name.')
param knowledgeSourceName string = 'msdocs-content-v2-ks'

@description('Azure AI Search knowledge base name.')
param knowledgeBaseName string = 'msdocs-content-v2-kb'

@description('App Service plan SKU.')
param appServicePlanSku string

@description('Azure AI Search SKU available in the target cloud.')
param searchSkuName string

@description('Storage account SKU available in the target cloud.')
param storageSkuName string = 'Standard_LRS'

@description('MCP API version supported in the target cloud.')
param mcpApiVersion string

var uniqueSuffix = take(uniqueString(subscription().subscriptionId, resourceGroup().id), 7)
var searchServiceName = '${namePrefix}-search-${uniqueSuffix}'
var aiServicesName = '${namePrefix}-ai-${uniqueSuffix}'
var appServicePlanName = '${namePrefix}-plan-${uniqueSuffix}'
var adminAppName = '${namePrefix}-admin-${uniqueSuffix}'
var knowledgeAppName = '${namePrefix}-knowledge-${uniqueSuffix}'
var vnetName = '${namePrefix}-vnet-${uniqueSuffix}'
var blobPrivateEndpointName = '${namePrefix}-blob-pe-${uniqueSuffix}'
var blobPrivateDnsZoneName = 'privatelink.blob.${environment().suffixes.storage}'
var blobContainerName = 'crawled-pages'
var semanticConfigurationName = 'default-semantic-config'

var storageBlobDataContributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
)
var searchServiceContributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
)
var searchIndexDataContributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
)
var searchIndexDataReaderRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '1407120a-92aa-4202-b7e9-c0e197c71c8f'
)
var openAiUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
)

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: vnetName
  location: appLocation
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.20.0.0/16'
      ]
    }
    subnets: [
      {
        name: 'snet-appservice-integration'
        properties: {
          addressPrefix: '10.20.1.0/24'
          delegations: [
            {
              name: 'app-service-delegation'
              properties: {
                serviceName: 'Microsoft.Web/serverFarms'
              }
            }
          ]
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: 'snet-private-endpoints'
        properties: {
          addressPrefix: '10.20.2.0/24'
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

resource appIntegrationSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: vnet
  name: 'snet-appservice-integration'
}

resource privateEndpointSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: vnet
  name: 'snet-private-endpoints'
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'st${uniqueSuffix}msdocs'
  location: appLocation
  kind: 'StorageV2'
  sku: {
    name: storageSkuName
  }
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: 'Disabled'
    supportsHttpsTrafficOnly: true
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Deny'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
    containerDeleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

resource crawledPagesContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: blobContainerName
  properties: {
    publicAccess: 'None'
  }
}

resource blobPrivateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: blobPrivateEndpointName
  location: appLocation
  properties: {
    subnet: {
      id: privateEndpointSubnet.id
    }
    privateLinkServiceConnections: [
      {
        name: 'blob'
        properties: {
          privateLinkServiceId: storage.id
          groupIds: [
            'blob'
          ]
        }
      }
    ]
  }
}

resource blobPrivateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: blobPrivateDnsZoneName
  location: 'global'
}

resource blobPrivateDnsVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: blobPrivateDnsZone
  name: 'link-${vnet.name}'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: searchServiceName
  location: searchLocation
  sku: {
    name: searchSkuName
  }
  properties: {
    disableLocalAuth: true
    hostingMode: 'default'
    networkRuleSet: {
      ipRules: []
    }
    partitionCount: 1
    publicNetworkAccess: 'enabled'
    replicaCount: 1
    semanticSearch: 'free'
  }
}

resource aiServices 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: aiServicesName
  location: aiServicesLocation
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: aiServicesName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aiServices
  name: embeddingDeploymentName
  sku: {
    name: embeddingDeploymentSku
    capacity: embeddingDeploymentCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: embeddingModelName
      version: embeddingModelVersion
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
}

resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aiServices
  name: chatDeploymentName
  sku: {
    name: chatDeploymentSku
    capacity: chatDeploymentCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: chatModelName
      version: chatModelVersion
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
}

resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: appServicePlanName
  location: appLocation
  kind: 'linux'
  sku: {
    name: appServicePlanSku
    capacity: 1
  }
  properties: {
    reserved: true
  }
}

resource adminApp 'Microsoft.Web/sites@2023-12-01' = {
  name: adminAppName
  location: appLocation
  kind: 'app,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    clientAffinityEnabled: false
    httpsOnly: true
    publicNetworkAccess: 'Enabled'
    serverFarmId: appServicePlan.id
    virtualNetworkSubnetId: appIntegrationSubnet.id
    siteConfig: {
      alwaysOn: true
      appCommandLine: 'PYTHONPATH=/home/site/wwwroot/.python_packages/lib/site-packages python /home/site/wwwroot/app/serve.py'
      ftpsState: 'Disabled'
      healthCheckPath: '/healthz'
      http20Enabled: true
      linuxFxVersion: 'PYTHON|3.13'
      minTlsVersion: '1.2'
      vnetRouteAllEnabled: true
    }
  }
}

resource knowledgeApp 'Microsoft.Web/sites@2023-12-01' = {
  name: knowledgeAppName
  location: appLocation
  kind: 'app,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    clientAffinityEnabled: false
    httpsOnly: true
    publicNetworkAccess: 'Enabled'
    serverFarmId: appServicePlan.id
    siteConfig: {
      alwaysOn: true
      appCommandLine: 'PYTHONPATH=/home/site/wwwroot/.python_packages/lib/site-packages python /home/site/wwwroot/app/serve_knowledge.py'
      ftpsState: 'Disabled'
      healthCheckPath: '/healthz'
      http20Enabled: true
      linuxFxVersion: 'PYTHON|3.13'
      minTlsVersion: '1.2'
    }
  }
}

resource adminStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, adminApp.id, storageBlobDataContributorRoleId)
  scope: storage
  properties: {
    principalId: adminApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobDataContributorRoleId
  }
}

resource adminSearchServiceRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, adminApp.id, searchServiceContributorRoleId)
  scope: search
  properties: {
    principalId: adminApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: searchServiceContributorRoleId
  }
}

resource adminSearchDataRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, adminApp.id, searchIndexDataContributorRoleId)
  scope: search
  properties: {
    principalId: adminApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: searchIndexDataContributorRoleId
  }
}

resource adminOpenAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiServices.id, adminApp.id, openAiUserRoleId)
  scope: aiServices
  properties: {
    principalId: adminApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: openAiUserRoleId
  }
}

resource knowledgeSearchDataRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, knowledgeApp.id, searchIndexDataReaderRoleId)
  scope: search
  properties: {
    principalId: knowledgeApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: searchIndexDataReaderRoleId
  }
}

resource knowledgeOpenAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiServices.id, knowledgeApp.id, openAiUserRoleId)
  scope: aiServices
  properties: {
    principalId: knowledgeApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: openAiUserRoleId
  }
}

output adminAppName string = adminApp.name
output adminAppUrl string = 'https://${adminApp.properties.defaultHostName}'
output adminAppPrincipalId string = adminApp.identity.principalId
output aiServicesName string = aiServices.name
output aiServicesEndpoint string = 'https://${aiServices.name}.${aiServicesEndpointSuffix}/'
output blobAccountUrl string = 'https://${storage.name}.blob.${environment().suffixes.storage}'
output blobContainerName string = blobContainerName
output blobPrivateDnsZoneId string = blobPrivateDnsZone.id
output blobPrivateEndpointId string = blobPrivateEndpoint.id
output blobPrivateEndpointName string = blobPrivateEndpoint.name
output chatDeploymentName string = chatDeployment.name
output embeddingDeploymentName string = embeddingDeployment.name
output knowledgeAppName string = knowledgeApp.name
output knowledgeAppUrl string = 'https://${knowledgeApp.properties.defaultHostName}'
output knowledgeAppPrincipalId string = knowledgeApp.identity.principalId
output knowledgeBaseName string = knowledgeBaseName
output knowledgeSourceName string = knowledgeSourceName
output mcpApiVersion string = mcpApiVersion
output searchEndpoint string = 'https://${search.name}.${searchEndpointSuffix}'
output searchIndexName string = searchIndexName
output searchServiceName string = search.name
output semanticConfigurationName string = semanticConfigurationName
output storageAccountName string = storage.name
