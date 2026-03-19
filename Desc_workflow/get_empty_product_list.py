import os
import re
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv()

SHOPIFY_SHOP_URL = os.getenv('SHOPIFY_SHOP_URL', '').rstrip('/')
SHOPIFY_ACCESS_TOKEN = os.getenv('SHOPIFY_ACCESS_TOKEN')
PRODUCT_TYPE = "UNKNOWN"  # Change this to scan a different department

GRAPHQL_URL = f"{SHOPIFY_SHOP_URL}/admin/api/2024-01/graphql.json"
HEADERS = {
    "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
    "Content-Type": "application/json"
}

QUERY = """
query($cursor: String) {
  products(first: 250, after: $cursor, query: "product_type:'%s' AND status:ACTIVE") {
    edges {
      node {
        id
        title
        description
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
""" % PRODUCT_TYPE


def fetch_page(cursor=None):
    payload = {"query": QUERY, "variables": {"cursor": cursor}}
    response = requests.post(GRAPHQL_URL, json=payload, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.json()


def get_empty_products():
    empty_products = []
    total_products = 0
    cursor = None
    page = 1

    print(f"Fetching '{PRODUCT_TYPE}' products with empty descriptions...\n")

    while True:
        print(f"  Page {page}...")
        data = fetch_page(cursor)

        edges = data["data"]["products"]["edges"]
        page_info = data["data"]["products"]["pageInfo"]

        for edge in edges:
            node = edge["node"]
            total_products += 1
            if not node.get("description") or node["description"].strip() == "":
                # Strip GID prefix to get plain numeric ID
                raw_id = node["id"].replace("gid://shopify/Product/", "")
                empty_products.append({"id": raw_id, "title": node["title"]})

        if not page_info["hasNextPage"]:
            break

        cursor = page_info["endCursor"]
        page += 1
        time.sleep(1)  # rate limit pause

    return total_products, empty_products


def main():
    total, empty = get_empty_products()

    result = [{
        "finalEmptyProductCount": len(empty),
        "finalTotalProducts": total,
        "emptyProductsList": empty
    }]

    # Save to a sanitized filename based on product type
    safe_name = re.sub(r'[\\/*?:"<>|]', '', PRODUCT_TYPE).strip().replace(' ', '_').lower()
    output_file = f"{safe_name}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    print(f"\nDone.")
    print(f"Total products scanned : {total}")
    print(f"Empty descriptions     : {len(empty)}")
    print(f"Saved to               : {output_file}")


if __name__ == "__main__":
    main()
