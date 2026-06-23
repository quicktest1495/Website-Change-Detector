import os
import resend
from supabase import create_client
from datetime import datetime, timezone, timedelta


class FilteredNotificationScheduler:
    def __init__(self):
        self.supabase = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_KEY"],
        )
        resend.api_key = os.environ["RESEND_API_KEY"]
        self.notify_email = os.environ["NOTIFY_EMAIL"]

    def get_recent_filtered_changes(self, days: int = 7) -> list:
        """Pulls all filtered_changes from the past N days, ordered by similarity ascending
        (closest to the threshold first — most interesting for calibration)."""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        result = (
            self.supabase.table("filtered_changes")
            .select("site_id, similarity_score, diff, detected_at, threshold_at_time")
            .gte("detected_at", since)
            .order("similarity_score", desc=False)  # lowest similarity first — most borderline
            .execute()
        )

        return result.data

    def get_site_labels(self, site_ids: list[int]) -> dict[int, str]:
        """Batch fetch site labels to avoid N+1 queries."""
        result = (
            self.supabase.table("watched_sites")
            .select("id, label, url")
            .in_("id", site_ids)
            .execute()
        )
        return {row["id"]: f"{row['label']} ({row['url']})" for row in result.data}

    def parse_diff(self, diff: str) -> tuple[list, list]:
        """Extracts added and removed lines from a unified diff."""
        added, removed = [], []
        for line in diff.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added.append(line[1:].strip())
            elif line.startswith("-") and not line.startswith("---"):
                removed.append(line[1:].strip())
        added = [l for l in added if l and not l.startswith("<")]
        removed = [l for l in removed if l and not l.startswith("<")]
        return added, removed

    def similarity_bar(self, score: float, threshold: float) -> str:
        """Returns a simple visual bar showing score vs threshold."""
        filled = round(score * 20)
        bar = "█" * filled + "░" * (20 - filled)
        return f"{bar} {score:.3f} (threshold: {threshold:.2f})"

    def format_email(self, filtered: list, site_labels: dict) -> str:
        if not filtered:
            return """
                <p>No filtered changes in this period.</p>
                <p>This means either:</p>
                <ul>
                    <li>All pages were identical between scrapes (similarity = 1.0), or</li>
                    <li>All changes were significant enough to cross the threshold and were recorded in the main changes table.</li>
                </ul>
                <p>Your threshold looks well-calibrated.</p>
            """

        # Group by site
        by_site = {}
        for row in filtered:
            sid = row["site_id"]
            if sid not in by_site:
                by_site[sid] = []
            by_site[sid].append(row)

        total = len(filtered)
        threshold = filtered[0]["threshold_at_time"] if filtered else 0.95

        html = f"""
            <h2>🔍 Filtered Changes Calibration Report</h2>
            <p>These are comparisons that were <strong>suppressed by the SimHash threshold ({threshold})</strong>
            and not recorded as real changes. Review them to verify your threshold is correctly tuned.</p>
            <p><strong>{total} filtered comparison(s)</strong> across {len(by_site)} site(s).
            Ordered by similarity score — lowest first (most borderline at the top).</p>
            <hr>
        """

        for site_id, rows in by_site.items():
            label = site_labels.get(site_id, f"Site {site_id}")
            html += f"<h3>{label}</h3>"
            html += f"<p>{len(rows)} filtered comparison(s)</p>"

            for row in rows:
                detected_at = row["detected_at"][:19].replace("T", " ")
                sim = row["similarity_score"]
                gap = sim - row["threshold_at_time"]

                # Highlight borderline cases (within 0.03 of threshold)
                border_style = "border-left: 4px solid #e67e22; padding-left: 12px;" if gap < 0.03 else ""

                html += f'<div style="{border_style} margin-bottom: 16px;">'
                html += f"<p><em>{detected_at} UTC</em></p>"
                html += f"<p>Similarity: <code>{sim:.4f}</code> — "
                html += f"<strong>{gap:.4f} above threshold</strong>"
                if gap < 0.03:
                    html += " ⚠️ <em>Borderline — review carefully</em>"
                html += "</p>"

                added, removed = self.parse_diff(row["diff"])

                if added:
                    html += "<p><strong>Would have shown as added:</strong></p><ul>"
                    for line in added[:8]:
                        html += f"<li>{line}</li>"
                    if len(added) > 8:
                        html += f"<li>...and {len(added) - 8} more lines</li>"
                    html += "</ul>"

                if removed:
                    html += "<p><strong>Would have shown as removed:</strong></p><ul>"
                    for line in removed[:8]:
                        html += f"<li>{line}</li>"
                    if len(removed) > 8:
                        html += f"<li>...and {len(removed) - 8} more lines</li>"
                    html += "</ul>"

                if not added and not removed:
                    html += "<p><em>Structural HTML changed but no visible text difference.</em></p>"

                html += "</div><hr>"

        html += """
            <h3>How to use this report</h3>
            <ul>
                <li>If borderline items (⚠️) look like <strong>real changes you care about</strong>,
                    lower SIMILARITY_THRESHOLD (e.g. 0.93).</li>
                <li>If all filtered items look like <strong>noise</strong> (nav links, counters, ads),
                    your threshold is well-calibrated.</li>
                <li>If you see <strong>no filtered items at all</strong>, your threshold may be too low
                    and catching everything — check the main digest for false positives.</li>
            </ul>
        """

        return html

    def send_email(self, html: str, days: int):
        recipients = [email.strip() for email in self.notify_email.split(",")]
        resend.Emails.send({
            "from": "onboarding@resend.dev",
            "to": recipients,
            "subject": f"Filtered Changes Calibration Report — past {days} days",
            "html": html,
        })

    def clear_filtered_changes(self):
        """Clears all rows from filtered_changes after the report is sent."""
        self.supabase.table("filtered_changes").delete().neq("id", 0).execute()
        print("Cleared filtered_changes table")

    def run(self, days: int = 7):
        print(f"Fetching filtered changes from the past {days} days...")
        filtered = self.get_recent_filtered_changes(days)
        print(f"Found {len(filtered)} filtered comparison(s)\n")

        site_ids = list({row["site_id"] for row in filtered})
        site_labels = self.get_site_labels(site_ids) if site_ids else {}

        html = self.format_email(filtered, site_labels)
        self.send_email(html, days)
        print(f"Filtered changes report sent to {self.notify_email}")

        self.clear_filtered_changes()
