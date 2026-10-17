from django import forms
from django.contrib.auth import password_validation

from .models import User


class InviteForm(forms.Form):
    email = forms.EmailField(label="Their email address")

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("There's already an account for this address.")
        return email


class AcceptForm(forms.Form):
    """Creating an account from an invitation. The email address comes from
    the invitation, which proves the person can read it."""

    display_name = forms.CharField(
        label="Your name",
        max_length=100,
        help_text="Shown to the people in your exchanges.",
    )
    password1 = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="At least 10 characters. A password manager helps.",
    )
    password2 = forms.CharField(
        label="Password again",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, *args, email, **kwargs):
        super().__init__(*args, **kwargs)
        self.email = email

    def clean(self):
        cleaned = super().clean()
        password1, password2 = cleaned.get("password1"), cleaned.get("password2")
        if password1 and password2:
            if password1 != password2:
                self.add_error("password2", "The two passwords don't match.")
            else:
                user = User(email=self.email, display_name=cleaned.get("display_name"))
                try:
                    password_validation.validate_password(password1, user)
                except forms.ValidationError as error:
                    self.add_error("password1", error)
        return cleaned
