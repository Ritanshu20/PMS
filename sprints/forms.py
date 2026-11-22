from django import forms

from core.forms import BootstrapFormMixin
from sprints.models import Sprint
from sprints.services import UnfinishedTaskAction

DATE_INPUT = forms.DateInput(attrs={"type": "date"})


class SprintForm(BootstrapFormMixin, forms.Form):
    name = forms.CharField(max_length=100)
    goal = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    start_date = forms.DateField(widget=DATE_INPUT)
    end_date = forms.DateField(widget=DATE_INPUT)


class CompleteSprintForm(BootstrapFormMixin, forms.Form):
    """Choose what happens to unfinished tasks: backlog, or a planned sprint."""

    unfinished_action = forms.ChoiceField(
        choices=[
            (UnfinishedTaskAction.BACKLOG, "Move unfinished tasks to the backlog"),
            (UnfinishedTaskAction.SPRINT, "Move unfinished tasks to a planned sprint"),
        ],
        initial=UnfinishedTaskAction.BACKLOG,
        widget=forms.RadioSelect,
    )
    target_sprint_id = forms.TypedChoiceField(
        coerce=int, empty_value=None, required=False, label="Planned sprint"
    )

    def __init__(self, *args, planned_sprints: list[Sprint], **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target_sprint_id"].choices = [("", "-")] + [
            (sprint.pk, sprint.name) for sprint in planned_sprints
        ]
        self.fields["unfinished_action"].widget.attrs["class"] = "form-check-input"
