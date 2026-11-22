from django import forms


class BootstrapFormMixin:
    """Adds Bootstrap 5 CSS classes to every widget so templates stay simple.

    Forms that add fields in their own __init__ call `apply_bootstrap()` again
    at the end (it is safe to call twice).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_bootstrap()

    def apply_bootstrap(self) -> None:
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput | forms.CheckboxSelectMultiple):
                css_class = "form-check-input"
            elif isinstance(widget, forms.Select):
                css_class = "form-select"
            else:
                css_class = "form-control"
            widget.attrs["class"] = css_class
