import time
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

print("Starting benchmark...")
start_init = time.time()
from image_workflow import ImageWorkflowProcessor, SHOPIFY_SHOP_URL, SHOPIFY_ACCESS_TOKEN
processor = ImageWorkflowProcessor()
end_init = time.time()
print(f"Initialization took: {end_init - start_init:.2f}s")

def benchmark_shopify_search(title):
    import requests
    url = f"{SHOPIFY_SHOP_URL}/admin/api/2024-01/products.json"
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    params = {"title": title}
    start = time.time()
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        end = time.time()
        print(f"Shopify search took: {end - start:.2f}s (Status: {response.status_code})")
    except Exception as e:
        print(f"Shopify search failed: {e}")

if __name__ == "__main__":
    benchmark_shopify_search("PATANJALI-DAMRU GLASS-400ML")
