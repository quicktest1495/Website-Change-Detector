"""
Tests for WebScraper.

Unit tests use mocked Playwright and Supabase — no network or DB needed.
The concurrency test measures wall-clock time to confirm simultaneous scraping.
"""
import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def make_mock_page(
    html: str = "<html><body><p>Hello world</p></body></html>",
    inner_text: str = "Hello world",
    response_ok: bool = True,
    response_status: int = 200,
    goto_raises: Exception | None = None,
    networkidle_raises: Exception | None = None,
):
    """Build a mock Playwright page with configurable behaviour."""
    page = AsyncMock()

    mock_response = MagicMock()
    mock_response.ok = response_ok
    mock_response.status = response_status

    if goto_raises:
        page.goto.side_effect = goto_raises
    else:
        page.goto.return_value = mock_response

    if networkidle_raises:
        page.wait_for_load_state.side_effect = networkidle_raises
    else:
        page.wait_for_load_state.return_value = None

    page.content.return_value = html
    page.evaluate.return_value = inner_text
    return page


@pytest.fixture
def scraper():
    with patch("scraper.create_client", return_value=MagicMock()):
        from scraper import WebScraper
        return WebScraper()


# ── Error handling ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bad_http_response_returns_none(scraper):
    """4xx/5xx responses should be skipped, not saved."""
    browser = AsyncMock()
    browser.new_page.return_value = make_mock_page(response_ok=False, response_status=404)

    result = await scraper.scrape_url(browser, "https://example.com")
    assert result is None


@pytest.mark.asyncio
async def test_empty_html_returns_none(scraper):
    """A page with no HTML body should be skipped."""
    browser = AsyncMock()
    browser.new_page.return_value = make_mock_page(html="   ", inner_text="")

    result = await scraper.scrape_url(browser, "https://example.com")
    assert result is None


@pytest.mark.asyncio
async def test_no_visible_text_returns_none(scraper):
    """A page with HTML but no readable text (JS shell before render) should be skipped."""
    browser = AsyncMock()
    browser.new_page.return_value = make_mock_page(
        html="<html><body><div id='root'></div></body></html>",
        inner_text="   ",
    )

    result = await scraper.scrape_url(browser, "https://example.com")
    assert result is None


@pytest.mark.asyncio
async def test_network_exception_returns_none(scraper):
    """A connection error or timeout should be skipped gracefully."""
    browser = AsyncMock()
    browser.new_page.return_value = make_mock_page(
        goto_raises=Exception("net::ERR_CONNECTION_REFUSED")
    )

    result = await scraper.scrape_url(browser, "https://example.com")
    assert result is None


@pytest.mark.asyncio
async def test_networkidle_timeout_falls_back_gracefully(scraper):
    """If networkidle times out, scraper should fall back to fixed wait and still succeed."""
    browser = AsyncMock()
    page = make_mock_page(networkidle_raises=Exception("Timeout waiting for networkidle"))
    browser.new_page.return_value = page

    result = await scraper.scrape_url(browser, "https://example.com")

    # Should still succeed via the fallback wait
    assert result is not None
    raw_html, visible_text, content_hash = result
    assert visible_text == "Hello world"
    assert len(content_hash) == 64  # SHA-256 hex


@pytest.mark.asyncio
async def test_successful_scrape_returns_tuple(scraper):
    """A healthy page should return (raw_html, visible_text, content_hash)."""
    browser = AsyncMock()
    browser.new_page.return_value = make_mock_page(
        html="<html><body><p>Startup homepage</p></body></html>",
        inner_text="Startup homepage",
    )

    result = await scraper.scrape_url(browser, "https://example.com")

    assert result is not None
    raw_html, visible_text, content_hash = result
    assert "Startup homepage" in raw_html
    assert visible_text == "Startup homepage"
    assert len(content_hash) == 64


# ── Concurrency ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_scraping_faster_than_sequential(scraper):
    """Sites should be scraped in parallel, not one at a time.

    Each fake scrape takes 150 ms. With 4 sites sequential that's 600 ms.
    Concurrent should finish in ~150 ms. We allow up to 350 ms as headroom.

    async_playwright is mocked so no real Chromium launches — otherwise the
    ~700 ms browser startup dominates the measurement and masks sequential vs
    concurrent behaviour.
    """
    SCRAPE_DELAY = 0.15
    sites = [{"id": i, "url": f"https://site{i}.com"} for i in range(4)]

    async def slow_scrape(browser, url):
        await asyncio.sleep(SCRAPE_DELAY)
        return "<html><body>content</body></html>", "content", "hash"

    scraper.scrape_url = slow_scrape
    scraper.save_snapshot = MagicMock()
    scraper.cleanup_snapshots = MagicMock()

    mock_table = MagicMock()
    mock_table.select.return_value.execute.return_value.data = sites
    scraper.supabase.table.return_value = mock_table

    # Mock playwright so browser launch doesn't add ~700ms startup overhead
    mock_browser = AsyncMock()
    mock_pw_instance = AsyncMock()
    mock_pw_instance.chromium.launch = AsyncMock(return_value=mock_browser)
    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw_instance)
    mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("scraper.async_playwright", return_value=mock_pw_cm):
        start = time.monotonic()
        await scraper.run()
        elapsed = time.monotonic() - start

    assert elapsed < SCRAPE_DELAY * 2, (
        f"Expected concurrent scraping (~{SCRAPE_DELAY}s), "
        f"took {elapsed:.2f}s — may be running sequentially"
    )


@pytest.mark.asyncio
async def test_all_sites_attempted_even_if_some_fail(scraper):
    """Every site in watched_sites should be attempted, even if some fail."""
    sites = [{"id": i, "url": f"https://site{i}.com"} for i in range(5)]
    attempted = []

    async def tracking_scrape(browser, url):
        attempted.append(url)
        if "site2" in url:
            return None  # simulate failure
        return "<html><body>content</body></html>", "content", "hash"

    scraper.scrape_url = tracking_scrape
    scraper.save_snapshot = MagicMock()
    scraper.cleanup_snapshots = MagicMock()

    mock_table = MagicMock()
    mock_table.select.return_value.execute.return_value.data = sites
    scraper.supabase.table.return_value = mock_table

    await scraper.run()

    assert len(attempted) == 5, f"Expected 5 scrape attempts, got {len(attempted)}"
    assert all(f"https://site{i}.com" in attempted for i in range(5))
