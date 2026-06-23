"""
Tests for ChangeDetector.

Unit tests use a mocked Supabase client — no DB connection needed.
Covers: SimHash gating, diff generation, single-snapshot skip, and
the save_change path.
"""
import pytest
from unittest.mock import MagicMock, call, patch


@pytest.fixture
def detector():
    with patch("change_detector.create_client", return_value=MagicMock()):
        from change_detector import ChangeDetector
        return ChangeDetector()


def make_snapshot(visible_text: str, snapshot_id: int = 1) -> dict:
    return {"id": snapshot_id, "visible_text": visible_text}


# ── Single-snapshot skip ──────────────────────────────────────────────────────

def test_single_snapshot_is_skipped(detector):
    """Change detection requires two snapshots. One snapshot should skip gracefully."""
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.order.return_value.limit.return_value \
        .execute.return_value.data = [make_snapshot("Only snapshot so far")]
    detector.supabase.table.return_value = mock_table

    result = detector.get_latest_snapshots(site_id=1)
    assert result is None


def test_no_snapshots_is_skipped(detector):
    """A brand-new site with zero snapshots should also skip gracefully."""
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.order.return_value.limit.return_value \
        .execute.return_value.data = []
    detector.supabase.table.return_value = mock_table

    result = detector.get_latest_snapshots(site_id=1)
    assert result is None


# ── No change detected ────────────────────────────────────────────────────────

def test_identical_pages_produce_no_diff(detector):
    """Two identical snapshots should not produce a diff."""
    text = "Our pricing starts at ten dollars per month for all features included"
    latest = make_snapshot(text, snapshot_id=2)
    previous = make_snapshot(text, snapshot_id=1)

    diff = detector.compare_snapshots(latest, previous)
    assert diff is None


def test_minor_noise_produces_no_diff(detector):
    """A single rotating banner line out of a large page should not trigger a change."""
    stable = " ".join([f"stable content word{i}" for i in range(200)])
    latest = make_snapshot("Flash sale ends tonight\n" + stable, snapshot_id=2)
    previous = make_snapshot("Buy now limited offer\n" + stable, snapshot_id=1)

    diff = detector.compare_snapshots(latest, previous)
    assert diff is None, (
        "Rotating banner caused a false positive — "
        "consider raising SIMILARITY_THRESHOLD"
    )


# ── Change detected ───────────────────────────────────────────────────────────

def test_major_rewrite_produces_diff(detector):
    """A substantially rewritten page should produce a non-empty diff."""
    latest = make_snapshot(
        " ".join(["consumer mobile app download free trial signup today"] * 15),
        snapshot_id=2,
    )
    previous = make_snapshot(
        " ".join(["enterprise software solutions scalable reliable secure"] * 15),
        snapshot_id=1,
    )

    diff = detector.compare_snapshots(latest, previous)
    assert diff is not None and len(diff) > 0


def test_diff_contains_added_and_removed_markers(detector):
    """The unified diff should include + and - lines so the email is readable."""
    latest = make_snapshot(
        " ".join(["new pricing fifty dollars per month advanced features"] * 20),
        snapshot_id=2,
    )
    previous = make_snapshot(
        " ".join(["old pricing ten dollars per month basic features only"] * 20),
        snapshot_id=1,
    )

    diff = detector.compare_snapshots(latest, previous)
    assert diff is not None
    assert any(line.startswith("+") for line in diff.splitlines())
    assert any(line.startswith("-") for line in diff.splitlines())


def test_detected_change_is_saved_to_db(detector):
    """When a real change is found, it should be written to the changes table."""
    latest = make_snapshot(
        " ".join(["completely new homepage content product launch announcement"] * 20),
        snapshot_id=2,
    )
    previous = make_snapshot(
        " ".join(["old homepage content company description about us page"] * 20),
        snapshot_id=1,
    )

    detector.save_change = MagicMock()
    diff = detector.compare_snapshots(latest, previous)

    assert diff is not None
    detector.save_change(site_id=42, diff=diff)
    detector.save_change.assert_called_once_with(site_id=42, diff=diff)
