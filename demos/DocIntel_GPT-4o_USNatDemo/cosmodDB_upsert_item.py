from azure.cosmos import CosmosClient
import azure.cosmos.exceptions as exceptions

def cosmodDB_upsert_item(cosmosEndpoint, cosmosapi, jsondata, database):
# Read the JSON file
    print("cosmodDB_upsert_item")
    print("Uploading to Cosmos DB", jsondata, "  for Database ", database)
    # Initialize the Cosmos client
    container = database # For converneince, we are passing the container name as the database name
    jsondata = jsondata.replace("json{" , "{")
    client = CosmosClient(cosmosEndpoint, cosmosapi)

    # Read the JSON file
    with open(jsondata, 'r') as file:
        jsondata = json.load(file)

    # Get a reference to the database
    database = client.get_database_client(database)

    # Get a reference to the container
    container = database.get_container_client(container)

    container.create_item(body=jsondata,
        enable_automatic_id_generation=True)