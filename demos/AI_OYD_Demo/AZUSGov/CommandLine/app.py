import os
from openai import AzureOpenAI

endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
deployment = os.getenv("AZURE_OPENAI_CHATGPT_DEPLOYMENT")
search_endpoint = os.getenv("AZURE_AI_SEARCH_ENDPOINT")
search_key = os.getenv("AZURE_AI_SEARCH_KEY")
search_index = os.getenv("AZURE_AI_SEARCH_INDEX")
subscription_key = os.getenv("AZURE_OPENAI_KEY")

print(endpoint)
print(deployment)
print(search_endpoint)
print(search_key)
print(search_index)
print(subscription_key)


# Initialize Azure OpenAI client with key-based authentication
client = AzureOpenAI(
    azure_endpoint = endpoint,
    api_key = subscription_key,
    api_version = os.getenv("AZURE_OPENAI_API_VERSION"),
)

completion = client.chat.completions.create(
    model=deployment,
    messages= [
    {
        "role": "system",
        "content": "You are an AI assistant that helps people find information."
    },
    {
        "role": "user",
        "content": "hello"
    }
],
    max_tokens=800,
    temperature=0,
    top_p=1,
    frequency_penalty=0,
    presence_penalty=0,
    stop=None,
    stream=False
,
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
            "role_information": "You are an AI assistant that helps people find information.",
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

print(completion.choices[0].message.content)