import os
from dotenv import load_dotenv

print(f"Current Working Directory: {os.getcwd()}")
print(f"File Directory: {os.path.dirname(__file__)}")

# Try to find .env file
env_path_1 = os.path.join(os.path.dirname(__file__), '..', 'Desc_workflow', '.env')
env_path_2 = os.path.join(os.path.dirname(__file__), '..', '..', 'Desc_workflow', '.env')
env_path_3 = os.path.join(os.path.dirname(__file__), '.env')
env_path_4 = os.path.join(os.path.dirname(__file__), '..', '.env')

for p in [env_path_1, env_path_2, env_path_3, env_path_4]:
    exists = os.path.exists(p)
    print(f"Checking {p}: {'EXISTS' if exists else 'NOT FOUND'}")
    if exists:
        load_dotenv(p)

print(f"SHOPIFY_SHOP_URL: {os.getenv('SHOPIFY_SHOP_URL')}")
