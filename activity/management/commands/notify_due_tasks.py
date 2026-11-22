from django.core.management.base import BaseCommand

from activity.services import create_due_soon_notifications


class Command(BaseCommand):
    help = "Create 'due_soon' notifications for open tasks due within 24 hours (idempotent)."

    def handle(self, *args, **options) -> None:
        created = create_due_soon_notifications()
        self.stdout.write(f"Created {created} due-soon notification(s).")
