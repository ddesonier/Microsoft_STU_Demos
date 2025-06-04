from typing import Tuple
import sys
from io import StringIO
import contextlib
import traceback
import re
import time
import os
import streamlit as st
from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
# import openai
from openai import AzureOpenAI
from azure.search.documents import SearchClient, IndexDocumentsBatch
from azure.search.documents.indexes import SearchIndexerClient
from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
import logging
import sys

logging.basicConfig(stream=sys.stdout, level=logging.INFO)

print("Azure OpenAI API initialized")
logging.info("Azure OpenAI API initialized")

load_dotenv()
# Azure OpenAI configuration
endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
model = os.getenv("AZURE_OPENAI_CHATGPT_DEPLOYMENT")
api_key = os.getenv("AZURE_OPENAI_KEY")
api_version = os.getenv("AZURE_OPENAI_API_VERSION")

print("Azure OpenAI API initialized")
client = AzureOpenAI(
    azure_endpoint = endpoint,
    api_key = api_key,
    api_version = api_version
)

# Azure Storage Account connection string
STORAGE_ACCOUNT_NAME = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
CONTAINER_NAME = os.getenv("CONTAINER_NAME")
ACCOUNT_URL: str = f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net"
# print(f"Account URL: {ACCOUNT_URL}")

# Initialize Azure Blob Service Client
credential = DefaultAzureCredential()
blob_service_client = BlobServiceClient(account_url=f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net", credential=credential)
container_client = blob_service_client.get_container_client(CONTAINER_NAME)

# Azure Search configuration
search_endpoint = os.getenv("AZURE_AI_SEARCH_ENDPOINT")
search_key = os.getenv("AZURE_AI_SEARCH_KEY")
search_index = os.getenv("AZURE_AI_SEARCH_INDEX")
search_indexer = os.getenv("AZURE_AI_SEARCH_INDEXER")

# Initialize Azure Search Client
search_client = SearchClient(endpoint=search_endpoint, index_name=search_index, credential=AzureKeyCredential(search_key))
indexer_client = SearchIndexerClient(endpoint=search_endpoint, credential=AzureKeyCredential(search_key))

if 'sys_prompt' not in st.session_state:
    st.session_state.sys_prompt = "You are an assistant to a programmer, in responses only provide code when asked to convert.  When asked to explain, provide detailed explanation that is technical in depth" 

# Function to change the variable
def change_global_var(value):
    st.session_state.sys_prompt = value

@contextlib.contextmanager
def capture_output():
    """Capture stdout and stderr"""
    new_out, new_err = StringIO(), StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    try:
        sys.stdout, sys.stderr = new_out, new_err
        yield sys.stdout, sys.stderr
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def submit_prompt(task_description: str) -> Tuple[str, str]:
    """Process the code using Azure OpenAI API and return feedback and refined code."""
    prompt = f"""
Task Description: {task_description}

Please provide:
1. A detailed technical response
2. Expert written code if asked to generate code 


Format your response exactly as follows:
---FEEDBACK---
[Your feedback here]
---CODE---
[The generated code here without any markdown formatting or additional explanation within the code section]
"""
    try:
        print("st.session_state.sys_prompt", st.session_state.sys_prompt)
        message = client.chat.completions.create(
            model=model,
            messages=[
            {
            "role": "system",
            "content": st.session_state.sys_prompt
            },
            {
                "role": "user", 
                "content": prompt
            }
            ], 
        max_tokens=8000,
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
                "role_information": st.session_state.sys_prompt,
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
        # print("Message: ", message)
        content = message.choices[0].message.content
        # Extract feedback and code
        feedback_match = re.search(r"---FEEDBACK---(.*?)---CODE---", content, re.DOTALL)
        code_match = re.search(r"---CODE---(.*)", content, re.DOTALL)

        if feedback_match and code_match:
            feedback = feedback_match.group(1).strip()
            refined_code = code_match.group(1).strip()
            return feedback, refined_code
        else:
            return content, ""
    except Exception as e:
        st.error(f"Error calling Azure OpenAI API: {e}")
        return "", ""


def process_code(task_description: str, code: str) -> Tuple[str, str]:
    """Process the code using Azure OpenAI API and return feedback and refined code."""
    prompt = f"""
Task Description: {task_description}


Original Code:
```python
{code}
```
Please provide:
1. A detailed code review and feedback
2. A refined version of the code that implements the requested changes
3. Make sure the code doesn't require user input and uses test cases instead
4. Prefer using emoji-based output over terminal colors for better compatibility
5. If using colors, use only standard print statements or emojis
6. If asked, explain the code in detail

Format your response exactly as follows:
---FEEDBACK---
[Your feedback here]
---CODE---
[The refined code here without any markdown formatting or additional explanation within the code section]
"""
    try:
        message = client.chat.completions.create(
            model=model,
            messages=[
            {
            "role": "system",
            "content": st.session_state.sys_prompt
            },
            {
                "role": "user", 
                "content": prompt
            }
            ], 
        max_tokens=8000,
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
                "role_information": st.session_state.sys_prompt,
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
        # print("Message: ", message)
        content = message.choices[0].message.content
        # Extract feedback and code
        feedback_match = re.search(r"---FEEDBACK---(.*?)---CODE---", content, re.DOTALL)
        code_match = re.search(r"---CODE---(.*)", content, re.DOTALL)

        if feedback_match and code_match:
            feedback = feedback_match.group(1).strip()
            refined_code = code_match.group(1).strip()
            return feedback, refined_code
        else:
            return content, ""
    except Exception as e:
        st.error(f"Error calling Azure OpenAI API: {e}")
        return "", ""

def explain_code(task_description: str, code: str) -> Tuple[str, str]:
    print("Taskdescription: ", task_description)
    """Process the code using Azure OpenAI API and return feedback and refined code."""
    prompt = f"""
Task Description: {task_description}


Original Code:
```python
{code}
```
Please provide:
1. A detailed code explanation
2. Explain line by line the code and what it does
3. Offer suggestions for improvement
4. Provide examples of how the code can be used


Format your response exactly as follows:
---FEEDBACK---
[Your feedback here]
---CODE---
[The refined code here without any markdown formatting or additional explanation within the code section]
"""
    try:
        message = client.chat.completions.create(
            model=model,
            messages=[
            {
            "role": "system",
            "content": st.session_state.sys_prompt
            },
            {
                "role": "user", 
                "content": prompt
            }
            ], 
        max_tokens=8000,
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
                "role_information": st.session_state.sys_prompt,
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
        # print("Message: ", message)
        content = message.choices[0].message.content
        # Extract feedback and code
        feedback_match = re.search(r"---FEEDBACK---(.*?)---CODE---", content, re.DOTALL)
        code_match = re.search(r"---CODE---(.*)", content, re.DOTALL)

        if feedback_match and code_match:
            feedback = feedback_match.group(1).strip()
            refined_code = code_match.group(1).strip()
            return feedback, refined_code
        else:
            return content, ""
    except Exception as e:
        st.error(f"Error calling Azure OpenAI API: {e}")
        return "", ""

def create_readme(code: str) -> Tuple[str, str]:
    """Process the code using Azure OpenAI API and return feedback and refined code."""
    prompt = f"""

Original Code:
```python
{code}
```
Please:
1. Create a well formatted readme.md file that is comprehensive and easy to understand
2. Describe purpose of code
3. Details about prerequisites, installation, usage, and examples

Format your response exactly as follows:
---FEEDBACK---
[Your feedback here]
---CODE---
[The refined code here without any markdown formatting or additional explanation within the code section]
"""
    try:
        message = client.chat.completions.create(
            model=model,
            messages=[
            {
            "role": "system",
            "content": st.session_state.sys_prompt
            },
            {
                "role": "user", 
                "content": prompt
            }
            ], 
        max_tokens=8000,
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
                "role_information": st.session_state.sys_prompt,
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
        # print("Message: ", message)
        content = message.choices[0].message.content
        # Extract feedback and code
        feedback_match = re.search(r"---FEEDBACK---(.*?)---CODE---", content, re.DOTALL)
        code_match = re.search(r"---CODE---(.*)", content, re.DOTALL)

        if feedback_match and code_match:
            feedback = feedback_match.group(1).strip()
            refined_code = code_match.group(1).strip()
            return feedback, refined_code
        else:
            return content, ""
    except Exception as e:
        st.error(f"Error calling Azure OpenAI API: {e}")
        return "", ""


def main():
    logging.info("Starting AI Code Assistant")
    logging.info("Azure OpenAI API Endpoint: %s", endpoint)
    st.set_page_config(
        page_title="AI Code Assistant",
        page_icon="🤖",
        layout="wide"
    )
    
    st.title("🤖 AI Code Assistant")
    
    # Initialize session state
    if 'api_key' not in st.session_state:
        # Try to get API key from environment variables first
        st.session_state.api_key = os.getenv("AZURE_OPENAI_KEY")
    if 'run_clicked' not in st.session_state:
        st.session_state.run_clicked = False
    
    with st.sidebar:
        # File uploader
        uploaded_file = st.file_uploader("Choose a file")

        if uploaded_file is not None:
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

            # Delete the local copy of the file
            os.remove(uploaded_file.name)

        if st.button("Re-index Data"):
            try:
                indexer_client.run_indexer(search_indexer)
                st.success("Re-indexing triggered successfully!")
            except Exception as e:
                st.error(f"Error triggering re-indexing: {e}")

        
        if st.button("Change System Prompt"):
            change_global_var(st.text_area(
                "System Prompt",
                height=150,
                placeholder=st.session_state.sys_prompt
            ))
            print("st.session_state.sys_prompt", st.session_state.sys_prompt)

    col1, col2 = st.columns([1,1])

    with col1:
        task_description = st.text_area(
            "Task Description",
            height=100,
            placeholder="Describe what you want to achieve with your code..."
        )

        code = st.text_area(
            "Your Code",
            height=300,
            placeholder="Paste your code here...",
            help="Paste the code you want Azure OpenAI to analyze and improve"
        )

        selected_action = st.selectbox(
            "Select an Action",
            ["Analyze Code", "Create Readme", "Explain Code", "Submit Prompt"]
        )

        if st.button("Submit", type="primary"):
            if selected_action == "Analyze Code":
                if not task_description:
                    task_description = "Analyze Code"
                    #st.error("Please provide a task description")
                elif not code:
                    st.error("Please provide some code to analyze")
                else:
                    with st.spinner("Analyzing your code..."):
                        feedback, refined_code = process_code(task_description, code)
                        st.session_state.feedback = feedback
                        st.session_state.refined_code = refined_code
                        st.session_state.run_clicked = False

            elif selected_action == "Create Readme":
                if task_description:
                    task_description = "Create Readme"
                    #st.error("Task description will not be used in this prompt")
                elif not code:
                    st.error("Please provide code to generate readme file")
                else:
                    with st.spinner("Submitting your prompt..."):
                        feedback, refined_code = create_readme(code)
                        st.session_state.feedback = feedback
                        st.session_state.refined_code = refined_code
                        st.session_state.run_clicked = False

            elif selected_action == "Explain Code":
                if not task_description:
                    task_description = "Explain Code"
                    #st.error("Please provide a task description")
                elif not code:
                    st.error("Please provide some code to explain")
                else:
                    with st.spinner("Analyzing your code..."):
                        feedback, refined_code = explain_code(task_description, code)
                        st.session_state.feedback = feedback
                        st.session_state.refined_code = refined_code
                        st.session_state.run_clicked = False

            elif selected_action == "Submit Prompt":
                if not task_description:
                    st.error("Please provide a task description")
                elif code:
                    st.error("The Code will not be used in this prompt.")
                else:
                    with st.spinner("Submitting your prompt..."):
                        feedback, refined_code = submit_prompt(task_description)
                        st.session_state.feedback = feedback
                        st.session_state.refined_code = refined_code
                        st.session_state.run_clicked = False

    with col2:
        st.subheader("Output")

        if 'feedback' in st.session_state and st.session_state.feedback:
            st.markdown(st.session_state.feedback)

        if 'refined_code' in st.session_state and st.session_state.refined_code:
            st.code(st.session_state.refined_code, language='python')

if __name__ == "__main__":
    main()