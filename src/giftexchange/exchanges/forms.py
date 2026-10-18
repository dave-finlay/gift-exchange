from django import forms
from django.conf import settings
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q

from .models import Circle, Exchange, Household, Person, Rule


def household_named(circle, name):
    """The circle's household with this name (ignoring case), created if
    needed. None for a blank name."""
    name = name.strip()
    if not name:
        return None
    existing = circle.households.filter(name__iexact=name).first()
    return existing or Household.objects.create(circle=circle, name=name)


def numbered(name, taken):
    """`name`, or "name 2", "name 3"… if that's taken (ignoring case)."""
    taken = {t.lower() for t in taken}
    candidate, number = name, 1
    while candidate.lower() in taken:
        number += 1
        candidate = f"{name} {number}"
    return candidate


def join_drafts(person):
    """A new person starts off taking part in the circle's draft gift
    exchanges, which are still being set up."""
    for exchange in person.circle.exchanges.filter(status=Exchange.Status.DRAFT):
        exchange.participants.get_or_create(person=person)


def remove_empty_households(circle):
    circle.households.filter(members=None).delete()


def check_room(circle, adding):
    limit = settings.MAX_PEOPLE_PER_CIRCLE
    if circle.people.count() + adding > limit:
        raise forms.ValidationError(f"A circle can have up to {limit} people.")


class CircleForm(forms.ModelForm):
    """A new circle's name is optional; renaming needs one."""

    DEFAULT_NAME = "My Circle"

    name = forms.CharField(max_length=100, required=False)

    class Meta:
        model = Circle
        fields = ["name"]

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner
        field = self.fields["name"]
        if self.instance.pk is None:
            field.label = "Name (optional)"
            field.help_text = (
                'For example, "The Smith family". '
                f'Leave it blank for "{self.DEFAULT_NAME}".'
            )
        else:
            field.required = True

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        circles = Circle.objects.filter(owner=self.owner).exclude(pk=self.instance.pk)
        taken = {n.lower() for n in circles.values_list("name", flat=True)}
        if not name:
            name = numbered(self.DEFAULT_NAME, taken)
        if name.lower() in taken:
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

    field_order = ["name", "email", "household"]

    class Meta:
        model = Person
        # The household is a name here, not the model's field.
        fields = ["name", "email"]

    def __init__(self, *args, circle, **kwargs):
        super().__init__(*args, **kwargs)
        self.circle = circle
        if self.instance.household:
            self.initial["household"] = self.instance.household.name

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
        is_new = person.pk is None
        person.circle = self.circle
        person.household = household_named(self.circle, self.cleaned_data["household"])
        person.save()
        if is_new:
            join_drafts(person)
        remove_empty_households(self.circle)
        return person


def split_line(line):
    """A pasted line's (name, email, household) fields, or None.

    Commas and tabs separate fields, and a run of them counts as one,
    since lines copied from spreadsheets and emails mix them. "Name email"
    and "Name <email>" (from an email's To line) work too. Plain string
    operations, not regular expressions, so time stays linear in the
    line's length whatever is pasted.
    """
    fields = [f.strip() for f in line.replace("\t", ",").split(",")]
    fields = [f for f in fields if f]
    if fields and (name_email := _name_and_email(fields[0])):
        fields = [*name_email, *fields[1:]]
    if len(fields) not in (2, 3):
        return None
    return (*fields, "")[:3]


def _name_and_email(text):
    """ "Name <email>" or "Name email" split into (name, email), or None."""
    if text.endswith(">") and "<" in text:
        name, _, email = text[:-1].rpartition("<")
    else:
        words = text.rsplit(None, 1)
        if len(words) != 2:
            return None
        name, email = words
    name = name.strip().strip("\"'").strip()
    email = email.strip()
    if not name or "@" not in email or "@" in name:
        return None
    return name, email


class PasteForm(forms.Form):
    """Lines of "Name, email" or "Name, email, household". Also "Name
    email" and "Name <email>"."""

    text = forms.CharField(
        label="One person per line",
        max_length=20_000,
        widget=forms.Textarea(
            attrs={
                "placeholder": "Alice, alice@example.org, Smiths\nBob, bob@example.org"
            }
        ),
        help_text="Name, email address, and optionally a household, separated by "
        'commas or tabs. "Name <email>", copied from an email, works too.',
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
            fields = split_line(line)
            if fields is None:
                errors.append(f"Line {number}: expected a name and an email address.")
                continue
            name, email, household = fields
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
            person = Person.objects.create(
                circle=self.circle,
                name=name,
                email=email,
                household=household_named(self.circle, household),
            )
            join_drafts(person)
        return len(self.cleaned_data["text"])


class RuleForm(forms.Form):
    """A standing rule, or with `exchange`, a rule for that exchange only.
    "Both ways" makes two rules."""

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

    def __init__(self, *args, circle, exchange=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.circle = circle
        self.exchange = exchange
        people = circle.people.order_by("name")
        if exchange:
            people = people.filter(participations__exchange=exchange)
        self.fields["giver"].queryset = people
        self.fields["receiver"].queryset = people

    def rules_in_force(self):
        """The rules this one must be consistent with: standing rules, plus
        this exchange's own."""
        scope = Q(exchange=None)
        if self.exchange:
            scope |= Q(exchange=self.exchange)
        return Rule.objects.filter(scope, circle=self.circle)

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
        rules = self.rules_in_force()
        for g, r in self.pairs():
            if rules.filter(giver=g, receiver=r).exists():
                raise forms.ValidationError(f"There's already a rule for {g} and {r}.")
        if kind == Rule.Kind.REQUIRE:
            if cleaned.get("both_ways"):
                raise forms.ValidationError("A “must give to” rule goes one way.")
            if giver.household_id and giver.household_id == receiver.household_id:
                raise forms.ValidationError(
                    f"{giver} and {receiver} are in the same household."
                )
            required = rules.filter(kind=Rule.Kind.REQUIRE)
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
                exchange=self.exchange,
                giver=g,
                receiver=r,
                kind=self.cleaned_data["kind"],
                note=self.cleaned_data["note"],
            )
            for g, r in self.pairs()
        ]


