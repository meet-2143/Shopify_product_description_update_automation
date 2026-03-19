import os
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'Desc_workflow', '.env'))
SERPAPI_KEY = os.getenv("SERPAPI_KEY")

def test_serpapi(query):
    url = "https://serpapi.com/search.json"
    params = {
        "q": query,
        "engine": "google_images",
        "api_key": SERPAPI_KEY
    }
    print(f"Searching SerpAPI for: {query}")
    try:
        response = requests.get(url, params=params, timeout=10)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            results = response.json().get('images_results', [])
            print(f"Found {len(results)} images.")
            for i, img in enumerate(results[:3]):
                print(f"  {i+1}: {img.get('original')}")
            return results
        else:
            print(f"Error: {response.text}")
            return None
    except Exception as e:
        print(f"Exception: {e}")
        return None

if __name__ == "__main__":
    test_serpapi("FRUITA VITAL NECTOR KINNOW ORANGE packaging white background")
