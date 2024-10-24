```markdown
# Streamlit Interface for Azure OpenAI and Azure Storage

This project demonstrates how to integrate Azure OpenAI and Azure Storage using a Streamlit interface. The application allows users to upload files to Azure Blob Storage, edit system and user prompts for Azure OpenAI, and trigger Azure Cognitive Search indexing.

## Prerequisites

- Python 3.7 or higher
- Azure Subscription
- Azure OpenAI Service
- Azure Blob Storage
- Azure Cognitive Search

## Setup

1. **Clone the repository**:
   ```sh
   git clone https://github.com/your-repo/azure-openai-storage-integration.git
   cd azure-openai-storage-integration
   ```

2. **Create a virtual environment and activate it**:
   ```sh
   python -m venv .venv
   .venv\Scripts\activate  # On Windows
   source .venv/bin/activate  # On macOS/Linux
   ```

3. **Install the required libraries**:
   ```sh
   pip install -r requirements.txt
   ```

4. **Set up environment variables**:
   Create a `.env` file in the root directory of the project and add the following environment variables:
   ```env
   AZURE_OPENAI_ENDPOINT=your_openai_endpoint
   AZURE_OPENAI_CHATGPT_DEPLOYMENT=your_chatgpt_deployment
   AZURE_AI_SEARCH_ENDPOINT=your_search_endpoint
   AZURE_AI_SEARCH_KEY=your_search_key
   AZURE_AI_SEARCH_INDEX=your_search_index
   AZURE_OPENAI_KEY=your_openai_key
   AZURE_CONNECTION_STRING=your_storage_connection_string
   CONTAINER_NAME=your_container_name
   AZURE_AI_SEARCH_INDEXER_NAME=your_indexer_name
   ```

## Usage

1. **Run the Streamlit app**:
   ```sh
   streamlit run app.py
   ```

2. **Interact with the interface**:
   - **Upload Files**: Use the file uploader to upload files to Azure Blob Storage. You can choose to overwrite existing files if needed.
   - **Edit Prompts**: Edit the system and user prompts for Azure OpenAI.
   - **Trigger Indexing**: After uploading files, you can trigger Azure Cognitive Search indexing to update the search index.

## Code Overview

### app.py

The `app.py` file contains the main Streamlit application code. Here's a brief overview of its structure:

- **Imports and Initialization**:
  ```python
  import os
  import streamlit as st
  from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
  from openai import AzureOpenAI
  from azure.search.documents import SearchClient
  from azure.search.documents.indexes import SearchIndexerClient
  from azure.core.credentials import AzureKeyCredential
  ```

- **Environment Variables**:
  ```python
  AZURE_CONNECTION_STRING = os.getenv("AZURE_CONNECTION_STRING")
  CONTAINER_NAME = os.getenv("CONTAINER_NAME")
  search_endpoint = os.getenv("AZURE_AI_SEARCH_ENDPOINT")
  search_key = os.getenv("AZURE_AI_SEARCH_KEY")
  search_index = os.getenv("AZURE_AI_SEARCH_INDEX")
  ```

- **Azure Clients Initialization**:
  ```python
  blob_service_client = BlobServiceClient.from_connection_string(AZURE_CONNECTION_STRING)
  container_client = blob_service_client.get_container_client(CONTAINER_NAME)
  search_client = SearchClient(endpoint=search_endpoint, index_name=search_index, credential=AzureKeyCredential(search_key))
  indexer_client = SearchIndexerClient(endpoint=search_endpoint, credential=AzureKeyCredential(search_key))
  ```

- **Streamlit Configuration**:
  ```python
  st.set_page_config(layout="wide")
  st.title("Streamlit Interface for Azure OpenAI and Azure Storage")
  ```

- **Streamlit Interface Layout**:
  The interface is divided into three columns:
  - **Column 1**: File uploader and overwrite option.
  - **Column 2**: System and user prompts input.
  - **Column 3**: Display the response from Azure OpenAI.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
```

This 

README.md

 file provides an overview of the Streamlit application, setup instructions, and usage examples. Adjust the repository URL and any other details as necessary for your specific project.