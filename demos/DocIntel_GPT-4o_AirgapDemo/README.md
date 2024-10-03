# DocIntel GPT-4o AirGapDemo

This project demonstrates the use of Azure OpenAI to extract and process invoice data from images. It includes functionalities to encode images to base64, and extract invoice data using the GPT-4o model deployed on Azure OpenAI.

## Setup Instructions

### Prerequisites

- Python 3.8 or higher
- pip (Python package installer)

### Installation

1. **Clone the repository**:
    ```sh
    git clone https://github.com/your-repo/DocIntel_GPT-4o_USNatDemo.git
    cd DocIntel_GPT-4o_USNatDemo
    ```

2. **Create a virtual environment**:
    ```sh
    python -m venv myenv
    ```

3. **Activate the virtual environment**:
    - On Windows:
        ```sh
        myenv\Scripts\activate
        ```
    - On macOS and Linux:
        ```sh
        source myenv/bin/activate
        ```

4. **Install the required packages**:
    ```sh
    pip install -r requirements.txt
    ```

### Python Package Requirements

- openai==0.27.0
- azure-cosmos==4.2.0

### Commands to Install Packages

```sh
pip install openai==1.45.1 # or higher


```markdown
# DocIntel GPT-4o USNatDemo

This project demonstrates the use of Azure OpenAI and CosmosDB to extract and process invoice data from images. It includes functionalities to encode images to base64, delete files in a directory, and extract invoice data using the GPT-4o model deployed on Azure OpenAI.

## Setup Instructions

### Prerequisites

- Python 3.8 or higher
- pip (Python package installer)

### Installation

1. **Clone the repository**:
    ```sh
    git clone https://github.com/your-repo/DocIntel_GPT-4o_USNatDemo.git
    cd DocIntel_GPT-4o_USNatDemo
    ```

2. **Create a virtual environment**:
    ```sh
    python -m venv myenv
    ```

3. **Activate the virtual environment**:
    - On Windows:
        ```sh
        myenv\Scripts\activate
        ```
    - On macOS and Linux:
        ```sh
        source myenv/bin/activate
        ```

4. **Install the required packages**:
    ```sh
    pip install -r requirements.txt
    ```

### Python Package Requirements

- openai==0.27.0
- azure-cosmos==4.2.0

### Commands to Install Packages

```sh
pip install openai==0.27.0
pip install azure-cosmos==4.2.0
```

### Environment Variables

Ensure the following environment variables are set in your environment or directly in the script:

- `AZURE_OPENAI_KEY`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_API_VERSION`
- `AZURE_OPENAI_CHATGPT_DEPLOYMENT`
- `AZURE_COSMOS_API`
- `AZURE_COSMOS_ENDPOINT`

### Usage

1. **Run the script**:
    ```sh
    python app.py
    ```

2. **Example function call**:
    ```python
    from app import encode_image, delete_files_in_directory

    # Encode an image
    base64_image = encode_image("path/to/your/image.png")

    # Delete files in a directory
    delete_files_in_directory("path/to/your/directory")
    ```

