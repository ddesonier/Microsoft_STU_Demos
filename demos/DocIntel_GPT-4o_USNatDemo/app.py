import openai
import os
import base64
import json
# from dotenv import load_dotenv
from datetime import datetime
from delete_files_in_directory import delete_files_in_directory
from generate_schema_from_json import generate_schema_from_json
# from cosmodDB_upsert_item import cosmodDB_upsert_item
# from pdf_to_base64_images import pdf_to_base64_images

#load_dotenv()

# api_key = os.getenv("AZURE_OPENAI_KEY")
#api_base = os.getenv("AZURE_OPENAI_ENDPOINT")
# api_version = os.getenv("AZURE_OPENAI_API_VERSION")
# model = os.getenv("AZURE_OPENAI_CHATGPT_DEPLOYMENT")


###################################################################
######          Fill in these vairables with your values     ######
api_key = ""
api_base = ""
api_version = ""
model = ""
###################################################################


# Set up the Open AI Client
os.environ["AZURE_OPENAI_ENDPOINT"] = api_base
openai.api_type = "azure"
openai.api_base = api_base
openai.api_version = api_version
openai.api_key = api_key

# cosmosapi = os.getenv("AZURE_COSMOS_API")
# cosmosEndpoint = os.getenv("AZURE_COSMOS_ENDPOINT")

@staticmethod
def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")

def images_to_base64(image_path):
    """
    Convert multiple image files to a list of base64 strings.

    :param image_paths: List of paths to the image files
    :return: List of base64 encoded strings of the images
    """
    print("images_to_base64 ", image_path)

    if not os.path.exists(image_path):
        raise FileNotFoundError(f"The file {image_path} does not exist.")
    print("images_to_base64 Path: ", image_path)
    
    with open(image_path, "rb") as image_file:
        # Read the binary data from the file
        image_data = image_file.read()
        # Encode the binary data to a base64 string
        base64_encoded = base64.b64encode(image_data).decode('utf-8')
        #base64_images.append(base64_encoded)
    
    return base64_encoded


def extract_invoice_data(base64_image):
    print("extract_invoice_data: " , openai.api_base)
    system_prompt = f"""
    You are an OCR-like data extraction tool that extracts invoice data from images.
   
    1. Please extract the data in this invoice, grouping data according to theme/sub groups, and then output into JSON.

    2. Please keep the keys and values of the JSON in the original language. 

    3. The type of data you might encounter in the invoice includes but is not limited to: company information, customer information, invoice information,
    unit price, invoice date, part numbers, taxes, each line item may rows embedded showing qurntity and unit proces based on quantity, 
    and total invoice total etc.  For each row that has embedded rows, keep them in the same row. 

    4. If the page contains no charge data, please output an empty JSON object and don't make up any data.

    5. If there are blank data fields in the invoice, please include them as "null" values in the JSON object.
    
    6. If there are tables in the invoice, capture all of the rows and columns in the JSON object. 
    Even if a column is blank, include it as a key in the JSON object with a null value.
    
    7. If a row is blank denote missing fields with "null" values. 
    
    8. Don't interpolate or make up data.

    9. Please maintain the table structure of the items, i.e. capture all of the rows and columns in the JSON object.

    """
    
    response = openai.chat.completions.create(
        model = model,
        response_format={ "type": "json_object" },
        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "extract the data in this invoice and output into JSON "},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}", "detail": "high"}}
                ]
            }
        ],
        temperature=0.0,
    )
    print("Extract Invoice Data Call Info:")
    print(response.model)
    print(response.usage)
    return response.choices[0].message.content


def extract_from_multiple_pages(base64_image, original_filename, output_directory):
    invoice_json = extract_invoice_data(base64_image)
    #print(invoice_json)
    #invoice_data = json.loads(invoice_json)
    entire_invoice = json.loads(invoice_json)
    #entire_invoice.append(invoice_data)

    # Ensure the output directory exists
    os.makedirs(output_directory, exist_ok=True)
    original_filename = original_filename.lower()

    # Construct the output file path
    timestamp = datetime.now().strftime('%Y%m%d%H%M')
    #output_filename = os.path.join(output_directory, original_filename.replace('.pdf', '_extracted_' + timestamp + '.json'))
    output_filename = os.path.join(output_directory, original_filename.replace('.jpg', '_extracted_' + timestamp + '.json'))

    # Save the entire_invoice list as a JSON file
    with open(output_filename, 'w', encoding='utf-8') as f:
        json.dump(entire_invoice, f, ensure_ascii=False, indent=4)
    print("PDF file: " + original_filename + " has been extracted to file: " + output_filename)
    return output_filename

