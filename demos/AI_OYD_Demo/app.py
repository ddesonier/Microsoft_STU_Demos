import os
import streamlit as st
from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
from openai import AzureOpenAI
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexerClient
from azure.core.credentials import AzureKeyCredential

# Azure Storage Account connection string
AZURE_CONNECTION_STRING = os.getenv("AZURE_CONNECTION_STRING")
CONTAINER_NAME = os.getenv("CONTAINER_NAME")

# Initialize Azure Blob Service Client
blob_service_client = BlobServiceClient.from_connection_string(AZURE_CONNECTION_STRING)
container_client = blob_service_client.get_container_client(CONTAINER_NAME)

# Azure Search configuration
search_endpoint = os.getenv("AZURE_AI_SEARCH_ENDPOINT")
search_key = os.getenv("AZURE_AI_SEARCH_KEY")
search_index = os.getenv("AZURE_AI_SEARCH_INDEX")

# Initialize Azure Search Client
search_client = SearchClient(endpoint=search_endpoint, index_name=search_index, credential=AzureKeyCredential(search_key))
indexer_client = SearchIndexerClient(endpoint=search_endpoint, credential=AzureKeyCredential(search_key))

# Set Streamlit page configuration
st.set_page_config(layout="wide")

# Streamlit interface
st.title("Streamlit Interface for Azure OpenAI and Azure Storage")

# Layout with three columns, left column is half the size of the others
col1, col2, col3 = st.columns([1, 2, 2])

with col1:
    # File uploader
    uploaded_file = st.file_uploader("Choose a file")

    if uploaded_file is not None:
        # Ask if the user wants to overwrite the file if it already exists
        # overwrite = st.checkbox("Overwrite if file already exists", value=False)

        # Save the uploaded file to a temporary location
        with open(uploaded_file.name, "wb") as f:
            f.write(uploaded_file.getbuffer())
        
        # Upload the file to Azure Storage
        blob_client = container_client.get_blob_client(uploaded_file.name)
        with open(uploaded_file.name, "rb") as data:
            try:
                blob_client.upload_blob(data, overwrite=True)
                st.success(f"File {uploaded_file.name} uploaded to Azure Storage successfully!")
            except Exception as e:
                st.error(f"Error uploading file: {e}")
                st.stop()

        # # Delete the local copy of the file
        os.remove(uploaded_file.name)

        # Button to trigger re-indexing
        if st.button("Re-index Data"):
            try:
                indexer_client.run_indexer(search_index)
                st.success("Re-indexing triggered successfully!")
            except Exception as e:
                st.error(f"Error triggering re-indexing: {e}")

with col2:
    # System prompt input
    system_prompt = st.text_input("System Prompt", "You are an AI assistant that helps people find information.")

    # User prompt input
    user_prompt = st.text_input("User Prompt", "hello")

    # Display the prompts
    st.write("System Prompt:", system_prompt)
    st.write("User Prompt:", user_prompt)

    # Azure OpenAI configuration
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    deployment = os.getenv("AZURE_OPENAI_CHATGPT_DEPLOYMENT")
    subscription_key = os.getenv("AZURE_OPENAI_KEY")

    # Initialize Azure OpenAI client with key-based authentication
    client = AzureOpenAI(
        azure_endpoint = endpoint,
        api_key = subscription_key,
        api_version = os.getenv("AZURE_OPENAI_API_VERSION"),
    )

with col3:
    # Create completion
    completion = client.chat.completions.create(
        model=deployment,
        messages= [
        {
            "role": "system",
            "content": system_prompt
        },
        {
            "role": "user",
            "content": user_prompt
        }
    ],
        max_tokens=800,
        temperature=0,
        top_p=1,
        frequency_penalty=0,
        presence_penalty=0,
        stop=None,
        stream=False,
        extra_body={
          "data_sources": [{
              "type": "azure_search",
              "parameters": {
                "endpoint": f"{search_endpoint}",
                "index_name": search_index,
                "semantic_configuration": "default",
                "query_type": "simple",
                "fields_mapping": {},
                "in_scope": True,
                "role_information": system_prompt,
                "filter": None,
                "strictness": 3,
                "top_n_documents": 5,
                "authentication": {
                  "type": "api_key",
                  "key": f"{search_key}"
                }
              }
            }]
        })

    # Display the completion response with a border
    st.markdown(
        f"""
        <div style="border: 2px solid #4CAF50; padding: 10px; border-radius: 5px; width: 100%; box-sizing: border-box;">
            <h4>Response:</h4>
            <p>{completion.choices[0].message.content}</p>
        </div>
        """,
        unsafe_allow_html=True
    )