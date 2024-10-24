import os
from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
import re
# from dotenv import load_dotenv

# # Load environment variables from .env file
# load_dotenv()

# Azure Storage Account connection string and container name
AZURE_CONNECTION_STRING = os.getenv("AZURE_CONNECTION_STRING")
CONTAINER_NAME = os.getenv("CONTAINER_NAME")

# Initialize Azure Blob Service Client
blob_service_client = BlobServiceClient.from_connection_string(AZURE_CONNECTION_STRING)
container_client = blob_service_client.get_container_client(CONTAINER_NAME)

def sanitize_blob_name(blob_name):
    """Sanitize the blob name to ensure it only contains valid characters."""
    return re.sub(r'[^a-zA-Z0-9_\-=]', '_', blob_name)

def upload_file_to_blob(file_path):
    """Upload a file to Azure Blob Storage."""
    try:
        # Sanitize the file name
        blob_name = sanitize_blob_name(os.path.basename(file_path))

        # Create a blob client
        blob_client = container_client.get_blob_client(blob_name)

        # Upload the file
        with open(file_path, "rb") as data:
            blob_client.upload_blob(data, overwrite=True)
        
        print(f"File {file_path} uploaded to Azure Blob Storage successfully as {blob_name}!")
    except Exception as e:
        print(f"Error uploading file: {e}")

if __name__ == "__main__":
    # Ensure the environment variables are set
    if not AZURE_CONNECTION_STRING or not CONTAINER_NAME:
        print("Please ensure AZURE_CONNECTION_STRING and CONTAINER_NAME environment variables are set.")
    else:
        # Path to the file you want to upload
        file_path = input("Enter the path to the file you want to upload: ")
        print(file_path)
        if os.path.isfile(file_path):
            upload_file_to_blob(file_path)
        else:
            print(f"File {file_path} does not exist.")