DEFAULT_EXCHANGE_NAME = "My Gift Exchange"


def check_yearly_limit(circle, gift_date, exchange):
    limit = settings.MAX_EXCHANGES_PER_CIRCLE_PER_YEAR
    if not (circle and gift_date):
        return
    same_year = circle.exchanges.filter(gift_date__year=gift_date.year)
    if same_year.exclude(pk=exchange.pk).count() >= limit:
        raise forms.ValidationError(
            f"A circle can have up to {limit} gift exchanges a year."
        )


def new_exchange(owner):
    """A new draft gift exchange, named "My Gift Exchange" (or "… 2", …)."""
    limit = settings.MAX_EXCHANGES_PER_USER
    current = owner.exchanges.exclude(status=Exchange.Status.ARCHIVED)
    if current.count() >= limit:
        raise forms.ValidationError(f"You can have up to {limit} gift exchanges.")
    names = owner.exchanges.values_list("name", flat=True)
    return Exchange.objects.create(
        owner=owner, name=numbered(DEFAULT_EXCHANGE_NAME, names)
    )


@transaction.atomic
def use_circle(exchange, circle):
    """Set the circle a draft gift exchange is for: everyone in it starts
    off taking part."""
    exchange.circle = circle
    exchange.save(update_fields=["circle"])
    for person in circle.people.all():
        exchange.participants.create(person=person)


class CircleChoiceForm(forms.Form):
    """Which of the organizer's circles a gift exchange is for."""

    circle = forms.ModelChoiceField(
        queryset=Circle.objects.none(), label="Circle", empty_label=None
    )

    def __init__(self, *args, exchange, **kwargs):
        super().__init__(*args, **kwargs)
        self.exchange = exchange
        self.fields["circle"].queryset = exchange.owner.circles.order_by("name")

    def clean_circle(self):
        circle = self.cleaned_data["circle"]
        check_yearly_limit(circle, self.exchange.gift_date, self.exchange)
        return circle


class ExchangeForm(forms.ModelForm):
    """The basics and settings of a gift exchange."""

    class Meta:
        model = Exchange
        fields = [
            "name",
            "gift_date",
            "draw_date",
            "budget",
            "note",
            "confirm_first",
            "avoid_repeats",
            "allow_mutual",
            "single_loop",
        ]
        widgets = {
            # Browsers' date pickers need ISO dates.
            "gift_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "draw_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "avoid_repeats": forms.NumberInput(attrs={"min": 0, "max": 5}),
        }
        labels = {
            "gift_date": "Gift date",
            "draw_date": "Draw date (optional)",
            "budget": "Budget (optional)",
            "note": "Note to everyone (optional)",
            "confirm_first": "Ask people to confirm they're in before the draw",
            "avoid_repeats": "Avoid pairings from the last … exchanges",
            "allow_mutual": "Allow mutual pairs (A gives to B, and B gives to A)",
            "single_loop": "One single loop through everyone (A → B → C → … → A)",
        }
        help_texts = {
            "name": 'For example, "Christmas 2026".',
            "draw_date": "Shown to participants. You start the draw yourself.",
            "budget": 'For example, "About $30".',
            "note": "Included in the emails.",
            "avoid_repeats": "0 to allow repeats.",
            "single_loop": "With a single loop, mutual pairs can't happen anyway.",
        }

    def clean(self):
        cleaned = super().clean()
        gift_date, draw_date = cleaned.get("gift_date"), cleaned.get("draw_date")
        if gift_date and draw_date and draw_date > gift_date:
            self.add_error("draw_date", "The draw has to be before the gift date.")
        check_yearly_limit(self.instance.circle, gift_date, self.instance)
        return cleaned


class ParticipantsForm(forms.Form):
    """Who's taking part: a tick box for each person in the circle."""

    people = forms.ModelMultipleChoiceField(
        queryset=Person.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Taking part",
    )

    def __init__(self, *args, exchange, **kwargs):
        super().__init__(*args, **kwargs)
        self.exchange = exchange
        if exchange.circle:
            people = exchange.circle.people.order_by("name")
            self.fields["people"].queryset = people
        self.initial["people"] = list(
            exchange.participants.values_list("person_id", flat=True)
        )

    @transaction.atomic
    def save(self):
        chosen = set(self.cleaned_data["people"])
        current = {
            p.person: p for p in self.exchange.participants.select_related("person")
        }
        for person, participant in current.items():
            if person not in chosen:
                participant.delete()
        for person in chosen - current.keys():
            self.exchange.participants.create(person=person)
        # Rules for this exchange only matter while both people take part,
        # so they're kept: ticking someone again brings them back.
