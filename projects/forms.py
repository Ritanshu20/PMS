from django import forms

from core.forms import BootstrapFormMixin
from projects.models import ProjectMember


class ProjectForm(BootstrapFormMixin, forms.Form):
    name = forms.CharField(max_length=120)
    key = forms.CharField(
        max_length=6,
        help_text="2 to 6 letters, e.g. WEB. Task keys look like WEB-12.",
    )
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)


class MemberAddForm(BootstrapFormMixin, forms.Form):
    email = forms.EmailField(help_text="Exact email of a registered user.")
    role = forms.ChoiceField(choices=ProjectMember.Role.choices, initial=ProjectMember.Role.MEMBER)


class MemberRoleForm(BootstrapFormMixin, forms.Form):
    role = forms.ChoiceField(choices=ProjectMember.Role.choices)
