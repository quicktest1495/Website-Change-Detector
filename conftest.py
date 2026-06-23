import pytest
from unittest.mock import MagicMock, patch
from dotenv import load_dotenv

load_dotenv()


def make_scraper():
    """WebScraper instance with Supabase mocked out — no DB connection needed."""
    with patch("scraper.create_client", return_value=MagicMock()):
        from scraper import WebScraper
        return WebScraper()


def make_detector():
    """ChangeDetector instance with Supabase mocked out — no DB connection needed."""
    with patch("change_detector.create_client", return_value=MagicMock()):
        from change_detector import ChangeDetector
        return ChangeDetector()


@pytest.fixture
def scraper():
    return make_scraper()


@pytest.fixture
def detector():
    return make_detector()
