#!/usr/bin/env python3
"""
Sobeys API Scraper
Uses Algolia Search API for fast, reliable product data extraction
Follows the Rooney Method: API First → DOM Fallback
"""

import logging
import time
from typing import List, Optional, Dict, Any
import httpx
from pathlib import Path

from scrapers.base import ProductRecord, BaseScraper
from scrapers.common import get_iso_timestamp


class SobeysAPIScraper(BaseScraper):
    """
    API-based scraper for Sobeys using Algolia Search.
    Much faster and more reliable than DOM scraping.
    """

    # Algolia API configuration (discovered from Network tab)
    ALGOLIA_APP_ID = "ACSYSHF8AU"
    ALGOLIA_API_KEY = "fe555974f588b3e76ad0f1c548113b22"
    ALGOLIA_BASE_URL = "https://acsyshf8au-dsn.algolia.net"
    ALGOLIA_INDEX = "dxp_product_en"

    # Sobeys internal API
    SOBEYS_API_BASE = "https://www.sobeys.com/api"

    def __init__(self, config_path, project_root, headless=True):
        super().__init__(config_path, project_root)
        self.client = None
        self.store_number = "0320"  # Default store (Airdrie)
        # API scraper doesn't need headless param but accepts it for compatibility

    def _get_headers(self) -> Dict[str, str]:
        """Get headers for API requests"""
        return {
            "x-algolia-api-key": self.ALGOLIA_API_KEY,
            "x-algolia-application-id": self.ALGOLIA_APP_ID,
            "x-algolia-agent": "Algolia for JavaScript (5.46.2); Search (5.46.2); Browser",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Origin": "https://www.sobeys.com",
            "Referer": "https://www.sobeys.com/",
        }

    def search_products(self, query: str, max_pages: int = 1) -> List[ProductRecord]:
        """
        Search for products using Algolia API

        Args:
            query: Search term (e.g., "milk", "bread")
            max_pages: Maximum number of pages to scrape

        Returns:
            List of ProductRecord objects
        """
        products = []

        with httpx.Client(headers=self._get_headers(), timeout=30.0) as client:
            self.client = client

            # First, get featured/campaign products (optional, often has sales)
            try:
                featured_products = self._fetch_featured_products(query)
                logging.info(f"Found {len(featured_products)} featured products")
                products.extend(featured_products)
            except Exception as e:
                logging.warning(f"Could not fetch featured products: {e}")

            # Main product search via Algolia
            page = 0
            while page < max_pages:
                try:
                    logging.info(f"Fetching page {page + 1}/{max_pages} for query '{query}'...")

                    page_products = self._search_algolia(query, page)

                    if not page_products:
                        logging.info(f"No more products found on page {page + 1}")
                        break

                    logging.info(f"Extracted {len(page_products)} products from page {page + 1}")
                    products.extend(page_products)

                    page += 1

                    # Polite delay between requests
                    if page < max_pages:
                        time.sleep(1.5)

                except Exception as e:
                    logging.error(f"Error fetching page {page + 1}: {e}")
                    break

        # Deduplicate by unique_id (objectID from Algolia)
        unique_products = {}
        for product in products:
            # Use the _unique_id we attached during parsing
            unique_id = getattr(product, '_unique_id', None)
            if unique_id and unique_id not in unique_products:
                unique_products[unique_id] = product
            elif not unique_id:
                # Fallback: use name+price as key
                key = f"{product.name}_{product.price}"
                if key not in unique_products:
                    unique_products[key] = product

        logging.info(f"Total unique products: {len(unique_products)}")
        return list(unique_products.values())

    def _search_algolia(self, query: str, page: int = 0, hits_per_page: int = 24) -> List[ProductRecord]:
        """
        Search Algolia index for products

        Args:
            query: Search term
            page: Page number (0-indexed)
            hits_per_page: Results per page

        Returns:
            List of ProductRecord objects
        """
        url = f"{self.ALGOLIA_BASE_URL}/1/indexes/*/queries"

        # Algolia multi-index search body
        body = {
            "requests": [
                {
                    "indexName": self.ALGOLIA_INDEX,
                    "params": f"query={query}&hitsPerPage={hits_per_page}&page={page}"
                }
            ]
        }

        logging.debug(f"Algolia request: {body}")

        response = self.client.post(url, json=body)
        response.raise_for_status()

        data = response.json()
        logging.debug(f"Algolia response keys: {data.keys()}")

        # Extract products from response
        if "results" in data and len(data["results"]) > 0:
            hits = data["results"][0].get("hits", [])
            logging.info(f"Algolia returned {len(hits)} hits")

            products = []
            for hit in hits:
                product = self._parse_algolia_product(hit)
                if product:
                    products.append(product)

            return products

        return []

    def _fetch_featured_products(self, query: str) -> List[ProductRecord]:
        """
        Fetch featured/campaign products
        These often include sale items and promotions
        """
        url = f"{self.SOBEYS_API_BASE}/featuredCampaignDataForSearch"

        # This endpoint might need specific parameters
        # For now, we'll just try to fetch it
        response = self.client.get(url)
        response.raise_for_status()

        data = response.json()

        # Parse featured products
        if "data" in data and "products" in data["data"]:
            hits = data["data"]["products"].get("hits", [])
            logging.info(f"Featured API returned {len(hits)} products")

            products = []
            for hit in hits:
                product = self._parse_algolia_product(hit)
                if product:
                    products.append(product)

            return products

        return []

    def _parse_algolia_product(self, hit: Dict[str, Any]) -> Optional[ProductRecord]:
        """
        Parse an Algolia hit into a ProductRecord

        Args:
            hit: Product data from Algolia API

        Returns:
            ProductRecord or None if parsing fails
        """
        try:
            # Extract required fields
            name = hit.get("name") or hit.get("title") or hit.get("pageSlug", "Unknown")

            # Price - Algolia returns it as a number
            price = hit.get("price")
            if price is not None:
                price = float(price)

            # Brand
            brand = hit.get("brand") or hit.get("manufacturer")

            # Size/Weight - maps to size_text
            size_text = hit.get("weight") or hit.get("size") or hit.get("priceQuantity")
            if size_text and hit.get("uom"):
                size_text = f"{size_text} {hit.get('uom')}"

            # Unit price
            unit_price = None
            unit_price_uom = None
            if hit.get("unitPrice"):
                unit_price = float(hit.get("unitPrice"))
                unit_price_uom = hit.get("uom")

            # Category - use hierarchical categories if available → category_path
            category_path = None
            if "hierarchicalCategories" in hit:
                hier_cats = hit["hierarchicalCategories"]
                if "lvl2" in hier_cats and hier_cats["lvl2"]:
                    # lvl2 is most specific: "Fresh Fruits & Vegetables > Fruits > Tropical"
                    category_path = hier_cats["lvl2"][0] if isinstance(hier_cats["lvl2"], list) else hier_cats["lvl2"]
                elif "lvl1" in hier_cats and hier_cats["lvl1"]:
                    category_path = hier_cats["lvl1"][0] if isinstance(hier_cats["lvl1"], list) else hier_cats["lvl1"]
                elif "lvl0" in hier_cats and hier_cats["lvl0"]:
                    category_path = hier_cats["lvl0"][0] if isinstance(hier_cats["lvl0"], list) else hier_cats["lvl0"]
            elif "categories" in hit:
                # Fallback to simple categories array
                cats = hit["categories"]
                if cats:
                    category_path = " > ".join(cats) if isinstance(cats, list) else str(cats)

            # Stock status
            in_stock = hit.get("inStock", True)
            availability = "in_stock" if in_stock else "out_of_stock"

            # UPC → external_id
            # Note: UPC field can be comma-separated list, take first one
            upc_raw = hit.get("upc") or hit.get("gtin") or hit.get("articleNumber")
            if upc_raw and isinstance(upc_raw, str) and ',' in upc_raw:
                external_id = upc_raw.split(',')[0].strip()
            else:
                external_id = upc_raw

            # Use objectID as unique identifier (fallback to article number)
            unique_id = hit.get("objectID") or hit.get("articleNumber") or external_id

            # Image
            image_url = None
            if "images" in hit and hit["images"]:
                image_url = hit["images"][0] if isinstance(hit["images"], list) else hit["images"]
            elif "image" in hit:
                image_url = hit["image"]

            # Product URL → source_url
            source_url = "https://www.sobeys.com"  # Default
            if "pageSlug" in hit:
                source_url = f"https://www.sobeys.com/product/{hit['pageSlug']}"

            # Create ProductRecord with correct schema
            record = ProductRecord(
                store="Sobeys",
                site_slug="sobeys",
                source_url=source_url,
                scrape_ts=get_iso_timestamp(),
                external_id=external_id,
                name=name,
                brand=brand,
                size_text=size_text,
                price=price,
                currency="CAD",
                unit_price=unit_price,
                unit_price_uom=unit_price_uom,
                image_url=image_url,
                category_path=category_path,
                availability=availability,
                raw_source=hit  # Store the full API response for reference
            )

            # Store unique_id as an attribute for deduplication
            record._unique_id = unique_id

            # Validation
            if not record.name or record.name == "Unknown":
                logging.debug(f"Skipping product with no name: {hit}")
                return None

            return record

        except Exception as e:
            logging.error(f"Error parsing product: {e}")
            logging.debug(f"Problem hit: {hit}")
            return None

    def scrape_product_page(self, product_url: str) -> Optional[ProductRecord]:
        """
        API scraper doesn't scrape individual product pages
        Product data comes from search API
        """
        raise NotImplementedError("API scraper gets all data from search endpoint")

    def scrape_category(self, category_url: str, max_pages: int = 1) -> List[ProductRecord]:
        """
        API scraper doesn't use category URLs
        Instead, it extracts category from URL and searches
        """
        # Extract search term from URL if possible
        # For now, just raise NotImplementedError
        raise NotImplementedError("Use search_products() with a query instead")

    def scrape_search(self, query: str, max_pages: int = 1) -> List[ProductRecord]:
        """
        Scrape search results (wrapper for search_products)
        """
        return self.search_products(query, max_pages)


