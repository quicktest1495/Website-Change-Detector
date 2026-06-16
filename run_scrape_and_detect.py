import asyncio
from scraper import WebScraper
from change_detector import ChangeDetector


async def main():
    scraper = WebScraper()
    await scraper.run()

    detector = ChangeDetector()
    detector.run()


asyncio.run(main())