def main_extract(read_path, write_path):
    for filename in os.listdir(read_path):
        file_path = os.path.join(read_path, filename)
        if os.path.isfile(file_path):
#            base64_images = pdf_to_base64_images(file_path)
            base64_images = images_to_base64(file_path)
            extract_from_multiple_pages(base64_images, filename, write_path)




def transform_invoice_data(json_raw, json_schema):
    system_prompt = f"""
    You are a data transformation tool that takes in JSON data and a reference JSON schema, and outputs JSON data according to the schema.
    Not all of the data in the input JSON will fit the schema, so you may need to omit some data or add null values to the output JSON.
    Translate all data into English if not already in English. 
    Ensure values are formatted as specified in the schema (e.g. dates as YYYY-MM-DD).
    Here is the schema:
    {json_schema}
    """
    userprompt = f"""
    Transform the following raw JSON data according to the provided schema. Ensure all data is in English and formatted as specified by values in the schema. 
    Keep JSON strucutre the same for purposes of inserting into a COMOS DB. 
    Here is the raw JSON: 
    {json_raw}
    """

    response = openai.chat.completions.create(
        model = model,
        response_format={ "type": "json_object" },
        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": userprompt
            }
        ],
        temperature=0.0,
        top_p=0.0,
    )
    print("Transform Invoice Data Call Info:")
    print(response.model)
    print(response.usage)
    json_content = json.loads(response.choices[0].message.content)
    return json_content #json.loads(response.choices[0].message.content)

def main_transform(extracted_invoice_json_path, myjson_schema, save_path, company):
    # Load the JSON schema
    with open(myjson_schema, 'r', encoding='utf-8') as f:
        json_schema = json.load(f)

    # Ensure the save directory exists
    os.makedirs(save_path, exist_ok=True)

    # Process each JSON file in the extracted invoices directory
    for filename in os.listdir(extracted_invoice_json_path):
        if filename.endswith(".json"):
            file_path = os.path.join(extracted_invoice_json_path, filename)
            print("filename: " + filename)
            # Load the extracted JSON
            with open(file_path, 'r', encoding='utf-8') as f:
                json_raw = json.load(f)
            # Transform the JSON data
            transformed_json = transform_invoice_data(json_raw, json_schema)
            transformed_filename = filename.replace('extracted', 'transformed')
            transformed_file_path = os.path.join(save_path, transformed_filename)
            with open(transformed_file_path, 'w', encoding='utf-8') as f:
                json.dump(transformed_json, f, ensure_ascii=False, indent=2)
            print("JSON Extraction file: " + filename + " has been transformed to file: " + transformed_filename)
            # cosmodDB_upsert_item(cosmosEndpoint, cosmosapi, transformed_file_path, company)

def main_create_json_schema(extracted_invoice_json_path, json_schema_file):
    print("main_create_json_schema")
    for filename in os.listdir(extracted_invoice_json_path):
        file_path = os.path.join(extracted_invoice_json_path, filename)
        if os.path.isfile(file_path):
            with open(file_path, 'r') as file:
                json_data = json.load(file)
            json_schema = generate_schema_from_json(json_data)

            with open(json_schema_file, 'w', encoding='utf-8') as f:
                json.dump(json_schema, f, ensure_ascii=False, indent=4)

            print(json_schema_file)

# Uncommment the following code to run the script for one or more companies
companys = []
companys.append("abc")
#companys.append("arrow")
#companys.append("euc")
#companys.append("max")


print(companys)

for company in companys:
    print("Processing company: " + company)
    read_path = "./data/" + company + "invoices"
    write_path = read_path + "/" + "extracted_invoice_json"
    extracted_invoice_json_path = write_path
    json_schema_path = read_path + "/" + "schema"
    save_path = read_path + "/" + "transformed_invoice_json"
    json_schema = json_schema_path + "/" + "json_schema_" + company + "_extracted.json"

    main_extract(read_path, extracted_invoice_json_path)

    if os.path.exists(json_schema):
        print("Schema file already exists, using: " + json_schema)
    else:
        print("Schema file does not exist, creating: " + json_schema)
        main_create_json_schema(extracted_invoice_json_path, json_schema)
    main_transform(extracted_invoice_json_path, json_schema, save_path, company)
    # Delete files in the extracted_invoice_json folder after processing
    delete_files_in_directory(extracted_invoice_json_path)
    # delete_files_in_directory(save_path)