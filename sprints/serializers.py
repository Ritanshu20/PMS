from rest_framework import serializers

from sprints.models import Sprint
from sprints.services import UnfinishedTaskAction


class SprintSerializer(serializers.ModelSerializer):
    task_count = serializers.IntegerField(read_only=True, required=False)
    planned_points = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = Sprint
        fields = [
            "id",
            "name",
            "goal",
            "start_date",
            "end_date",
            "status",
            "committed_points",
            "completed_points",
            "task_count",
            "planned_points",
            "started_at",
            "completed_at",
        ]
        read_only_fields = fields


class SprintCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    goal = serializers.CharField(required=False, allow_blank=True, default="")
    start_date = serializers.DateField()
    end_date = serializers.DateField()


class SprintUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, required=False)
    goal = serializers.CharField(required=False, allow_blank=True)
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)


class SprintCompleteSerializer(serializers.Serializer):
    unfinished_action = serializers.ChoiceField(choices=UnfinishedTaskAction.CHOICES)
    target_sprint_id = serializers.IntegerField(required=False, allow_null=True, default=None)


class SprintTaskSerializer(serializers.Serializer):
    task_number = serializers.IntegerField(min_value=1)


class SprintFilterSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Sprint.Status.choices, required=False)
