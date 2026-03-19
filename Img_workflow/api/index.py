from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os
import json
import sys

# Add parent directory to path so we can import root image_workflow
ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from image_workflow import ImageWorkflowProcessor, GEMINI_API_KEY
print(f"DEBUG: Loaded root image_workflow. GEMINI_API_KEY present: {GEMINI_API_KEY is not None}")

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


class GenerateRequest(BaseModel):
    product_name: str

class ApproveRequest(BaseModel):
    product_id: str
    product_name: str = ""
    image_url: str
    is_base64: bool = False

@app.get("/", response_class=HTMLResponse)
async def read_index():
    index_path = os.path.join(os.path.dirname(__file__), "..", "static", "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "index.html not found"

@app.get("/api/stats")
async def get_all_stats():
    return get_stats()

@app.post("/api/generate")
async def generate_images(request: GenerateRequest):
    params = processor.get_ai_search_params(request.product_name)
    if not params:
        raise HTTPException(status_code=500, detail="Gemini failed to generate search query")
    
    # 2. Get product product_id from Shopify search
    found_id = processor.find_shopify_product_by_name(request.product_name)
    
    # 3. Get image list from SerpAPI
    images = processor.fetch_images_from_serpapi(params['search_query'])
    
    # 4. Generate AI image with Gemini 2.5 Pro / Imagen
    ai_image = processor.generate_ai_image(request.product_name)
    
    if not images and not ai_image:
        raise HTTPException(status_code=500, detail="Failed to find or generate any images")
    
    update_stats(generated=1)
    
    return {
        "title": params['title'],
        "search_query": params['search_query'],
        "images": images,
        "ai_image": ai_image,
        "product_id": found_id,
        "stats": get_stats()
    }

@app.post("/api/approve")
async def approve_image(request: ApproveRequest):
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
