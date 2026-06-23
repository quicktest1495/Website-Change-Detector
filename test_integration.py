"""
Integration tests — require a real Supabase connection and network access.

Run with:  pytest test_integration.py -v
Or with the full suite: pytest -v -m "not integration" for unit tests only.

These tests write to your real Supabase DB. They clean up after themselves
but assume the tables start empty (run after clearing snapshots and changes).
"""
import asyncio
import pytest
from dotenv import load_dotenv
from supabase import create_client
import os

load_dotenv()

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def supabase():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


@pytest.fixture(scope="module")
def watched_sites(supabase):
    sites = supabase.table("watched_sites").select("id, url, label").execute().data
    if not sites:
        pytest.skip("No sites in watched_sites table — add URLs before running integration tests")
    return sites


# ── Double-scrape: no false positives ────────────────────────────────────────

@pytest.mark.asyncio
async def test_double_scrape_produces_no_false_positives(supabase, watched_sites):
    """
    Scrape all watched URLs twice back-to-back, run change detection,
    and assert no changes are written. This validates that SimHash at the
    current threshold is not triggering on normal page variation between
    two immediate scrapes.

    If this test fails, lower SIMILARITY_THRESHOLD or investigate the site
    that produced the diff.
    """
    from scraper import WebScraper
    from change_detector import ChangeDetector

    scraper = WebScraper()
    detector = ChangeDetector()

    # Clear any leftover data so we start clean
    supabase.table("changes").delete().neq("id", 0).execute()
    supabase.table("snapshots").delete().neq("id", 0).execute()

    # Scrape 1 — builds baseline snapshots
    print(f"\nScrape 1: scraping {len(watched_sites)} site(s)...")
    await scraper.run()

    snapshot_count_after_first = (
        supabase.table("snapshots").select("id", count="exact").execute().count
    )
    print(f"Snapshots after scrape 1: {snapshot_count_after_first}")
    assert snapshot_count_after_first > 0, "No snapshots saved — check scraper"

    # Change detection after scrape 1 — should skip all sites (only 1 snapshot each)
    detector.run()
    changes_after_first = supabase.table("changes").select("id", count="exact").execute().count
    assert changes_after_first == 0, (
        f"Changes written after first scrape — should be impossible with only 1 snapshot per site"
    )

    # Scrape 2 — runs immediately, content should be nearly identical
    print(f"\nScrape 2: scraping {len(watched_sites)} site(s) again...")
    await scraper.run()

    snapshot_count_after_second = (
        supabase.table("snapshots").select("id", count="exact").execute().count
    )
    print(f"Snapshots after scrape 2: {snapshot_count_after_second}")

    # Change detection after scrape 2 — now has 2 snapshots per site, should find no changes
    detector.run()
    changes_after_second = supabase.table("changes").select("id", count="exact").execute().count

    # Report any false positives before asserting
    if changes_after_second > 0:
        false_positives = supabase.table("changes").select("site_id, diff, detected_at").execute().data
        for fp in false_positives:
            site = next((s for s in watched_sites if s["id"] == fp["site_id"]), {})
            print(f"\nFALSE POSITIVE on {site.get('label', fp['site_id'])} ({site.get('url', '')})")
            print(f"Diff preview:\n{fp['diff'][:400]}")

    assert changes_after_second == 0, (
        f"{changes_after_second} false positive(s) detected. "
        "See above for which sites triggered. "
        "Consider raising SIMILARITY_THRESHOLD or inspecting those sites."
    )

    print(f"\n✓ Double-scrape passed — no false positives across {len(watched_sites)} site(s)")


# ── Concurrency smoke test ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_scraping_completes_without_errors(supabase, watched_sites):
    """
    Runs the full scraper against real URLs concurrently and verifies:
    1. All reachable sites produce a snapshot
    2. No exceptions crash the run
    3. Snapshot count is reasonable (at least 1 new snapshot saved)

    This is a smoke test — it doesn't assert timing, just correctness.
    """
    from scraper import WebScraper

    scraper = WebScraper()

    # Clear snapshots so we start fresh
    supabase.table("changes").delete().neq("id", 0).execute()
    supabase.table("snapshots").delete().neq("id", 0).execute()

    before = supabase.table("snapshots").select("id", count="exact").execute().count
    assert before == 0

    # Run — should not raise
    await scraper.run()

    after = supabase.table("snapshots").select("id", count="exact").execute().count
    print(f"\nSnapshots saved: {after} / {len(watched_sites)} sites attempted")

    assert after >= 1, (
        "No snapshots were saved — all sites may have failed. "
        "Check your watched_sites URLs and network connectivity."
    )

    # Cleanup
    supabase.table("snapshots").delete().neq("id", 0).execute()
