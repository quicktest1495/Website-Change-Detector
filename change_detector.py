import difflib
from supabase import create_client
import os


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
            .select("id, content_hash, raw_content")
            .eq("site_id", site_id)
            .order("scraped_at", desc=True)
            .limit(2)
            .execute()
        )

        if len(result.data) < 2:
            print(f"  Not enough snapshots for site {site_id}, skipping")
            return None

        return result.data[0], result.data[1]

    def compare_snapshots(self, latest: dict, previous: dict) -> str | None:
        """Returns a diff string if content changed, otherwise None."""
        if latest["content_hash"] == previous["content_hash"]:
            return None

        latest_lines = latest["raw_content"].splitlines(keepends=True)
        previous_lines = previous["raw_content"].splitlines(keepends=True)

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
                print(f"  No changes detected\n")
            else:
                self.save_change(site["id"], diff)
                print(f"  Change detected and saved\n")
