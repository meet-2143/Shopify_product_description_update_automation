import os
import time
import json
import re
import requests
try:
    import gspread
except ImportError:
    gspread = None
from dotenv import load_dotenv

# -----------------------------------------------------------------------------
# Configuration & Setup
# -----------------------------------------------------------------------------
# Load environment variables from Desc_workflow or current directory
load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'Desc_workflow', '.env'))
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SHOPIFY_SHOP_URL = os.getenv("SHOPIFY_SHOP_URL")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")

# n8n logic: Exclude list from 'Code in JavaScript2'
ALREADY_UPDATED = [
    "PREETHI-ZODIAC MIXER GRINDER",
    "PREETHI-BLUE LEAF MIXES PLATINUM",
    "WOODEN-FORK-100 GM",
    "PATANJALI-DAMRU GLASS-400ML",
    "PITAMBARI-50GM",
    "IRON KADAI NO 2",
    "IRON KADAI NO 10",
    "SAUCE PAN",
    "SANDSI PAKAD",
    "LADDAL PAN 5",
    "FUTURA-3TO7 LT RING",
    "FUTURA-PRESSURE COOKER-5 LITRE",
    "MASTER-CHEF SAUCEPAN-2 LITRE",
    "FUTURA-KADAHI-26 CM",
    "MILTON-THERMOWARE INSULATED CASSEROLE-1500ML",
    "BELON NO 4 GUJRATI",
    "SONEX- MILK PAN- NO.9",
    "FUTURA -HANDI SAUCEPAN - 3 LT",
    "FUTURA-TAVA GRIDDLE-26CM",
    "FUTURA-DEEP FRY PAN-7.5LTR",
    "FUTURA-TAVA-26CM",
    "FUTURA-DOSA TAVA-33CM",
    "FUTURA-FLAT TAVA INDUCTION-30CM",
    "PITAMBARI-SHINING POWDER-150G",
    "COSTCO"
]
CLEAN_EXCLUSION_LIST = [title.strip().upper() for title in ALREADY_UPDATED]


