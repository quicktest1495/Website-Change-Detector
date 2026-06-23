import difflib
from simhash import Simhash
from supabase import create_client
import os

# How similar two pages must be (0–1) to be considered unchanged.
# At 0.95, a single rotating banner on a typical 50-block page won't trigger
# a change. A meaningful content update (new pricing, rewritten copy) will.
# Tune this down if you're missing real changes; up if you're seeing noise.
SIMILARITY_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.95"))


class ChangeDetector:
    def __init__(self):
        self.supabase = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_KEY"],
        )

    def get_latest_snapshots(self, site_id: int) -> tuple | None:
        """Returns the two most recent snapshots for a site, or None if fewer than 2 exist."""
        result = (
            self.supabase.table("snapshots")
            .select("id, visible_text")
            .eq("site_id", site_id)
            .order("scraped_at", desc=True)
            .limit(2)
            .execute()
        )

        if len(result.data) < 2:
            print(f"  Not enough snapshots for site {site_id}, skipping")
            return None

        return result.data[0], result.data[1]

    @staticmethod
    def similarity(text_a: str, text_b: str) -> float:
        """Returns a 0–1 similarity score between two texts using SimHash.
        1.0 = identical, 0.0 = completely different."""
        a = Simhash(text_a.split())
        b = Simhash(text_b.split())
        distance = a.distance(b)  # differing bits out of 64
        return 1 - (distance / 64)

    def compare_snapshots(self, latest: dict, previous: dict) -> str | None:
        """Returns a diff string if content changed meaningfully, otherwise None."""
        sim = self.similarity(latest["visible_text"], previous["visible_text"])
        print(f"  Similarity: {sim:.3f} (threshold: {SIMILARITY_THRESHOLD})")

        if sim >= SIMILARITY_THRESHOLD:
            return None

        latest_lines = latest["visible_text"].splitlines(keepends=True)
        previous_lines = previous["visible_text"].splitlines(keepends=True)

        diff = difflib.unified_diff(
            previous_lines,
            latest_lines,
            fromfile="previous",
            tofile="latest",
        )

        return "".join(diff)

    def save_change(self, site_id: int, diff: str):
        self.supabase.table("changes").insert({
            "site_id": site_id,
            "diff": diff,
        }).execute()

    def run(self):
        sites = self.supabase.table("watched_sites").select("id, url, label").execute().data
        print(f"Checking {len(sites)} site(s) for changes\n")

        for site in sites:
            print(f"Checking: {site['label']} ({site['url']})")
            snapshots = self.get_latest_snapshots(site["id"])

            if snapshots is None:
                continue

            latest, previous = snapshots
            diff = self.compare_snapshots(latest, previous)

            if diff is None:
                print(f"  No meaningful changes detected\n")
            else:
                self.save_change(site["id"], diff)
                print(f"  Change detected and saved\n")
