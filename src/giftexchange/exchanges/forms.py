import re

from django import forms
from django.conf import settings
from django.core.validators import validate_email
from django.db import transaction

from .models import Circle, Household, Person, Rule


def household_named(circle, name):
    """The circle's household with this name (ignoring case), created if
    needed. None for a blank name."""
    name = name.strip()
    if not name:
        return None
    existing = circle.households.filter(name__iexact=name).first()
    return existing or Household.objects.create(circle=circle, name=name)


def remove_empty_households(circle):
    circle.households.filter(members=None).delete()


def check_room(circle, adding):
    limit = settings.MAX_PEOPLE_PER_CIRCLE
    if circle.people.count() + adding > limit:
        raise forms.ValidationError(f"A circle can have up to {limit} people.")


class CircleForm(forms.ModelForm):
    class Meta:
        model = Circle
        fields = ["name"]
        labels = {"name": "Name"}
        help_texts = {"name": 'For example, "The Smith family".'}

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        circles = Circle.objects.filter(owner=self.owner).exclude(pk=self.instance.pk)
        if circles.filter(name__iexact=name).exists():
            raise forms.ValidationError("You already have a circle with this name.")
        return name

    def clean(self):
        limit = settings.MAX_CIRCLES_PER_USER
        is_new = self.instance.pk is None
        if is_new and Circle.objects.filter(owner=self.owner).count() >= limit:
            raise forms.ValidationError(f"You can have up to {limit} circles.")
        return super().clean()

    def save(self):
        self.instance.owner = self.owner
        return super().save()


class PersonForm(forms.ModelForm):
    name = forms.CharField(max_length=100)
    email = forms.EmailField(label="Email address")
    household = forms.CharField(
        required=False,
        max_length=100,
        help_text="People in the same household never give to each other.",
        widget=forms.TextInput(attrs={"list": "households"}),
    )

    field_order = ["name", "email", "household", "active"]

    class Meta:
        model = Person
        # The household is a name here, not the model's field.
        fields = ["name", "email", "active"]
        labels = {"active": "Include in new exchanges"}

    def __init__(self, *args, circle, **kwargs):
        super().__init__(*args, **kwargs)
        self.circle = circle
        if self.instance.household:
            self.initial["household"] = self.instance.household.name
        if self.instance.pk is None:
            del self.fields["active"]

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        people = self.circle.people.exclude(pk=self.instance.pk)
        if people.filter(name__iexact=name).exists():
            raise forms.ValidationError("There's already someone with this name.")
        return name

    def clean(self):
        if self.instance.pk is None:
            check_room(self.circle, 1)
        return super().clean()

    @transaction.atomic
    def save(self):
        person = self.instance
        person.circle = self.circle
        person.household = household_named(self.circle, self.cleaned_data["household"])
        person.save()
        remove_empty_households(self.circle)
        return person


class PasteForm(forms.Form):
    """Lines of "Name, email" or "Name, email, household"."""

    text = forms.CharField(
        label="One person per line",
        widget=forms.Textarea(
            attrs={
                "placeholder": "Alice, alice@example.org, Smiths\nBob, bob@example.org"
            }
        ),
        help_text="Name, email address, and optionally a household. "
        "Separate them with commas or tabs.",
    )

    def __init__(self, *args, circle, **kwargs):
        super().__init__(*args, **kwargs)
        self.circle = circle

    def clean_text(self):
        taken = {
            name.lower() for name in self.circle.people.values_list("name", flat=True)
        }
        people, errors = [], []
        lines = self.cleaned_data["text"].splitlines()
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            parts = [part.strip() for part in re.split(r"[,\t]", line)]
            if len(parts) not in (2, 3) or not parts[0]:
                errors.append(f"Line {number}: expected a name and an email address.")
                continue
            name, email, household = (*parts, "")[:3]
            try:
                validate_email(email)
            except forms.ValidationError:
                errors.append(f"Line {number}: {email!r} isn't an email address.")
                continue
            if name.lower() in taken:
                errors.append(f"Line {number}: there's already someone called {name}.")
                continue
            taken.add(name.lower())
            people.append((name, email, household))
        if errors:
            raise forms.ValidationError(errors)
        if not people:
            raise forms.ValidationError("There's nobody to add.")
        check_room(self.circle, len(people))
        return people

    @transaction.atomic
    def save(self):
        for name, email, household in self.cleaned_data["text"]:
            Person.objects.create(
                circle=self.circle,
                name=name,
                email=email,
                household=household_named(self.circle, household),
            )
        return len(self.cleaned_data["text"])


class RuleForm(forms.Form):
    """A standing rule. "Both ways" makes two rules."""

    KINDS = [
        (Rule.Kind.EXCLUDE, "can't give to"),
        (Rule.Kind.REQUIRE, "must give to"),
    ]

    giver = forms.ModelChoiceField(queryset=Person.objects.none(), label="Giver")
    kind = forms.ChoiceField(choices=KINDS, label="Rule")
    receiver = forms.ModelChoiceField(queryset=Person.objects.none(), label="Receiver")
    both_ways = forms.BooleanField(
        required=False, label="Both ways (only for “can't give to”)"
    )
    note = forms.CharField(
        required=False,
        max_length=200,
        help_text="Private to you, e.g. the reason for the rule.",
    )

    def __init__(self, *args, circle, **kwargs):
        super().__init__(*args, **kwargs)
        self.circle = circle
        people = circle.people.filter(active=True).order_by("name")
        self.fields["giver"].queryset = people
        self.fields["receiver"].queryset = people

    def pairs(self):
        giver, receiver = self.cleaned_data["giver"], self.cleaned_data["receiver"]
        if self.cleaned_data.get("both_ways"):
            return [(giver, receiver), (receiver, giver)]
        return [(giver, receiver)]

    def clean(self):
        cleaned = super().clean()
        giver, receiver = cleaned.get("giver"), cleaned.get("receiver")
        kind = cleaned.get("kind")
        if not (giver and receiver and kind):
            return cleaned
        if giver == receiver:
            raise forms.ValidationError("Choose two different people.")
        standing = Rule.objects.filter(circle=self.circle, exchange=None)
        for g, r in self.pairs():
            if standing.filter(giver=g, receiver=r).exists():
                raise forms.ValidationError(f"There's already a rule for {g} and {r}.")
        if kind == Rule.Kind.REQUIRE:
            if cleaned.get("both_ways"):
                raise forms.ValidationError("A “must give to” rule goes one way.")
            if giver.household_id and giver.household_id == receiver.household_id:
                raise forms.ValidationError(
                    f"{giver} and {receiver} are in the same household."
                )
            required = standing.filter(kind=Rule.Kind.REQUIRE)
            if required.filter(giver=giver).exists():
                raise forms.ValidationError(f"{giver} already must give to someone.")
            if required.filter(receiver=receiver).exists():
                raise forms.ValidationError(f"Someone already must give to {receiver}.")
        return cleaned

    @transaction.atomic
    def save(self):
        return [
            Rule.objects.create(
                circle=self.circle,
                giver=g,
                receiver=r,
                kind=self.cleaned_data["kind"],
                note=self.cleaned_data["note"],
            )
            for g, r in self.pairs()
        ]
