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
# Load environment variables
env_paths = [
    os.path.join(os.path.dirname(__file__), '..', 'Desc_workflow', '.env'),
    os.path.join(os.path.dirname(__file__), '.env'),
    os.path.join(os.getcwd(), '.env'),
    os.path.join(os.getcwd(), '..', 'Desc_workflow', '.env')
]

for p in env_paths:
    if os.path.exists(p):
        load_dotenv(p, override=True)
        # print(f"DEBUG: Loaded env from {p}")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SHOPIFY_SHOP_URL = os.getenv("SHOPIFY_SHOP_URL")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
SERPAPI_KEY = "41966b0261f39b292bd367b18d5604642586ae7b978562aa6710ae2b9fabfb78"
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://ohtatizvsqrvuxiekhsj.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
IMAGE_GEN_MODEL = os.getenv("IMAGE_GEN_MODEL", "gemini-3.1-flash-image-preview")
IMAGE_GEN_FALLBACK_MODEL = os.getenv("IMAGE_GEN_FALLBACK_MODEL", "imagen-3.0-fast-generate-001")

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

    def generate_ai_image(self, prompt: str) -> str:
        """Generates a product image using Gemini 3.1 Flash Image Preview SDK structure."""
        if not self.genai_client:
            print("  GenAI Client not initialized.")
            return ""

        full_prompt = (
            f"Create a professional product photography image of {prompt}. "
            "Clean white background, high resolution, single product, studio lighting.",
        )

        models_to_try = [IMAGE_GEN_MODEL, IMAGE_GEN_FALLBACK_MODEL]
        
        for model_name in models_to_try:
            if not model_name:
                continue
                
            print(f"  Attempting image generation with model: {model_name}")
            max_retries = 2
            for attempt in range(max_retries):
                try:
                    response = self.genai_client.models.generate_content(
                        model=model_name,
                        contents=full_prompt,
                    )

                    import base64
                    for part in response.parts:
                        if part.inline_data is not None:
                            return base64.b64encode(part.inline_data.data).decode('utf-8')
                    
                    # If we got here but no image data, maybe the model doesn't support inline_data
                    # or it returned a different structure.
                    print(f"    Model {model_name} returned no inline data.")
                    break # Try next model
                except Exception as e:
                    print(f"    Error with {model_name} (attempt {attempt+1}/{max_retries}): {e}")
                    if "503" in str(e) or "429" in str(e):
                        time.sleep(1 * (attempt + 1))
                        continue
                    break # Fatal error for this model, try next one
        
        return ""

    def host_image_in_supabase(self, base64_data: str, product_id: str, product_name: str) -> str:
        """Hosts an AI image in Supabase storage and returns the public URL."""
        if not self.supabase or not base64_data:
            return ""

        try:
            import base64
            image_bytes = base64.b64decode(base64_data)
            filename = f"ai_{product_id}_{uuid.uuid4().hex[:8]}.png"
            filepath = f"ai_generated/{filename}"
            
            # Upload to 'product-images' bucket
            self.supabase.storage.from_("product-images").upload(
                path=filepath,
                file=image_bytes,
                file_options={"content-type": "image/png"}
            )
            
            # 3. Get public URL
            public_url = self.supabase.storage.from_("product-images").get_public_url(filepath)
            
            # 4. Log to database (Optional: don't fail if this fails due to RLS)
            try:
                self.supabase.table("ai_generated_images").insert({
                    "product_id": product_id,
                    "product_name": product_name,
                    "image_url": public_url,
                    "source": "gemini-3.1-flash"
                }).execute()
            except Exception as log_err:
                print(f"  Note: Logging image to Supabase database failed (likely RLS): {log_err}")
                # We still return the public_url so Shopify can be updated
            
            return public_url
        except Exception as e:
            print(f"Error hosting in Supabase storage: {e}")
            return ""

    def update_shopify_product_image(self, product_id: str, image_source: str, is_base64: bool = False) -> bool:
        """Updates the Shopify product image. Using PUT replaces all existing images with this one."""
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
        """Searches Shopify for a product by title and returns its ID."""
        shop_url = SHOPIFY_SHOP_URL.rstrip('/')
        url = f"{shop_url}/admin/api/2024-01/products.json"
        
        headers = {
            "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
            "Content-Type": "application/json"
        }
        
        params = {
            "title": product_name
        }

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self.session.get(url, headers=headers, params=params, timeout=45)
                response.raise_for_status()
                products = response.json().get('products', [])
                
                if products:
                    # Return the ID of the first match
                    return str(products[0]['id'])
                return None
            except Exception as e:
                print(f"  Error searching Shopify for '{product_name}' (attempt {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2)
                    continue
                return None
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
