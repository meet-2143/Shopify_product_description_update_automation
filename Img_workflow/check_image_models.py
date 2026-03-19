import os
import requests
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv('GEMINI_API_KEY')
url = f'https://generativelanguage.googleapis.com/v1beta/models?key={api_key}'

try:
    r = requests.get(url)
    data = r.json()
    for m in data.get('models', []):
        if 'image' in m['name'].lower() or 'gen' in m['name'].lower():
            print(f"Model: {m['name']} - Ops: {m['supportedGenerationMethods']}")
except Exception as e:
    print(f"Error: {e}")
