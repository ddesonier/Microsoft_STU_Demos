import os
from azure.search.documents.indexes import SearchIndexerClient
from azure.core.credentials import AzureKeyCredential

# Load environment variables
search_endpoint = os.getenv("AZURE_AI_SEARCH_ENDPOINT")
search_key = os.getenv("AZURE_AI_SEARCH_KEY")
search_index = os.getenv("AZURE_AI_SEARCH_INDEX")

# Initialize Azure Search Indexer Client
indexer_client = SearchIndexerClient(endpoint=search_endpoint, credential=AzureKeyCredential(search_key))

def trigger_indexing(indexer_name):
    try:
        indexer_client.run_indexer(indexer_name)
        print(f"Re-indexing triggered successfully for indexer: {indexer_name}")
    except Exception as e:
        print(f"Error triggering re-indexing: {e}")

if __name__ == "__main__":
    if not search_endpoint or not search_key or not search_index:
        print("Please ensure AZURE_AI_SEARCH_ENDPOINT, AZURE_AI_SEARCH_KEY, and AZURE_AI_SEARCH_INDEXER_NAME environment variables are set.")
    else:
        trigger_indexing(search_index)