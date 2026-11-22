from django import forms

from core.forms import BootstrapFormMixin


class RegisterForm(BootstrapFormMixin, forms.Form):
    username = forms.RegexField(
        regex=r"^[A-Za-z0-9_.-]{3,150}$",
        max_length=150,
        help_text="3+ characters: letters, digits, . _ -. Used for @mentions.",
    )
    email = forms.EmailField()
    display_name = forms.CharField(max_length=150, required=False)
    password = forms.CharField(widget=forms.PasswordInput, strip=False)
    password_confirm = forms.CharField(
        widget=forms.PasswordInput, strip=False, label="Confirm password"
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password") != cleaned.get("password_confirm"):
            self.add_error("password_confirm", "The two passwords do not match.")
        return cleaned
