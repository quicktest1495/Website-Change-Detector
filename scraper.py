import hashlib
import asyncio
from playwright.async_api import async_playwright
from supabase import create_client
import os

# Seconds to wait between the two looks at a site. The gap lets jittery content
# (counters, cookie banners, animations) change so we can spot and drop it.
SCRAPE_GAP_SECONDS = int(os.environ.get("SCRAPE_GAP_SECONDS", "60"))


class WebScraper:
    def __init__(self):
        self.supabase = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_KEY"],
        )

    async def scrape_url(self, url: str) -> tuple[str, str, str] | None:
        """Returns (raw_html, visible_text, content_hash) or None if the page should be skipped."""
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                page = await browser.new_page()
                # "domcontentloaded" rather than "networkidle": sites that stream
                # live data (e.g. GeekWire) never go idle and would time out.
                response = await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                # Give client-side JS a moment to render before capturing
                await page.wait_for_timeout(2000)

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
                return raw_html, visible_text, content_hash

            except Exception as e:
                print(f"Skipping {url}: {e}")
                return None
            finally:
                await browser.close()

    @staticmethod
    def stable_text(text_a: str, text_b: str) -> str:
        """Keeps only the lines that appear in BOTH looks at the page. Lines that
        changed between the two scrapes (counters, cookie banners, animations) are
        dropped as jitter; lines that stayed the same are the real content."""
        a_lines = set(line.strip() for line in text_a.splitlines())
        stable = [line for line in text_b.splitlines() if line.strip() in a_lines]
        return "\n".join(stable)

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

    async def run(self):
        sites = self.supabase.table("watched_sites").select("id, url").execute().data

        for site in sites:
            url = site["url"]

            # Look 1
            look_a = await self.scrape_url(url)
            if look_a is None:
                continue
            print(f"  Looked at {url} (1/2), waiting {SCRAPE_GAP_SECONDS}s...")
            await asyncio.sleep(SCRAPE_GAP_SECONDS)

            # Look 2
            look_b = await self.scrape_url(url)

            if look_b is None:
                # Second look failed — fall back to the single look, unfiltered
                raw_html, visible_text, content_hash = look_a
            else:
                # Keep only the content that stayed the same across both looks
                visible_text = self.stable_text(look_a[1], look_b[1])
                raw_html = look_b[0]
                content_hash = hashlib.sha256(visible_text.encode()).hexdigest()

            self.save_snapshot(site["id"], raw_html, visible_text, content_hash)
            self.cleanup_snapshots(site["id"])
            print(f"Saved stable snapshot for {url}")
