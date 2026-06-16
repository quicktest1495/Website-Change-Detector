import asyncio
from dotenv import load_dotenv
from scraper import WebScraper

load_dotenv()


async def main():
    scraper = WebScraper()

    sites = scraper.supabase.table("watched_sites").select("id, url, label").execute().data
    print(f"Found {len(sites)} site(s) in watched_sites\n")

    for site in sites:
        print(f"Scraping: {site['label']} ({site['url']})")
        result = await scraper.scrape_url(site["url"])

        if result is None:
            print(f"  SKIPPED\n")
            continue

        raw_html, content_hash = result
        print(f"  HTML length: {len(raw_html)} chars")
        print(f"  Content hash: {content_hash[:16]}...")

        scraper.save_snapshot(site["id"], raw_html, content_hash)
        print(f"  Snapshot saved\n")


asyncio.run(main())
