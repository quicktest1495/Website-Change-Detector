import hashlib
import asyncio
from playwright.async_api import async_playwright, Browser
from supabase import create_client
import os

# Max number of sites scraped simultaneously. Keeps memory and network usage
# reasonable on the GitHub Actions runner.
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_SCRAPES", "5"))


class WebScraper:
    def __init__(self):
        self.supabase = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_KEY"],
        )

    async def dismiss_banners(self, page) -> None:
        """Remove cookie banners and consent dialogs from the DOM before extracting text.

        Uses a recursive approach to handle both regular DOM elements and elements
        inside shadow roots (e.g. OneTrust, CookieYes), which querySelectorAll
        cannot reach on its own.

        Silently ignores any selector or removal errors so a stubborn banner
        never blocks the rest of the scrape.
        """
        await page.evaluate("""
            () => {
                const SELECTORS = [
                    '[class*="cookie"]', '[class*="consent"]', '[class*="gdpr"]',
                    '[class*="onetrust"]', '[id*="onetrust"]',
                    '[class*="cookieyes"]', '[id*="cookieyes"]',
                    '[aria-label*="cookie" i]', '[aria-label*="consent" i]',
                    '[aria-describedby*="cookie" i]',
                    'cookie-consent', 'cookie-banner', 'consent-banner',
                ];

                function removeMatching(root) {
                    SELECTORS.forEach(sel => {
                        try {
                            root.querySelectorAll(sel).forEach(el => el.remove());
                        } catch(e) {}
                    });
                    // Recurse into shadow roots — handles OneTrust and similar
                    root.querySelectorAll('*').forEach(el => {
                        if (el.shadowRoot) removeMatching(el.shadowRoot);
                    });
                }

                removeMatching(document);
            }
        """)

    async def scrape_url(self, browser: Browser, url: str) -> tuple[str, str, str] | None:
        """Returns (raw_html, visible_text, content_hash) or None if the page should be skipped."""
        page = await browser.new_page()
        try:
            response = await page.goto(url, timeout=30000, wait_until="domcontentloaded")

            # Try to wait for network to settle. Sites that stream live data never
            # go fully idle, so we fall back to a fixed wait if it times out.
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                await page.wait_for_timeout(3000)

            if response is None or not response.ok:
                print(f"Skipping {url}: bad response ({response.status if response else 'no response'})")
                return None

            raw_html = await page.content()
            if not raw_html or raw_html.strip() == "":
                print(f"Skipping {url}: empty page")
                return None

            # Strip cookie banners and consent dialogs before reading text
            await self.dismiss_banners(page)

            visible_text = await page.evaluate(
                "() => document.body ? document.body.innerText : ''"
            )
            if not visible_text.strip():
                print(f"Skipping {url}: no visible text")
                return None

            content_hash = hashlib.sha256(visible_text.encode()).hexdigest()
            return raw_html, visible_text, content_hash

        except Exception as e:
            print(f"Skipping {url}: {e}")
            return None
        finally:
            await page.close()

    def save_snapshot(self, site_id: int, raw_html: str, visible_text: str, content_hash: str):
        self.supabase.table("snapshots").insert({
            "site_id": site_id,
            "raw_content": raw_html,
            "visible_text": visible_text,
            "content_hash": content_hash,
        }).execute()

    def cleanup_snapshots(self, site_id: int):
        """Keeps only the 2 most recent snapshots for a site and deletes the rest."""
        result = (
            self.supabase.table("snapshots")
            .select("id")
            .eq("site_id", site_id)
            .order("scraped_at", desc=True)
            .execute()
        )
        snapshots = result.data
        if len(snapshots) > 2:
            ids_to_delete = [s["id"] for s in snapshots[2:]]
            self.supabase.table("snapshots").delete().in_("id", ids_to_delete).execute()
            print(f"  Deleted {len(ids_to_delete)} old snapshot(s)")

    async def scrape_and_save(self, browser: Browser, site: dict, semaphore: asyncio.Semaphore):
        async with semaphore:
            url = site["url"]
            result = await self.scrape_url(browser, url)
            if result is None:
                return
            raw_html, visible_text, content_hash = result
            self.save_snapshot(site["id"], raw_html, visible_text, content_hash)
            self.cleanup_snapshots(site["id"])
            print(f"Saved snapshot for {url}")

    async def run(self):
        sites = self.supabase.table("watched_sites").select("id, url").execute().data
        print(f"Scraping {len(sites)} site(s) with max {MAX_CONCURRENT} concurrent...\n")

        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            await asyncio.gather(
                *[self.scrape_and_save(browser, site, semaphore) for site in sites]
            )
            await browser.close()