def main():
    """Run comprehensive scraping test"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    # Setup paths
    project_root = Path(__file__).parent.parent.parent
    config_path = project_root / "configs" / "sobeys.json"

    scraper = SobeysAPIScraper(config_path, project_root)

    # Collect products for multiple search terms
    all_products = []
    queries = ["milk", "bread", "eggs"]

    for query in queries:
        logging.info(f"\n{'='*60}")
        logging.info(f"Searching for: {query}")
        logging.info(f"{'='*60}")

        products = scraper.search_products(query, max_pages=5)

        logging.info(f"Found {len(products)} unique products for '{query}'")
        all_products.extend(products)

    # Deduplicate across all queries
    unique_products = {}
    for product in all_products:
        # Use the _unique_id we attached during parsing
        unique_id = getattr(product, '_unique_id', None)
        key = unique_id or f"{product.name}_{product.price}"
        if key not in unique_products:
            unique_products[key] = product

    final_products = list(unique_products.values())
    logging.info(f"\n{'='*60}")
    logging.info(f"TOTAL UNIQUE PRODUCTS: {len(final_products)}")
    logging.info(f"{'='*60}")

    # Show sample with all fields
    if final_products:
        logging.info("\nSample products (showing all fields):")
        for product in final_products[:5]:
            logging.info(f"\n  Name: {product.name}")
            logging.info(f"  Brand: {product.brand}")
            logging.info(f"  Price: ${product.price} {product.currency}")
            logging.info(f"  Size: {product.size_text}")
            logging.info(f"  Unit Price: {product.unit_price} {product.unit_price_uom}")
            logging.info(f"  Category: {product.category_path}")
            logging.info(f"  Availability: {product.availability}")
            logging.info(f"  UPC: {product.external_id}")

    # Save to proper output file
    output_dir = Path("data/raw/sobeys")
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / "sobeys_products.jsonl"

    import json
    with open(output_file, 'w', encoding='utf-8') as f:
        for product in final_products:
            f.write(json.dumps(product.to_dict()) + '\n')

    logging.info(f"\n{'='*60}")
    logging.info(f"✅ SCRAPING COMPLETE")
    logging.info(f"{'='*60}")
    logging.info(f"Saved {len(final_products)} products to: {output_file}")
    logging.info(f"File size: {output_file.stat().st_size / 1024:.2f} KB")


if __name__ == "__main__":
    main()
