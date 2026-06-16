import hashlib
from playwright.async_api import async_playwright
from supabase import create_client
import os


class WebScraper:
    def __init__(self):
        self.supabase = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_KEY"],
        )

    async def scrape_url(self, url: str) -> tuple[str, str] | None:
        """Returns (raw_html, content_hash) or None if the page should be skipped."""
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                page = await browser.new_page()
                response = await page.goto(url, timeout=30000, wait_until="networkidle")

                if response is None or not response.ok:
                    print(f"Skipping {url}: bad response")
                    return None

                raw_html = await page.content()

                if not raw_html or raw_html.strip() == "":
                    print(f"Skipping {url}: empty page")
                    return None

                visible_text = await page.evaluate(
                    "() => document.body ? document.body.innerText : ''"
                )

                if not visible_text.strip():
                    print(f"Skipping {url}: no visible text")
                    return None

                content_hash = hashlib.sha256(visible_text.encode()).hexdigest()
                return raw_html, content_hash

            except Exception as e:
                print(f"Skipping {url}: {e}")
                return None
            finally:
                await browser.close()

    def save_snapshot(self, site_id: int, raw_html: str, content_hash: str):
        self.supabase.table("snapshots").insert({
            "site_id": site_id,
            "raw_content": raw_html,
            "content_hash": content_hash,
        }).execute()

    async def run(self):
        sites = self.supabase.table("watched_sites").select("id, url").execute().data

        for site in sites:
            result = await self.scrape_url(site["url"])
            if result is None:
                continue
            raw_html, content_hash = result
            self.save_snapshot(site["id"], raw_html, content_hash)
            print(f"Saved snapshot for {site['url']}")
