import os
import resend
from supabase import create_client
from datetime import datetime, timezone, timedelta


class NotificationScheduler:
    def __init__(self):
        self.supabase = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_KEY"],
        )
        resend.api_key = os.environ["RESEND_API_KEY"]
        self.notify_email = os.environ["NOTIFY_EMAIL"]

    def get_recent_changes(self) -> list:
        """Pulls all changes from the past 7 days grouped by site."""
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        result = (
            self.supabase.table("changes")
            .select("site_id, diff, detected_at")
            .gte("detected_at", week_ago)
            .order("detected_at", desc=True)
            .execute()
        )

        return result.data

    def get_site_label(self, site_id: int) -> str:
        result = (
            self.supabase.table("watched_sites")
            .select("label, url")
            .eq("id", site_id)
            .single()
            .execute()
        )
        return f"{result.data['label']} ({result.data['url']})"

    def format_email(self, changes: list) -> str:
        """Builds a readable HTML email from the changes list."""
        if not changes:
            return "<p>No changes detected across your tracked sites this week.</p>"

        # Group changes by site_id
        by_site = {}
        for change in changes:
            site_id = change["site_id"]
            if site_id not in by_site:
                by_site[site_id] = []
            by_site[site_id].append(change)

        html = "<h2>Weekly Website Change Digest</h2>"

        for site_id, site_changes in by_site.items():
            label = self.get_site_label(site_id)
            html += f"<h3>{label}</h3>"
            html += f"<p><strong>{len(site_changes)} change(s) detected</strong></p>"

            for change in site_changes:
                detected_at = change["detected_at"][:19].replace("T", " ")
                html += f"<p><em>Detected at: {detected_at}</em></p>"
                html += f"<pre style='background:#f4f4f4;padding:10px;font-size:12px;overflow:auto'>{change['diff'][:1000]}</pre>"

        return html

    def send_email(self, html: str):
        recipients = [email.strip() for email in self.notify_email.split(",")]
        resend.Emails.send({
            "from": "onboarding@resend.dev",
            "to": recipients,
            "subject": "Weekly Website Change Digest",
            "html": html,
        })

    def run(self):
        print("Fetching changes from the past 7 days...")
        changes = self.get_recent_changes()
        print(f"Found {len(changes)} change(s)\n")

        html = self.format_email(changes)
        self.send_email(html)
        print(f"Digest email sent to {self.notify_email}")
