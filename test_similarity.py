"""
Unit tests for the SimHash similarity function in ChangeDetector.
No DB or network required — pure logic tests.
"""
import pytest
from unittest.mock import patch, MagicMock

with patch("change_detector.create_client", return_value=MagicMock()):
    from change_detector import ChangeDetector, SIMILARITY_THRESHOLD


# ── Baseline behaviour ────────────────────────────────────────────────────────

def test_identical_text_scores_perfect():
    text = "We build enterprise software for startups and Fortune 500 companies worldwide"
    assert ChangeDetector.similarity(text, text) == 1.0


def test_empty_texts_score_perfect():
    assert ChangeDetector.similarity("", "") == 1.0


def test_similarity_is_symmetric():
    a = "startup pricing plans features enterprise growth"
    b = "startup pricing tiers options business scale"
    assert ChangeDetector.similarity(a, b) == ChangeDetector.similarity(b, a)


def test_score_is_between_zero_and_one():
    a = "completely different content about cats"
    b = "totally unrelated text about spacecraft"
    sim = ChangeDetector.similarity(a, b)
    assert 0.0 <= sim <= 1.0


# ── Noise should NOT trigger a change ────────────────────────────────────────

def test_rotating_banner_stays_above_threshold():
    """One rotating banner line out of a full page should not cross the threshold."""
    stable = " ".join([f"stable content paragraph word{i}" for i in range(200)])
    page_a = "Summer Sale 20 percent off everything this weekend only\n" + stable
    page_b = "New Year Special free trial for thirty days signup now\n" + stable
    sim = ChangeDetector.similarity(page_a, page_b)
    assert sim >= SIMILARITY_THRESHOLD, (
        f"Rotating banner caused false positive: similarity={sim:.3f} "
        f"is below threshold {SIMILARITY_THRESHOLD}"
    )


def test_noise_scores_higher_than_real_change():
    """A single noisy line should score closer to 1.0 than a genuine content rewrite."""
    stable = " ".join([f"word{i}" for i in range(100)])

    # Noise: one rotating banner line, rest identical
    noise_a = "Buy now limited offer\n" + stable
    noise_b = "Flash sale ends tonight\n" + stable
    noise_sim = ChangeDetector.similarity(noise_a, noise_b)

    # Real change: most of the content is different
    real_a = " ".join(["enterprise solution scalable reliable secure"] * 20)
    real_b = " ".join(["consumer mobile app download free signup today"] * 20)
    real_sim = ChangeDetector.similarity(real_a, real_b)

    assert noise_sim > real_sim, (
        f"Noise ({noise_sim:.3f}) should score higher than real change ({real_sim:.3f})"
    )


# ── Real changes SHOULD trigger a change ─────────────────────────────────────

def test_major_content_rewrite_falls_below_threshold():
    old = " ".join(["enterprise software solutions scalable reliable"] * 15)
    new = " ".join(["consumer mobile app download free trial today"] * 15)
    sim = ChangeDetector.similarity(old, new)
    assert sim < SIMILARITY_THRESHOLD, (
        f"Major rewrite should fall below threshold, got {sim:.3f}"
    )


def test_pricing_page_change_falls_below_threshold():
    """Rewriting a full pricing section should be detected."""
    shared_nav = "Home About Product Pricing Blog Contact"
    old = shared_nav + " " + " ".join([
        "Starter plan ten dollars per month basic features one user",
        "Pro plan fifty dollars per month advanced features five users",
        "Enterprise plan contact us unlimited users all features"
    ] * 5)
    new = shared_nav + " " + " ".join([
        "Free plan zero dollars forever limited features one user",
        "Growth plan thirty dollars per month most features ten users",
        "Scale plan one hundred dollars per month everything unlimited users"
    ] * 5)
    sim = ChangeDetector.similarity(old, new)
    assert sim < SIMILARITY_THRESHOLD, (
        f"Pricing page rewrite should fall below threshold, got {sim:.3f}"
    )
