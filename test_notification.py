from dotenv import load_dotenv
from notification_scheduler import NotificationScheduler

load_dotenv()


def main():
    scheduler = NotificationScheduler()
    scheduler.run()


main()