class ImageWorkflowProcessor:
    def __init__(self):
        self.session = requests.Session()
        # You need service_account.json in this folder to use Google Sheets logic
        self.gc = None
        if gspread:
            try:
                self.gc = gspread.service_account(filename=os.path.join(os.path.dirname(__file__), 'service_account.json'))
            except Exception as e:
                pass # Silently proceed if credentials are not set up

    def get_ai_search_params(self, product_title: str) -> dict:
        """Replicates 'AI Agent' node from n8n."""
        # Note: Your API key uniquely supports 'gemini-2.5-flash' in v1beta
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        
        system_message = """You are an Indian grocery e-commerce assistant.
From the user's input, identify the specific grocery product. 

The product can belong to:
- Grocery (food, beverages, staples, snacks, dairy, etc.)
- Home Care (detergents, cleaners, dishwash, toilet cleaner, floor cleaner, etc.)
- Kitchen Expenditure (kitchen utilities & consumables such as aluminium foil, cling wrap, butter paper, tissue paper, garbage bags, scrubbers, storage bags, matchbox, candles, etc.)
- Religious & Pooja Items
(agarbatti/incense sticks, dhoop, sambrani, camphor/kapoor, diya, oil for lamps, pooja samagri kits, ghee for pooja, havan samagri, cotton wicks, sacred powders, etc.)

Home & Decor / Pooja Accessories (Non-Consumable)
(brass bowls, pooja thali, diya, kalash, idols, decorative utensils, lamps, metal décor items)
Return ONLY a raw JSON object with a single best-match variant.
The search_query must be optimized for Google Images to find a clean product shot. 
Include keywords like "product packaging", "white background", and "high resolution".

Format:
{
  "product": "name",
  "variant": {
      "title": "Brand + Variant + Weight",
      "search_query": "Exact Brand Name Product Packaging white background"
  }
}"""

        prompt = f"Product Title: {product_title}"
        
        payload = {
            "contents": [{
                "parts": [{"text": f"{system_message}\n\nTask:\n{prompt}"}]
            }]
        }

        try:
            response = self.session.post(url, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            raw_output = data['candidates'][0]['content']['parts'][0]['text'].strip()
            
            # Replicate 'Code in JavaScript' n8n logic
            raw_output = re.sub(r'```json', '', raw_output)
            raw_output = re.sub(r'```', '', raw_output).strip()
            
            parsed = json.loads(raw_output)
            variant = parsed.get("variant")
            if not variant:
                return None
                
            search_query = variant.get("search_query", "")
            # n8n: query = query.replace(/[^\w\s]/gi, ' ');
            search_query = re.sub(r'[^\w\s]', ' ', search_query).strip()
            
            return {
                "title": variant.get("title", product_title),
                "search_query": search_query
            }
        except Exception as e:
            print(f"  Error in AI Agent for '{product_title}': {e}")
            return None
    
    def fetch_images_from_serpapi(self, search_query: str) -> list:
        """Fetch all image results from SerpAPI."""
        url = "https://serpapi.com/search.json"
        params = {
            "q": search_query,
            "api_key": SERPAPI_KEY,
            "engine": "google_images"
        }
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            images_results = data.get("images_results", [])
            if not images_results:
                return []
                
            return [img.get("original") for img in images_results if img.get("original")]
        except Exception as e:
            print(f"  Error in SerpAPI for '{search_query}': {e}")
            return []

    def update_shopify_product_image(self, product_id: str, image_url: str) -> bool:
        """Replicates 'Update a product' Shopify node from n8n."""
        shop_url = SHOPIFY_SHOP_URL.rstrip('/')
        url = f"{shop_url}/admin/api/2024-01/products/{product_id}.json"
        
        headers = {
            "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
            "Content-Type": "application/json"
        }
        
        payload = {
            "product": {
                "id": product_id,
                "images": [{"src": image_url}]
            }
        }
        
        try:
            response = self.session.put(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"  Error updating Shopify product {product_id}: {e}")
            return False

    def archive_entry_to_sheet(self, title: str, department: str):
        """Replicates 'Append row in sheet' node from n8n."""
        if not self.gc:
            return
        try:
            # Sheet ID from n8n JSON
            sheet = self.gc.open_by_key("1AY9029F_0KUvuKisJdYb-rr0yjjVBr6r9jyCGkfyk60").worksheet("Sheet9")
            # Columns: Product Name={{ $json.title }}, Department={{ $json.product_type }}
            sheet.append_row([title, department])
        except Exception as e:
            print(f"  Error archiving to sheet: {e}")

    def process_item(self, product_id: str, title: str, department: str):
        """Standard processing loop for one item."""
        # n8n: Wait 4 seconds
        time.sleep(4)
        
        print(f"\nEvaluating: '{title}' (ID: {product_id})")
        
        # AI Step
        print("  Generating optimized search params...")
        params = self.get_ai_search_params(title)
        if not params:
            return
        
        # Image Step
        print(f"  Searching image for: '{params['search_query']}'")
        image_url = self.fetch_image_from_serpapi(params['search_query'])
        if not image_url:
            print("  No image found.")
            return
        
        # Shopify Step
        print(f"  Found: {image_url}")
        if product_id:
            if self.update_shopify_product_image(product_id, image_url):
                print("  Shopify updated.")
            else:
                return
        
        # Archival Step
        print("  Archiving to sheet...")
        self.archive_entry_to_sheet(params.get("title", title), department)
        
        # n8n: Wait 3 seconds
        time.sleep(3)

    def run_full_workflow(self):
        """The full automated batching logic from n8n."""
        if not self.gc:
            print("Google Sheets credentials not found. Cannot run full batch workflow.")
            return

        print("Fetching products from Google Sheet...")
        try:
            # Replicates 'Get row(s) in sheet'
            input_sheet = self.gc.open_by_key("1FRFIE2Lduhf8jvUheto65dwkdwedKu0FOT-DrgvAwHQ").worksheet("Sheet3")
            all_records = input_sheet.get_all_records()
            
            # Filter for KITCHEN EXPENDITURE
            items = [r for r in all_records if r.get('productType') == 'KITCHEN EXPENDITURE']
            print(f"Found {len(items)} kitchen items.")
            
            # Replicates 'Code in JavaScript2' (Exclusion Filter)
            items_to_process = []
            for item in items:
                title = str(item.get('title', '')).strip().upper()
                if title and title not in CLEAN_EXCLUSION_LIST:
                    items_to_process.append(item)
            
            print(f"Items after exclusion filter: {len(items_to_process)}")
            
            for item in items_to_process:
                self.process_item(
                    product_id=item.get('id'),
                    title=item.get('title'),
                    department=item.get('productType')
                )
                
            print("\nFull Workflow Complete.")
        except Exception as e:
            print(f"Fatal error in full workflow: {e}")

if __name__ == "__main__":
    import sys
    processor = ImageWorkflowProcessor()

    # Mode 1: Passing a product name as input (as requested by user)
    if len(sys.argv) > 1:
        product_name = " ".join(sys.argv[1:])
        processor.process_item(None, product_name, "Manual Input")
    else:
        # Mode 2: Interactive Prompt or Full Workflow
        print("1. Enter unique product name")
        print("2. Run FULL batch workflow (requires Google Sheets setup)")
        choice = input("Select mode (1/2): ").strip()
        
        if choice == '1':
            name = input("Enter product name: ").strip()
            processor.process_item(None, name, "Manual Input")
        elif choice == '2':
            processor.run_full_workflow()
        else:
            print("Invalid choice.")
