from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
import os
import json
import sys
from typing import Union, Any

# Add parent directory to path so we can import image_workflow
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from image_workflow import ImageWorkflowProcessor, SHOPIFY_SHOP_URL, SHOPIFY_ACCESS_TOKEN
import requests
from concurrent.futures import ThreadPoolExecutor

app = FastAPI()

# Enable CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

processor = ImageWorkflowProcessor()
executor = ThreadPoolExecutor(max_workers=5)

STATS_FILE = os.path.join(os.path.dirname(__file__), "..", "stats.json")

def get_stats():
    if not os.path.exists(STATS_FILE):
        return {"total_generated": 0, "total_approved": 0}
    try:
        with open(STATS_FILE, "r") as f:
            return json.load(f)
    except:
        return {"total_generated": 0, "total_approved": 0}

def update_stats(generated=0, approved=0):
    stats = get_stats()
    stats["total_generated"] += generated
    stats["total_approved"] += approved
    try:
        with open(STATS_FILE, "w") as f:
            json.dump(stats, f)
    except:
        pass # Vercel functions are read-only except for /tmp, so this might fail in lambda

def search_product(title):
    url = f"{SHOPIFY_SHOP_URL}/admin/api/2024-01/products.json"
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    params = {
        "title": title
    }
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            products = response.json().get('products', [])
            return products[0] if products else None
        else:
            print(f"Error searching Shopify: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"Exception searching Shopify: {e}")
        return None

class GenerateRequest(BaseModel):
    product_name: str

class ApproveRequest(BaseModel):
    product_id: Any
    product_name: str = ""
    image_url: str
    is_base64: bool = False

    @field_validator('product_id', mode='before')
    @classmethod
    def ensure_string(cls, v):
        return str(v)

@app.get("/", response_class=HTMLResponse)
def read_index():
    index_path = os.path.join(os.path.dirname(__file__), "..", "static", "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "index.html not found"

@app.get("/api/stats")
def get_all_stats():
    return get_stats()

@app.post("/api/generate")
def generate_images(request: GenerateRequest):
    # 1. Run Search Query generation and Shopify search in parallel
    with ThreadPoolExecutor() as exe:
        future_params = exe.submit(processor.get_ai_search_params, request.product_name)
        future_shopify = exe.submit(search_product, request.product_name)
        
        params = future_params.result()
        found_res = future_shopify.result()

    if not params:
        raise HTTPException(status_code=500, detail="Gemini failed to generate search query")
    
    found_id = found_res['id'] if found_res else None
    if not found_id:
        print(f"  Warning: Product '{request.product_name}' not found in Shopify.")

    # 2. Run SerpAPI search
    with ThreadPoolExecutor() as exe:
        future_serp = exe.submit(processor.fetch_images_from_serpapi, params['search_query'])
        images = future_serp.result()
    
    if not images:
        # We don't fail here if no images are found, as the user might want to generate AI image later
        print(f"  Warning: No search images found for '{request.product_name}'")
    
    update_stats(generated=1)
    
    return {
        "title": params['title'],
        "search_query": params['search_query'],
        "images": images,
        "product_id": found_id,
        "stats": get_stats()
    }

@app.post("/api/generate-ai")
def generate_ai(request: GenerateRequest):
    # This only generates the AI image
    ai_image = processor.generate_ai_image(request.product_name)
    if not ai_image:
        raise HTTPException(status_code=500, detail="Gemini failed to generate AI image")
    
    return {
        "ai_image": ai_image
    }

@app.post("/api/approve")
def approve_image(request: ApproveRequest):
    try:
        final_image_source = request.image_url
        final_is_base64 = request.is_base64
        
        # If it's an AI image (base64), host it in Supabase first to get a public URL
        if request.is_base64:
            print(f"  Hosting AI image for product {request.product_id} in Supabase...")
            hosted_url = processor.host_image_in_supabase(
                base64_data=request.image_url,
                product_id=request.product_id,
                product_name=request.product_name
            )
            if not hosted_url:
                raise HTTPException(status_code=500, detail="Failed to host AI image in Supabase storage")
            
            final_image_source = hosted_url
            final_is_base64 = False # Now it's a URL
            print(f"  Successfully hosted at: {hosted_url}")

        success = processor.update_shopify_product_image(
            product_id=request.product_id, 
            image_source=final_image_source, 
            is_base64=final_is_base64
        )
        if not success:
            raise HTTPException(status_code=500, detail="Shopify update failed")
        
        update_stats(approved=1)
        return {"status": "success", "message": "Shopify product updated successfully", "stats": get_stats()}
    except Exception as e:
        print(f"Error in approve_image: {e}")
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))

# For local testing
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
