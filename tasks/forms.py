"""
Forms for the server-rendered task pages. Forms only check input shape; every
business rule is enforced by the services the views call.
"""

from typing import Any

from django import forms

from core.forms import BootstrapFormMixin
from projects.permissions import ASSIGNABLE_ROLES
from projects.selectors import project_members
from tasks.models import Task, TimeEntry
from tasks.selectors import project_labels

DATE_INPUT = forms.DateInput(attrs={"type": "date"})


class TaskForm(BootstrapFormMixin, forms.Form):
    """Create/edit form. On edit it also carries `status` and the hidden `version`."""

    title = forms.CharField(max_length=200)
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), required=False)
    priority = forms.ChoiceField(choices=Task.Priority.choices, initial=Task.Priority.MEDIUM)
    assignee_id = forms.TypedChoiceField(
        label="Assignee", coerce=int, empty_value=None, required=False
    )
    due_date = forms.DateField(widget=DATE_INPUT, required=False)
    story_points = forms.IntegerField(min_value=0, max_value=100, required=False)
    estimate_minutes = forms.IntegerField(min_value=1, required=False, label="Estimate (minutes)")
    label_ids = forms.TypedMultipleChoiceField(
        label="Labels", coerce=int, required=False, widget=forms.CheckboxSelectMultiple
    )

    def __init__(self, *args: Any, project_id: int, editing: bool = False, **kwargs: Any):
        super().__init__(*args, **kwargs)
        members = project_members(project_id).filter(role__in=ASSIGNABLE_ROLES)
        self.fields["assignee_id"].choices = [("", "Unassigned")] + [
            (member.user_id, member.user.username) for member in members
        ]
        self.fields["label_ids"].choices = [
            (label.pk, label.name) for label in project_labels(project_id)
        ]
        if editing:
            self.fields["status"] = forms.ChoiceField(choices=Task.Status.choices)
            self.fields["version"] = forms.IntegerField(widget=forms.HiddenInput)
        else:
            self.fields["status"] = forms.ChoiceField(
                choices=[(Task.Status.BACKLOG, "Backlog"), (Task.Status.TODO, "To do")]
            )
        self.apply_bootstrap()

    @staticmethod
    def initial_from_task(task: Task) -> dict[str, Any]:
        return {
            "title": task.title,
            "description": task.description,
            "priority": task.priority,
            "assignee_id": task.assignee_id or "",
            "due_date": task.due_date,
            "story_points": task.story_points,
            "estimate_minutes": task.estimate_minutes,
            "label_ids": [label.pk for label in task.labels.all()],
            "status": task.status,
            "version": task.version,
        }


class CommentForm(BootstrapFormMixin, forms.Form):
    body = forms.CharField(
        widget=forms.Textarea(
            attrs={"rows": 3, "placeholder": "Write a comment. Use @username to mention."}
        ),
        max_length=5000,
    )


class DependencyForm(BootstrapFormMixin, forms.Form):
    blocker_number = forms.IntegerField(min_value=1, label="Blocked by task number")


class TimeEntryForm(BootstrapFormMixin, forms.Form):
    minutes = forms.IntegerField(min_value=TimeEntry.MIN_MINUTES, max_value=TimeEntry.MAX_MINUTES)
    date = forms.DateField(widget=DATE_INPUT)
    note = forms.CharField(max_length=255, required=False)
