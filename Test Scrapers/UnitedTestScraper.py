"""
United Airlines MileagePlus LLM-Powered Scraper
Uses Playwright + Ollama (100% FREE, runs locally)
Requires: pip install playwright ollama markdownify pydantic
Then run: playwright install chromium
Then run: ollama pull llama3.1
"""

import asyncio
import json
import os
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, ValidationError
from playwright.async_api import async_playwright
from markdownify import markdownify as md
import ollama

# ============================================================================
# DATA MODELS (Pydantic for validation)
# ============================================================================

class Partner(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    category: str = Field(..., description="airline, hotel, car_rental, or other")
    
class Promotion(BaseModel):
    title: str
    description: Optional[str] = None
    validity: Optional[str] = None
    miles_bonus: Optional[str] = None

class AwardRoute(BaseModel):
    route: str = Field(..., description="e.g. 'US to Europe'")
    economy_saver: Optional[str] = None
    economy_standard: Optional[str] = None
    business_saver: Optional[str] = None
    business_standard: Optional[str] = None

class EarningRule(BaseModel):
    category: str = Field(..., description="e.g. 'base_earning', 'elite_bonus', 'partner_earning'")
    description: str
    rate: Optional[str] = None

# ============================================================================
# SCRAPER CLASS
# ============================================================================

class UnitedLLMScraper:
    def __init__(self, model: str = "llama3.1"):
        """Initialize with Ollama model"""
        self.model = model
        self.browser = None
        self.context = None
        self.playwright = None
        
        # Test Ollama connection
        try:
            ollama.list()
            print(f"✓ Connected to Ollama (using model: {model})")
        except Exception as e:
            print(f"✗ Cannot connect to Ollama: {e}")
            print("\nMake sure Ollama is running:")
            print("  brew services start ollama")
            print("  OR run: ollama serve")
            raise
        
    async def __aenter__(self):
        """Async context manager entry"""
        self.playwright = await async_playwright().start()
        
        # Launch browser with stealth settings
        self.browser = await self.playwright.chromium.launch(
            headless=False,  # Run visible to avoid detection
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox'
            ]
        )
        
        # Create context with realistic settings
        self.context = await self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            locale='en-US',
            timezone_id='America/Los_Angeles',
            # Add realistic browser features
            extra_http_headers={
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Upgrade-Insecure-Requests': '1'
            }
        )
        
        # Remove automation indicators
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
    
    async def fetch_page_markdown(self, url: str, wait_selector: str = None) -> str:
        """
        Fetch a page and convert to clean markdown
        
        Args:
            url: URL to fetch
            wait_selector: Optional CSS selector to wait for before extracting
        
        Returns:
            Clean markdown text
        """
        print(f"  → Fetching: {url}")
        
        page = await self.context.new_page()
        
        try:
            # Navigate with more lenient settings
            await page.goto(url, wait_until='load', timeout=60000)
            
            # Wait for page to be interactive
            print(f"  → Waiting for content to load...")
            await page.wait_for_timeout(5000)  # Give page time to settle
            
            # Optional: wait for specific content
            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=15000)
                except:
                    print(f"  ⚠ Selector '{wait_selector}' not found, continuing anyway")
            
            # Scroll to load lazy content
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)
            
            # Get the rendered HTML
            html = await page.content()
            
            # Convert to markdown (removes scripts, styles, etc.)
            markdown = md(html, heading_style="ATX", bullets="-")
            
            # Clean up excessive whitespace
            markdown = "\n".join(line for line in markdown.split("\n") if line.strip())
            
            print(f"  ✓ Extracted {len(markdown)} characters")
            return markdown
            
        except Exception as e:
            print(f"  ✗ Error fetching page: {e}")
            # Try to get whatever content we can
            try:
                html = await page.content()
                markdown = md(html, heading_style="ATX", bullets="-")
                print(f"  ⚠ Got partial content: {len(markdown)} characters")
                return markdown
            except:
                return ""
        finally:
            await page.close()
    
    def extract_with_llm(
        self, 
        markdown: str, 
        extraction_prompt: str,
        model_class: BaseModel = None
    ):
        """
        Use Ollama to extract structured data from markdown
        
        Args:
            markdown: Clean markdown text
            extraction_prompt: Instructions for what to extract
            model_class: Optional Pydantic model for validation
        
        Returns:
            Parsed JSON data or raw text
        """
        print(f"  → Sending {len(markdown)} chars to Ollama ({self.model})...")
        
        # Truncate if too long (Ollama has context limits)
        if len(markdown) > 100000:
            print(f"  ⚠ Truncating markdown from {len(markdown)} to 100000 chars")
            markdown = markdown[:100000] + "\n\n[Content truncated...]"
        
        system_prompt = """You are a data extraction specialist. Your job is to:
1. Read the provided markdown content carefully
2. Extract ONLY the information requested
3. Return valid JSON that matches the requested schema
4. If data is missing or unclear, use null instead of guessing
5. Never hallucinate or invent data that isn't in the source
6. Return ONLY the JSON array or object, no explanation or markdown formatting"""

        message = f"""{extraction_prompt}

SOURCE CONTENT:
{markdown}

Return your response as valid JSON only, with no markdown formatting or explanation."""

        try:
            # Call Ollama
            response = ollama.chat(
                model=self.model,
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': message}
                ],
                options={
                    'temperature': 0.1,  # Low temperature for consistent extraction
                    'num_predict': 4000  # Max tokens to generate
                }
            )
            
            result_text = response['message']['content'].strip()
            
            # Remove markdown code fences if present
            if result_text.startswith("```"):
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]
                result_text = result_text.strip()
            
            # Parse JSON
            data = json.loads(result_text)
            
            # Validate with Pydantic if model provided
            if model_class:
                if isinstance(data, list):
                    validated = [model_class(**item) for item in data]
                    data = [v.model_dump() for v in validated]
                else:
                    validated = model_class(**data)
                    data = validated.model_dump()
            
            print(f"  ✓ Extracted and validated data")
            return data
            
        except json.JSONDecodeError as e:
            print(f"  ✗ JSON parsing error: {e}")
            print(f"  Raw response: {result_text[:500]}")
            return None
        except ValidationError as e:
            print(f"  ✗ Validation error: {e}")
            return None
        except Exception as e:
            print(f"  ✗ Extraction error: {e}")
            return None
    
    # ========================================================================
    # SCRAPING METHODS
    # ========================================================================
    
    async def scrape_partners(self) -> dict:
        """Scrape partner airlines, hotels, and car rentals"""
        print("\n" + "="*70)
        print("SCRAPING PARTNERS")
        print("="*70)
        
        url = "https://www.united.com/en/us/fly/mileageplus/partners.html"
        markdown = await self.fetch_page_markdown(url, wait_selector="main")
        
        extraction_prompt = """Extract ALL partner companies mentioned on this page.
For each partner, identify:
- name: The company name
- category: One of "airline", "hotel", "car_rental", or "other"

Return as JSON array: [{"name": "...", "category": "..."}]"""
        
        partners = self.extract_with_llm(markdown, extraction_prompt, Partner)
        
        if partners:
            # Group by category
            grouped = {
                'airlines': [p for p in partners if p['category'] == 'airline'],
                'hotels': [p for p in partners if p['category'] == 'hotel'],
                'car_rentals': [p for p in partners if p['category'] == 'car_rental'],
                'other': [p for p in partners if p['category'] == 'other']
            }
            
            print(f"\n  Found {len(partners)} total partners:")
            for cat, items in grouped.items():
                print(f"    • {cat}: {len(items)}")
            
            return grouped
        
        return None
    
    async def scrape_promotions(self) -> List[dict]:
        """Scrape current promotions and offers"""
        print("\n" + "="*70)
        print("SCRAPING PROMOTIONS")
        print("="*70)
        
        url = "https://www.united.com/en/us/fly/mileageplus/promotions.html"
        markdown = await self.fetch_page_markdown(url, wait_selector="main")
        
        extraction_prompt = """Extract ALL current promotions and special offers.
For each promotion include:
- title: The promotion name/headline
- description: Brief description of the offer
- validity: Expiration date or validity period if mentioned
- miles_bonus: Miles amount if mentioned (e.g. "2X miles", "5,000 bonus miles")

Return as JSON array."""
        
        promotions = self.extract_with_llm(markdown, extraction_prompt, Promotion)
        
        if promotions:
            print(f"\n  Found {len(promotions)} promotions")
            for promo in promotions[:3]:
                print(f"\n    • {promo['title']}")
                if promo.get('miles_bonus'):
                    print(f"      {promo['miles_bonus']}")
        
        return promotions
    
    async def scrape_earning_rules(self) -> List[dict]:
        """Scrape how to earn miles"""
        print("\n" + "="*70)
        print("SCRAPING EARNING RULES")
        print("="*70)
        
        url = "https://www.united.com/en/us/fly/mileageplus/miles/earn.html"
        markdown = await self.fetch_page_markdown(url, wait_selector="main")
        
        extraction_prompt = """Extract all rules about how to earn MileagePlus miles.
Focus on:
- Base earning rates (miles per dollar, miles per flight)
- Elite status bonuses
- Partner earning rates
- Credit card bonuses
- Other earning methods

For each rule provide:
- category: Type of earning (e.g. "base_earning", "elite_bonus", "partner_earning")
- description: Clear explanation
- rate: The earning rate if specified (e.g. "5x miles", "50% bonus")

Return as JSON array."""
        
        rules = self.extract_with_llm(markdown, extraction_prompt, EarningRule)
        
        if rules:
            print(f"\n  Found {len(rules)} earning rules")
            for rule in rules[:5]:
                print(f"\n    • {rule['category']}: {rule['description'][:80]}...")
        
        return rules
    
    async def scrape_award_chart(self) -> List[dict]:
        """Scrape award travel pricing"""
        print("\n" + "="*70)
        print("SCRAPING AWARD CHART")
        print("="*70)
        
        url = "https://www.united.com/en/us/fly/mileageplus/awards/travel/award-travel.html"
        markdown = await self.fetch_page_markdown(url, wait_selector="main")
        
        extraction_prompt = """Extract award pricing information for different routes.
Look for tables or listings showing:
- Routes (e.g. "US to Europe", "Within US")
- Saver award prices (economy and business)
- Standard award prices (economy and business)

For each route provide:
- route: The route description
- economy_saver: Saver economy price in miles
- economy_standard: Standard economy price in miles  
- business_saver: Saver business price in miles
- business_standard: Standard business price in miles

Use null if a price isn't mentioned. Return as JSON array."""
        
        awards = self.extract_with_llm(markdown, extraction_prompt, AwardRoute)
        
        if awards:
            print(f"\n  Found {len(awards)} award routes")
            for award in awards[:3]:
                print(f"\n    • {award['route']}")
                if award.get('economy_saver'):
                    print(f"      Economy Saver: {award['economy_saver']}")
        
        return awards
    
    async def run_full_scrape(self) -> dict:
        """Run complete scraping operation"""
        print("\n" + "="*70)
        print(f"UNITED AIRLINES LLM SCRAPER")
        print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*70)
        
        results = {
            'metadata': {
                'timestamp': datetime.now().isoformat(),
                'scraper_version': '4.0_ollama',
                'model': self.model
            }
        }
        
        # Run all scraping tasks
        results['partners'] = await self.scrape_partners()
        await asyncio.sleep(2)  # Rate limiting
        
        results['promotions'] = await self.scrape_promotions()
        await asyncio.sleep(2)
        
        results['earning_rules'] = await self.scrape_earning_rules()
        await asyncio.sleep(2)
        
        results['award_chart'] = await self.scrape_award_chart()
        
        return results
    
    def save_results(self, results: dict, filename: str = 'united_scrape_ollama.json'):
        """Save results to Desktop"""
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        filepath = os.path.join(desktop, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        
        print(f"\n✓ Saved to: {filepath}")
        return filepath

# ============================================================================
# MAIN EXECUTION
# ============================================================================

async def main():
    print("""
╔══════════════════════════════════════════════════════════════════╗
║      United Airlines MileagePlus LLM Scraper v4.0                ║
║         Playwright + Ollama (100% FREE!)                         ║
╚══════════════════════════════════════════════════════════════════╝

SETUP INSTRUCTIONS:
1. Install dependencies:
   pip install playwright ollama markdownify pydantic
   
2. Install browser:
   playwright install chromium
   
3. Install and start Ollama:
   brew install ollama
   brew services start ollama
   ollama pull llama3.1
   
4. Run this script:
   python3 UnitedTestScraper.py
    """)
    
    try:
        async with UnitedLLMScraper(model="llama3.1") as scraper:
            results = await scraper.run_full_scrape()
            filepath = scraper.save_results(results)
            
            print("\n" + "="*70)
            print("✓ SCRAPING COMPLETE")
            print("="*70)
            
            # Print summary
            if results.get('partners'):
                total_partners = sum(len(v) for v in results['partners'].values())
                print(f"\nPartners: {total_partners} found")
            
            if results.get('promotions'):
                print(f"Promotions: {len(results['promotions'])} found")
            
            if results.get('earning_rules'):
                print(f"Earning Rules: {len(results['earning_rules'])} found")
            
            if results.get('award_chart'):
                print(f"Award Routes: {len(results['award_chart'])} found")
            
            print(f"\nData saved to: {filepath}")
            
    except KeyboardInterrupt:
        print("\n\n✗ Interrupted by user")
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())