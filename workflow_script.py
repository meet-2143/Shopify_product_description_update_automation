import os
import time
import json
import re
import csv
from typing import List, Dict
from datetime import datetime
import requests
from dotenv import load_dotenv

load_dotenv()

# Configuration - all values loaded from .env
SHOPIFY_SHOP_URL = os.getenv('SHOPIFY_SHOP_URL')
SHOPIFY_ACCESS_TOKEN = os.getenv('SHOPIFY_ACCESS_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

INPUT_FILE = 'input_script.js'
PROCESSED_LOG_CSV = 'processed_products_log.csv'

SHOPIFY_GRAPHQL_URL = f"{SHOPIFY_SHOP_URL.rstrip('/')}/admin/api/2024-07/graphql.json"
GRAPHQL_HEADERS = {
    "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
    "Content-Type": "application/json"
}
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

PRODUCT_UPDATE_MUTATION = """
mutation productUpdate($input: ProductInput!) {
  productUpdate(input: $input) {
    product {
      id
      title
      descriptionHtml
    }
    userErrors {
      field
      message
    }
  }
}
"""


class WorkflowProcessor:
    def __init__(self):
        self.counter = 0
        self._init_csv()

    def _init_csv(self):
        if not os.path.exists(PROCESSED_LOG_CSV):
            with open(PROCESSED_LOG_CSV, 'w', newline='', encoding='utf-8') as f:
                csv.writer(f).writerow(['Timestamp', 'Product ID', 'Product Title', 'Status'])

    def load_products_from_file(self) -> List[Dict]:
        try:
            with open(INPUT_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            products = data[0].get('emptyProductsList', [])
            print(f"Loaded {len(products)} products from {INPUT_FILE}")
            return products
        except Exception as e:
            print(f"Error loading input file: {e}")
            return []

    def generate_description(self, product_title: str) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"

        prompt = f"""Write a professional, SEO-optimized product description for the product: {product_title}.

The output must be formatted in clean HTML (using <h2>, <p>, <ul>, <li>, and <strong> tags). Do not include <html> or <body> tags, and do not include the product title as a header.

IMPORTANT FORMATTING RULES:
1. Do NOT use Markdown syntax. Do not use asterisks (**) for bolding.
2. Use <strong> tags for bold text.
3. Do not include <html>, <head>, or <body> tags.
4. Do not include the product title as a header.
5. Do not include code block fences (```html).

Follow this exact structure:
1. <h2>Overview</h2>: A 2-3 sentence paragraph explaining what the product is and its primary benefits.
2. <h2>Key Features</h2>: A bulleted list of 5-6 technical or health benefits.
3. <h2>Size & Packaging</h2>: A bulleted list including the net weight/size (extracted from the title if possible) and packaging details.
4. <h2>Why Choose [Product Name]?</h2>: A short paragraph explaining the unique value proposition.
5. <h2>Usage Suggestions</h2>: A bulleted list of how to use the product.
6. <h2>Storage Instructions</h2>: A short sentence on how to store the product.

Only return the HTML code. No introductory text."""

        try:
            response = requests.post(
                url,
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            text = data['candidates'][0]['content']['parts'][0]['text']
            text = re.sub(r'```html|```', '', text)
            text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text)
            return text.strip()
        except Exception as e:
            print(f"  Error generating description: {e}")
            return ""

    def save_to_supabase(self, product_id: str, title: str, description: str) -> bool:
        """Insert product_id, name and description into Supabase Atta table"""
        url = f"{SUPABASE_URL}/rest/v1/Atta"
        payload = {
            "Product_id": product_id,
            "Product_name": title,
            "Product_description": description
        }
        try:
            response = requests.post(url, json=payload, headers=SUPABASE_HEADERS, timeout=30)
            response.raise_for_status()
            print(f"  Saved to Supabase.")
            return True
        except Exception as e:
            print(f"  Error saving to Supabase: {e}")
            return False

    def update_shopify_product(self, product_id: str, description: str) -> bool:
        """Update Shopify product descriptionHtml via Admin GraphQL API"""
        gid = f"gid://shopify/Product/{product_id}" if not str(product_id).startswith("gid://") else product_id

        payload = {
            "query": PRODUCT_UPDATE_MUTATION,
            "variables": {
                "input": {
                    "id": gid,
                    "descriptionHtml": description
                }
            }
        }

        try:
            response = requests.post(
                SHOPIFY_GRAPHQL_URL,
                json=payload,
                headers=GRAPHQL_HEADERS,
                timeout=30
            )
            response.raise_for_status()
            result = response.json()

            user_errors = result.get("data", {}).get("productUpdate", {}).get("userErrors", [])
            if user_errors:
                for err in user_errors:
                    print(f"  GraphQL userError → {err['field']}: {err['message']}")
                return False

            if "errors" in result:
                print(f"  GraphQL errors: {result['errors']}")
                return False

            return True
        except Exception as e:
            print(f"  Error updating Shopify: {e}")
            return False

    def log(self, product_id: str, title: str, status: str):
        with open(PROCESSED_LOG_CSV, 'a', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow([
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                product_id, title, status
            ])

    def process_product(self, product: Dict) -> bool:
        pid = product['id']
        title = product['title']

        # Step 1: Generate description
        print(f"  Generating description...")
        description = self.generate_description(title)
        if not description:
            self.log(pid, title, 'FAILED - no description')
            return False

        # Step 2: Save to Supabase
        print(f"  Saving to Supabase...")
        if not self.save_to_supabase(pid, title, description):
            self.log(pid, title, 'FAILED - supabase insert')
            return False

        time.sleep(1)

        # Step 3: Update Shopify
        print(f"  Updating Shopify...")
        if not self.update_shopify_product(pid, description):
            self.log(pid, title, 'FAILED - shopify update')
            return False

        self.log(pid, title, 'SUCCESS')
        return True

    def run(self):
        print("Starting workflow...\n")
        products = self.load_products_from_file()
        if not products:
            print("No products to process.")
            return

        total = len(products)
        for i, product in enumerate(products, 1):
            print(f"[{i}/{total}] {product['title']}")
            success = self.process_product(product)

            if success:
                self.counter += 1
                print(f"  Done.")

            if self.counter > 0 and self.counter % 50 == 0:
                print(f"\n  Rate limit pause (12s) after {self.counter} products...")
                time.sleep(12)
            else:
                time.sleep(3)

        print(f"\nWorkflow complete. {self.counter}/{total} products updated.")
        print(f"Log saved to: {PROCESSED_LOG_CSV}")


def main():
    if not os.path.exists(INPUT_FILE):
        print(f"Error: {INPUT_FILE} not found")
        return
    WorkflowProcessor().run()


if __name__ == "__main__":
    main()
