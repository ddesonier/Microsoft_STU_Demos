import streamlit as st
from streamlit_chat import message
from dotenv import load_dotenv
import os
import openai
import datetime
import json
from azure.identity import DefaultAzureCredential
from azure.mgmt.cognitiveservices import CognitiveServicesManagementClient

load_dotenv()

subscription=os.getenv('AZURE_SUBSCRIPTION_ID')
resource_group=os.getenv('AZURE_RESOURCE_GROUP')
accountname=os.getenv('AccountName')

print(subscription)
def list_deployments():
    client = CognitiveServicesManagementClient(
        credential=DefaultAzureCredential(),
        subscription_id=subscription,
    )

    response = client.deployments.list(
        resource_group_name=resource_group,
        account_name=accountname,
    )
    for item in response:
        print(item)
'''
# Get user input for AOAI endpoint, API key, API version, and deployment name
endpoint = st.text_input("Enter the AOAI Endpoint", value="https://{instance_name}.openai.azure.com")
apikey = st.text_input("Enter a API Key", type="password")
apiversion = st.text_input("Enter a API Key", value="2024-02-01")
model = st.text_input("Enter Model Deployment Name", value="{deployment_name}")
'''
# Define available models
models = ["gpt-4o", "gpt-35-turbo"]

# Select a model from the available models
selected_model = st.sidebar.selectbox(
    'What model would you like to try?',
    (models)
) 

# Map selected model to a deployment
#def switch(selected_model):
#    if selected_model == "gpt-4o":
#        return "gpt-4o_Demo"
#    elif selected_model == "gpt-35-turbo":
#        return "completions35turbo"

# Get the deployment name based on the selected model
#model = switch(selected_model)

# Set up the Open AI Client
openai.api_type = "azure"
openai.api_base = "https://tp-tprompt1.openai.azure.com"
openai.api_version = "2024-02-01"
openai.api_key = "e26fd76c0671416dbdc20f1e09110b88"

list_deployments()

# Set up the default prompt for the AI assistant
default_prompt = """
You are an AI assistant  that helps users write concise\
 reports on sources provided according to a user query.\
 You will provide reasoning for your summaries and deductions by\
 describing your thought process. You will highlight any conflicting\
 information between or within sources. Greet the user by asking\
 what they'd like to investigate.
"""

# Get the system prompt from the sidebar
system_prompt = st.sidebar.text_area("System Prompt", default_prompt, height=200)

# Define the seed message for the conversation
seed_message = {"role": "system", "content": system_prompt}

# Session management
if "generated" not in st.session_state:
    st.session_state["generated"] = []
if "past" not in st.session_state:
    st.session_state["past"] = []
if "messages" not in st.session_state:
    st.session_state["messages"] = [seed_message]
if "model_name" not in st.session_state:
    st.session_state["model_name"] = []
if "cost" not in st.session_state:
    st.session_state["cost"] = []
if "total_tokens" not in st.session_state:
    st.session_state["total_tokens"] = []
if "total_cost" not in st.session_state:
    st.session_state["total_cost"] = 0.0

# Display total cost of conversation in sidebar
counter_placeholder = st.sidebar.empty()
counter_placeholder.write(
    f"Total cost of this conversation: ${st.session_state['total_cost']:.5f}"
)


# Clear conversation button
clear_button = st.sidebar.button("Clear Conversation", key="clear")
if clear_button:
    st.session_state["generated"] = []
    st.session_state["past"] = []
    st.session_state["messages"] = [seed_message]
    st.session_state["number_tokens"] = []
    st.session_state["model_name"] = []
    st.session_state["cost"] = []
    st.session_state["total_cost"] = 0.0
    st.session_state["total_tokens"] = []
    counter_placeholder.write(
        f"Total cost of this conversation: ${st.session_state['total_cost']:.5f}"
    )

# Download conversation button
download_conversation_button = st.sidebar.download_button(
    "Download Conversation",
    data=json.dumps(st.session_state["messages"]),
    file_name=f"conversation.json",
    mime="text/json",
)

# Generate response based on user input
def generate_response(prompt):
    st.session_state["messages"].append({"role": "user", "content": prompt})
    completion = openai.ChatCompletion.create(
        engine = model,
        messages=st.session_state["messages"],
    )
    response = completion.choices[0].message.content
    st.session_state["messages"].append({"role": "assistant", "content": response})
    total_tokens = completion.usage.total_tokens
    prompt_tokens = completion.usage.prompt_tokens
    completion_tokens = completion.usage.completion_tokens
    return response, total_tokens, prompt_tokens, completion_tokens

# Main app title
st.title("ChatGPT Demo")

# Container for chat history
response_container = st.container()

# Container for text box
container = st.container()

# User input form
with container:
    with st.form(key="my_form", clear_on_submit=True):
        user_input = st.text_area("You:", key="input", height=100)
        submit_button = st.form_submit_button(label="Send")
    if submit_button and user_input:
        output, total_tokens, prompt_tokens, completion_tokens = generate_response(
            user_input
        )
        st.session_state["past"].append(user_input)
        st.session_state["generated"].append(output)
        st.session_state["model_name"].append(model)
        st.session_state["total_tokens"].append(total_tokens)
        cost = total_tokens * 0.001625 / 1000
        st.session_state["cost"].append(cost)
        st.session_state["total_cost"] += cost

# Display conversation history
if st.session_state["generated"]:
    with response_container:
        for i in range(len(st.session_state["generated"])):
            message(
                st.session_state["past"][i],
                is_user=True,
                key=str(i) + "_user",
                avatar_style="shapes",
            )
            message(
                st.session_state["generated"][i], key=str(i), avatar_style="identicon"
            )
        counter_placeholder.write(
            f"Total cost of this conversation: ${st.session_state['total_cost']:.5f}"
        )
