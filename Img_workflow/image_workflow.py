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

from google import genai
from PIL import Image
import io
import uuid
from supabase import create_client, Client

# -----------------------------------------------------------------------------
# Configuration & Setup
# -----------------------------------------------------------------------------
# Load environment variables from Desc_workflow or current directory
load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'Desc_workflow', '.env'))
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SHOPIFY_SHOP_URL = os.getenv("SHOPIFY_SHOP_URL")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
SERPAPI_KEY = "41966b0261f39b292bd367b18d5604642586ae7b978562aa6710ae2b9fabfb78"
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://ohtatizvsqrvuxiekhsj.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

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
        self.gemini_api_key = GEMINI_API_KEY
        
        # New SDK Client
        self.genai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
        
        # Supabase Client
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None
        
        # You need service_account.json in this folder to use Google Sheets logic
        self.gc = None
        if gspread:
            try:
                self.gc = gspread.service_account(filename=os.path.join(os.path.dirname(__file__), 'service_account.json'))
            except Exception as e:
                pass # Silently proceed if credentials are not set up


    def fetch_images_from_serpapi(self, search_query: str, start: int = 0) -> list:
        """Fetch image results from SerpAPI with pagination."""
        url = "https://serpapi.com/search.json"
        params = {
            "q": search_query,
            "api_key": SERPAPI_KEY,
            "engine": "google_images",
            "ijn": start // 100, # SerpApi uses ijn for page index (0=0-99, 1=100-199)
            "start": start
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



    def update_shopify_product_image(self, product_id: str, image_source: str, is_base64: bool = False) -> bool:
        """Updates the Shopify product image. Using PUT replaces all existing images with this one."""
        if not SHOPIFY_SHOP_URL:
            print("  Error: SHOPIFY_SHOP_URL is not set. Check your .env file.")
            return False
        shop_url = SHOPIFY_SHOP_URL.rstrip('/')
        # Use Product PUT endpoint to replace existing images
        url = f"{shop_url}/admin/api/2024-01/products/{product_id}.json"
        
        headers = {
            "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
            "Content-Type": "application/json"
        }
        
        # Structure for replacing images: send an array with just the new image
        payload = {
            "product": {
                "id": product_id,
                "images": [
                    {
                        "attachment": image_source if is_base64 else None,
                        "src": None if is_base64 else image_source
                    }
                ]
            }
        }
        
        # Clean up nulls
        if is_base64:
            del payload["product"]["images"][0]["src"]
        else:
            del payload["product"]["images"][0]["attachment"]

        try:
            # PUT replaces the images collection instead of POST which appends
            response = requests.put(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"  Error updating Shopify product {product_id}: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"  Response: {e.response.text}")
            return False

    def find_shopify_product_by_name(self, product_name: str) -> str:
        """Searches Shopify for a product by title and returns its ID.

        Tries multiple strategies since Shopify's title filter is a prefix search
        and stored titles may not match the full input name:
        1. Full name as-is
        2. Each hyphen-separated part (longest first)
        """
        if not SHOPIFY_SHOP_URL:
            print("  Error: SHOPIFY_SHOP_URL is not set. Check your Vercel Environment Variables.")
            return None
            
        shop_url = SHOPIFY_SHOP_URL.strip().rstrip('/')
        if not shop_url.startswith('http'):
            shop_url = f"https://{shop_url}"
        
        print(f"  Searching Shopify for '{product_name}' at {shop_url}...")
        url = f"{shop_url}/admin/api/2024-01/products.json"
        headers = {
            "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
            "Content-Type": "application/json"
        }

        # Build list of queries to try: full name first, then each part by length (desc)
        parts = [p.strip() for p in product_name.split('-') if len(p.strip()) >= 3]
        parts.sort(key=len, reverse=True)
        queries = [product_name] + parts

        for query in queries:
            for attempt in range(3):
                try:
                    response = self.session.get(url, headers=headers, params={"title": query}, timeout=45)
                    response.raise_for_status()
                    products = response.json().get('products', [])
                    if products:
                        matched = products[0]
                        print(f"  Found product via query '{query}': {matched['id']} | {matched['title']}")
                        return str(matched['id'])
                    break  # query returned 0 results, try next query
                except Exception as e:
                    print(f"  Error searching Shopify (attempt {attempt+1}/3): {e}")
                    if attempt < 2:
                        time.sleep(2)

        print(f"  No Shopify product found for '{product_name}'")
        return None

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
        """Standard processing loop for one item (AI-free)."""
        # Wait 4 seconds
        time.sleep(4)
        
        print(f"\nEvaluating: '{title}' (ID: {product_id})")
        
        # Image Step (Search using raw title instead of AI params)
        print(f"  Searching image for: '{title}'")
        images = self.fetch_images_from_serpapi(title)
        if not images:
            print("  No image found.")
            return
        
        image_url = images[0] # Take first result for automated workflow
        
        # Shopify Step
        print(f"  Found: {image_url}")
        if product_id:
            if self.update_shopify_product_image(product_id, image_url):
                print("  Shopify updated.")
            else:
                return
        
        # Archival Step
        print("  Archiving to sheet...")
        self.archive_entry_to_sheet(title, department)
        
        # Wait 3 seconds
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
