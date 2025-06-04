# AI Code Assistant

This application provides a Streamlit-based interface to interact with Azure OpenAI. It can analyze code, generate README content, explain code, and process custom prompts.

---

## Features

1. **Azure OpenAI Integration**  
   - Uses Azure OpenAI API to process code, generate feedback, and produce refined code snippets.
2. **Readme Generation**  
   - Automatically creates a well-structured README comparing and explaining the provided code.
3. **Code Explanation**  
   - Provides detailed explanations for code snippets, identifying potential improvements.
4. **RAG (Retrieval Augmented Generation) Support**  
   - Uploads content to an Azure Storage Account connected to an AI Search Indexer, enabling retrieval of additional context or new information for more precise output.

---

## Requirements

- Python 3.8 or higher  
- A working Azure OpenAI endpoint and API key  
- Properly configured Azure Storage Account with an associated AI Search Indexer  
- Properly configured Azure Search (endpoint, index, indexer)  
- A `.env` file with environment variables:
  - `AZURE_OPENAI_ENDPOINT`
  - `AZURE_OPENAI_CHATGPT_DEPLOYMENT`
  - `AZURE_OPENAI_KEY`
  - `AZURE_OPENAI_API_VERSION`
  - `AZURE_STORAGE_ACCOUNT_NAME`
  - `CONTAINER_NAME`
  - `AZURE_AI_SEARCH_ENDPOINT`
  - `AZURE_AI_SEARCH_KEY`
  - `AZURE_AI_SEARCH_INDEX`
  - `AZURE_AI_SEARCH_INDEXER`

---

## Installation

1. Clone the repository.  
2. Create a virtual environment and activate it:
   ```
   python -m venv venv
   venv\Scripts\activate  # For Windows
   ```
3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
4. Create a `.env` file and add your Azure credentials.

---

## Usage

1. Run the application:
   ```
   streamlit run app.py
   ```
2. Enter your **Task Description** and **Your Code**, then pick an action from the dropdown:
   - **Analyze Code**  
   - **Create Readme**  
   - **Explain Code**  
   - **Submit Prompt**  
3. Click **Submit** to send your request to Azure OpenAI.  
4. The **Output** on the right shows the generated feedback and refined code.

---

## Explanation

- **`process_code`** analyzes and refines the code using Azure OpenAI.  
- **`create_readme`** generates a well-structured README, describing the purpose of the code and including usage instructions.  
- **`explain_code`** provides a step-by-step explanation of the code’s functionality.  
- **`submit_prompt`** allows submission of custom prompts to Azure OpenAI.  
- The application also uploads content to an Azure Storage Account connected to an AI Search Indexer, allowing a Retrieval Augmented Generation (RAG) approach to incorporate additional context.

---

### Docker

```bash
$ docker login damonsregistry.azurecr.io

$ docker build --no-cache -t damonsregistry/app .

$ docker run -p 8501:8501 damonsregistry/app

$ docker tag damonsregistry/app damonsregistry.azurecr.io/app:v1

$ docker push damonsregistry.azurecr.io/app:v1
```

---

### Azure App Service Deployment

1. **Set the container port:**  
   If your app listens on port 8501, set the `WEBSITES_PORT` application setting to `8501` in the Azure portal under **Configuration > Application settings**.

2. **Update App Settings from .env:**  
   Use the provided PowerShell script to sync your `.env` file with Azure App Service settings:
   ```powershell
   .\update_app_settings.ps1 -ResourceGroup "<your-resource-group>" -AppName "<your-app-service-name>" -DotEnvPath ".env"
   ```

3. **Deploy the container:**  
   Configure your App Service to use the image from your Azure Container Registry.

4. **Check logs:**  
   Use the Azure portal's **Log Stream** to monitor your app's output and troubleshoot issues.

---

## Contributing

1. Fork this repository.  
2. Create a new branch for your feature:  
   ```
   git checkout -b feature/my-feature
   ```
3. Commit your changes:  
   ```
   git commit -m "Add new feature"
   ```
4. Push to the branch:  
   ```
   git push origin feature/my-feature
   ```
5. Open a Pull Request.

---

## License

This project is licensed under the [MIT License](LICENSE).