from rest_framework import serializers

from accounts.serializers import PublicUserSerializer
from activity.models import ActivityLog, Notification


class NotificationSerializer(serializers.ModelSerializer):
    project_key = serializers.CharField(source="project.key", read_only=True)
    is_read = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            "id",
            "type",
            "message",
            "link",
            "project_id",
            "project_key",
            "is_read",
            "read_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_is_read(self, notification: Notification) -> bool:
        return notification.read_at is not None


class NotificationFilterSerializer(serializers.Serializer):
    unread = serializers.BooleanField(required=False, default=False)


class ActivitySerializer(serializers.ModelSerializer):
    actor = PublicUserSerializer(read_only=True)

    class Meta:
        model = ActivityLog
        fields = ["id", "actor", "verb", "target_type", "target_id", "changes", "created_at"]
        read_only_fields = fields
