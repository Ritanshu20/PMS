from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.serializers import PublicUserSerializer
from tasks.comment_services import DELETED_PLACEHOLDER, MAX_COMMENT_LENGTH
from tasks.models import Comment, Label, Task, TimeEntry
from tasks.selectors import TASK_ORDERINGS

MAX_ESTIMATE_MINUTES = 60 * 24 * 365  # one year of minutes; anything larger is a typo


class LabelSerializer(serializers.ModelSerializer):
    color = serializers.RegexField(r"^#[0-9A-Fa-f]{6}$", required=False, default="#6c757d")

    class Meta:
        model = Label
        fields = ["id", "name", "color"]


class LabelUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=50, required=False)
    color = serializers.RegexField(r"^#[0-9A-Fa-f]{6}$", required=False)


class TaskRefSerializer(serializers.Serializer):
    """Short reference to another task, used for "blocked by" / "blocks" lists."""

    number = serializers.IntegerField()
    key = serializers.CharField()
    title = serializers.CharField()
    status = serializers.CharField()


class SprintRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    status = serializers.CharField()


class TaskSerializer(serializers.ModelSerializer):
    key = serializers.SerializerMethodField()
    reporter = PublicUserSerializer(read_only=True)
    assignee = PublicUserSerializer(read_only=True)
    labels = LabelSerializer(many=True, read_only=True)
    blocked_by = serializers.SerializerMethodField()
    blocks = serializers.SerializerMethodField()
    sprint = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            "id",
            "key",
            "number",
            "title",
            "description",
            "status",
            "priority",
            "reporter",
            "assignee",
            "due_date",
            "story_points",
            "estimate_minutes",
            "sprint",
            "position",
            "completed_at",
            "version",
            "labels",
            "blocked_by",
            "blocks",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_key(self, task: Task) -> str:
        return f"{task.project.key}-{task.number}"

    def _ref(self, task: Task, other: Task) -> dict:
        return {
            "number": other.number,
            "key": f"{task.project.key}-{other.number}",
            "title": other.title,
            "status": other.status,
        }

    @extend_schema_field(TaskRefSerializer(many=True))
    def get_blocked_by(self, task: Task) -> list[dict]:
        return [self._ref(task, link.blocker) for link in task.blocked_by_links.all()]

    @extend_schema_field(TaskRefSerializer(many=True))
    def get_blocks(self, task: Task) -> list[dict]:
        return [self._ref(task, link.blocked) for link in task.blocking_links.all()]

    @extend_schema_field(SprintRefSerializer(allow_null=True))
    def get_sprint(self, task: Task) -> dict | None:
        if task.sprint is None:
            return None
        return {"id": task.sprint.pk, "name": task.sprint.name, "status": task.sprint.status}


class BoardColumnsSerializer(serializers.Serializer):
    backlog = TaskSerializer(many=True)
    todo = TaskSerializer(many=True)
    in_progress = TaskSerializer(many=True)
    in_review = TaskSerializer(many=True)
    done = TaskSerializer(many=True)


class BoardSerializer(serializers.Serializer):
    """Documentation shape of GET /board/: one list of tasks per status column."""

    columns = BoardColumnsSerializer()


class TaskCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    status = serializers.ChoiceField(choices=Task.Status.choices, default=Task.Status.BACKLOG)
    priority = serializers.ChoiceField(choices=Task.Priority.choices, default=Task.Priority.MEDIUM)
    assignee_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    due_date = serializers.DateField(required=False, allow_null=True, default=None)
    story_points = serializers.IntegerField(
        required=False, allow_null=True, default=None, min_value=0, max_value=100
    )
    estimate_minutes = serializers.IntegerField(
        required=False, allow_null=True, default=None, min_value=1, max_value=MAX_ESTIMATE_MINUTES
    )
    label_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list
    )


class TaskUpdateSerializer(serializers.Serializer):
    """Partial update. `version` is mandatory: it is the version the edit was based on."""

    version = serializers.IntegerField(min_value=1)
    title = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=Task.Status.choices, required=False)
    priority = serializers.ChoiceField(choices=Task.Priority.choices, required=False)
    assignee_id = serializers.IntegerField(required=False, allow_null=True)
    due_date = serializers.DateField(required=False, allow_null=True)
    story_points = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, max_value=100
    )
    estimate_minutes = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=MAX_ESTIMATE_MINUTES
    )
    label_ids = serializers.ListField(child=serializers.IntegerField(), required=False)


class TaskMoveSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)
    status = serializers.ChoiceField(choices=Task.Status.choices)
    position = serializers.IntegerField(min_value=0)


@extend_schema_field(OpenApiTypes.STR)
class CommaSeparatedChoicesField(serializers.Field):
    """Query-param field: "todo,done" -> ["todo", "done"], each value validated."""

    def __init__(self, choices: list[str], **kwargs):
        self.allowed = set(choices)
        super().__init__(**kwargs)

    def to_internal_value(self, data: str) -> list[str]:
        values = [value.strip() for value in str(data).split(",") if value.strip()]
        invalid = [value for value in values if value not in self.allowed]
        if invalid:
            raise serializers.ValidationError(f"Invalid value(s): {', '.join(invalid)}.")
        return values


class TaskFilterSerializer(serializers.Serializer):
    status = CommaSeparatedChoicesField(Task.Status.values, required=False)
    priority = CommaSeparatedChoicesField(Task.Priority.values, required=False)
    assignee = serializers.RegexField(r"^(me|none|\d+)$", required=False)
    sprint = serializers.RegexField(r"^(none|\d+)$", required=False)
    label = serializers.IntegerField(required=False)
    due_before = serializers.DateField(required=False)
    due_after = serializers.DateField(required=False)
    overdue = serializers.BooleanField(required=False, default=False)
    q = serializers.CharField(required=False, max_length=100)
    ordering = serializers.ChoiceField(choices=list(TASK_ORDERINGS), required=False)


class CommentSerializer(serializers.ModelSerializer):
    author = PublicUserSerializer(read_only=True)
    body = serializers.SerializerMethodField()

    class Meta:
        model = Comment
        fields = ["id", "author", "body", "is_deleted", "created_at", "edited_at"]
        read_only_fields = fields

    def get_body(self, comment: Comment) -> str:
        return DELETED_PLACEHOLDER if comment.is_deleted else comment.body


class CommentWriteSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=MAX_COMMENT_LENGTH)


class TimeEntrySerializer(serializers.ModelSerializer):
    user = PublicUserSerializer(read_only=True)
    task_number = serializers.IntegerField(source="task.number", read_only=True)

    class Meta:
        model = TimeEntry
        fields = ["id", "task_number", "user", "minutes", "date", "note", "created_at"]
        read_only_fields = fields


class TimeEntryCreateSerializer(serializers.Serializer):
    minutes = serializers.IntegerField(
        min_value=TimeEntry.MIN_MINUTES, max_value=TimeEntry.MAX_MINUTES
    )
    date = serializers.DateField()
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class TimeEntryUpdateSerializer(serializers.Serializer):
    minutes = serializers.IntegerField(
        min_value=TimeEntry.MIN_MINUTES, max_value=TimeEntry.MAX_MINUTES, required=False
    )
    date = serializers.DateField(required=False)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True)


class TimeByTaskFilterSerializer(serializers.Serializer):
    sprint = serializers.IntegerField(required=False)


class MyTasksFilterSerializer(serializers.Serializer):
    include_done = serializers.BooleanField(required=False, default=False)


class DateRangeFilterSerializer(serializers.Serializer):
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)


class DependencyCreateSerializer(serializers.Serializer):
    blocker_number = serializers.IntegerField(min_value=1)
