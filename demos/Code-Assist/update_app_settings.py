import os
import subprocess
from dotenv import load_dotenv

def update_app_service_settings(resource_group: str, app_name: str):
    load_dotenv()  # Loads .env file into environment variables
    
    # Gather environment variables you want to push
    # (e.g., all variables starting with "AZURE_" or any custom logic you prefer)
    env_vars = {k: v for k, v in os.environ.items() if k.startswith('AZURE_')}
    
    # Build Azure CLI command to set app settings
    setting_args = []
    for key, value in env_vars.items():
        setting_args.append(f"{key}={value}")
    
    if setting_args:
        cli_cmd = [
            "az", "webapp", "config", "appsettings", "set",
            "--resource-group", resource_group,
            "--name", app_name,
            "--settings"
        ] + setting_args
        
        subprocess.run(cli_cmd, check=True)
        print("App settings updated successfully!")
    else:
        print("No environment variables found to update.")

if __name__ == "__main__":
    # Replace with your own resource group and app name
    rg = "myResourceGroup"
    app = "myAppServiceName"
    update_app_service_settings(rg, app)