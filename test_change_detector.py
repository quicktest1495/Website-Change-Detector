from dotenv import load_dotenv
from change_detector import ChangeDetector

load_dotenv()


def main():
    detector = ChangeDetector()

    # Run the full detection flow against real data
    detector.run()

    # Show what's in the changes table
    changes = detector.supabase.table("changes").select("*").execute().data
    print(f"Total changes in database: {len(changes)}")
    for change in changes:
        print(f"\n  Site ID: {change['site_id']}")
        print(f"  Detected at: {change['detected_at']}")
        print(f"  Diff preview: {change['diff'][:200]}...")


main()
