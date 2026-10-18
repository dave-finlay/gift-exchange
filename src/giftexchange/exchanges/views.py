"""Organizer screens. Every view finds gift exchanges and circles through
the signed-in user, so nobody can reach another organizer's data by
changing an ID."""

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Case, Value, When
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from giftexchange.audit.models import record

from . import checks
from .forms import (
    CircleChoiceForm,
    CircleForm,
    ExchangeForm,
    ParticipantsForm,
    PasteForm,
    PersonForm,
    RuleForm,
    new_exchange,
    remove_empty_households,
    use_circle,
)
from .models import Circle, Exchange, Person, Rule


def own_circle(request, pk):
    return get_object_or_404(Circle, pk=pk, owner=request.user)


@login_required
def home(request):
    """Gift exchanges first (ended ones last), then circles."""
    ended = Case(When(status=Exchange.Status.ARCHIVED, then=Value(1)), default=0)
    exchanges = (
        request.user.exchanges.select_related("circle")
        .annotate(ended=ended)
        .order_by("ended", "-created_at")
    )
    circles = request.user.circles.order_by("name")
    return render(
        request, "exchanges/home.html", {"exchanges": exchanges, "circles": circles}
    )


@login_required
def circle_new(request):
    form = CircleForm(request.POST or None, owner=request.user)
    if request.method == "POST" and form.is_valid():
        circle = form.save()
        return redirect("circle", circle.pk)
    return render(request, "exchanges/circle_new.html", {"form": form})


@login_required
def circle(request, pk):
    circle = own_circle(request, pk)
    people = circle.people.select_related("household").order_by("name")
    households = {}
    for person in people:
        households.setdefault(person.household, []).append(person)
    grouped = sorted(
        ((h, members) for h, members in households.items() if h),
        key=lambda item: item[0].name.lower(),
    )
    rules = (
        Rule.objects.filter(circle=circle, exchange=None)
        .select_related("giver", "receiver")
        .order_by("giver__name", "receiver__name")
    )
    return render(
        request,
        "exchanges/circle.html",
        {
            "circle": circle,
            "households": grouped,
            "on_their_own": households.get(None, []),
            "rules": rules,
            "exchanges": circle.exchanges.order_by("-created_at"),
        },
    )


@login_required
def circle_edit(request, pk):
    circle = own_circle(request, pk)
    form = CircleForm(request.POST or None, instance=circle, owner=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("circle", circle.pk)
    return render(
        request, "exchanges/circle_edit.html", {"circle": circle, "form": form}
    )


@login_required
def circle_delete(request, pk):
    """Delete a circle and its people, unless one of its gift exchanges is
    under way: participants hold links to it. Ended gift exchanges go with
    it; drafts stay, without a circle."""
    circle = own_circle(request, pk)
    busy = circle.exchanges.filter(
        status__in=[Exchange.Status.JOINING, Exchange.Status.SENT]
    )
    if request.method == "POST" and not busy.exists():
        circle.exchanges.filter(status=Exchange.Status.ARCHIVED).delete()
        circle.delete()
        record(request, "circle.delete", f"circle:{pk}")
        messages.success(request, f"Deleted {circle.name}.")
        return redirect("home")
    return render(
        request,
        "exchanges/circle_delete.html",
        {
            "circle": circle,
            "busy": [str(e) for e in busy],
            "people": circle.people.count(),
            "drafts": [
                str(e) for e in circle.exchanges.filter(status=Exchange.Status.DRAFT)
            ],
            "ended": circle.exchanges.filter(status=Exchange.Status.ARCHIVED).count(),
        },
    )


@login_required
def person_new(request, pk):
    return person_form(request, own_circle(request, pk), Person())


@login_required
def person_edit(request, circle_pk, pk):
    circle = own_circle(request, circle_pk)
    return person_form(request, circle, get_object_or_404(Person, pk=pk, circle=circle))


def person_form(request, circle, person):
    form = PersonForm(request.POST or None, instance=person, circle=circle)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("circle", circle.pk)
    households = circle.households.order_by("name").values_list("name", flat=True)
    return render(
        request,
        "exchanges/person_form.html",
        {"circle": circle, "form": form, "person": person, "households": households},
    )


@login_required
def person_delete(request, circle_pk, pk):
    """Delete a person, unless they're in a gift exchange that has started:
    that needs a withdrawal instead, so their giver gets someone new."""
    circle = own_circle(request, circle_pk)
    person = get_object_or_404(Person, pk=pk, circle=circle)
    exchanges = Exchange.objects.filter(participants__person=person)
    busy = exchanges.filter(status__in=[Exchange.Status.JOINING, Exchange.Status.SENT])
    if request.method == "POST" and not busy.exists():
        person.delete()
        remove_empty_households(circle)
        record(request, "person.delete", f"person:{pk}")
        messages.success(request, f"Deleted {person.name}.")
        return redirect("circle", circle.pk)
    return render(
        request,
        "exchanges/person_delete.html",
        {
            "circle": circle,
            "person": person,
            "busy": [str(e) for e in busy],
            "past": exchanges.filter(status=Exchange.Status.ARCHIVED).exists(),
        },
    )


@login_required
def paste(request, pk):
    circle = own_circle(request, pk)
    form = PasteForm(request.POST or None, circle=circle)
    if request.method == "POST" and form.is_valid():
        added = form.save()
        messages.success(
            request, f"Added {added} {'person' if added == 1 else 'people'}."
        )
        return redirect("circle", circle.pk)
    return render(request, "exchanges/paste.html", {"circle": circle, "form": form})


@login_required
def rule_new(request, pk):
    circle = own_circle(request, pk)
    form = RuleForm(request.POST or None, circle=circle)
    if request.method == "POST" and form.is_valid():
        for rule in form.save():
            record(request, "rule.add", f"rule:{rule.pk}")
        return redirect("circle", circle.pk)
    return render(request, "exchanges/rule_form.html", {"circle": circle, "form": form})


@login_required
@require_POST
def rule_delete(request, circle_pk, pk):
    circle = own_circle(request, circle_pk)
    rule = get_object_or_404(Rule, pk=pk, circle=circle, exchange=None)
    rule.delete()
    record(request, "rule.delete", f"rule:{pk}")
    return redirect("circle", circle.pk)


# Gift exchanges


def own_exchange(request, pk):
    return get_object_or_404(
        Exchange.objects.select_related("circle", "owner"), pk=pk, owner=request.user
    )


def editable(request, exchange):
    """Setup can change only while the gift exchange is a draft."""
    if exchange.status == Exchange.Status.DRAFT:
        return True
    messages.error(request, "This gift exchange has started, so it's locked.")
    return False


@login_required
@require_POST
def exchange_new(request):
    """Create a draft straight away and open it: its page asks the rest."""
    try:
        exchange = new_exchange(request.user)
    except forms.ValidationError as error:
        messages.error(request, error.messages[0])
        return redirect("home")
    return redirect("exchange", exchange.pk)


@login_required
def exchange(request, pk):
    exchange = own_exchange(request, pk)
    return setup_page(request, exchange)


NAMES_ON_CARD = 8


def setup_page(request, exchange, **forms):
    """The gift exchange page. Test mode runs only when asked for, with
    ?check; names only with ?details."""
    check = None
    if "check" in request.GET and exchange.circle:
        check = checks.check(exchange)
    rules = exchange.rules.select_related("giver", "receiver").order_by(
        "giver__name", "receiver__name"
    )
    names = []
    if exchange.circle:
        names = list(
            exchange.circle.people.order_by("name").values_list("name", flat=True)
        )
    context = {
        "exchange": exchange,
        "circle": exchange.circle,
        # Some of the circle's people, for its card
        "names": names[:NAMES_ON_CARD],
        "more": max(0, len(names) - NAMES_ON_CARD),
        "draft": exchange.status == Exchange.Status.DRAFT,
        "choose": forms.get("choose") or CircleChoiceForm(exchange=exchange),
        "has_circles": request.user.circles.exists(),
        "basics": forms.get("basics") or ExchangeForm(instance=exchange),
        "people": forms.get("people") or ParticipantsForm(exchange=exchange),
        "rules": rules,
        "check": check,
        "details": "details" in request.GET,
    }
    return render(request, "exchanges/exchange.html", context)


@login_required
@require_POST
def exchange_circle(request, pk):
    """Choose the circle a draft gift exchange is for. Once chosen, it
    stays: changing it is too unusual to be worth offering."""
    exchange = own_exchange(request, pk)
    if exchange.circle or not editable(request, exchange):
        return redirect("exchange", pk)
    form = CircleChoiceForm(request.POST, exchange=exchange)
    if not form.is_valid():
        return setup_page(request, exchange, choose=form)
    use_circle(exchange, form.cleaned_data["circle"])
    return redirect("exchange", pk)


@login_required
@require_POST
def exchange_circle_new(request, pk):
    """Start a new circle for a draft gift exchange, then add its people.
    The circle page links back to the gift exchange."""
    exchange = own_exchange(request, pk)
    if exchange.circle or not editable(request, exchange):
        return redirect("exchange", pk)
    form = CircleForm({"name": ""}, owner=request.user)
    if not form.is_valid():
        messages.error(request, form.errors.as_text())
        return redirect("exchange", pk)
    circle = form.save()
    use_circle(exchange, circle)
    messages.success(request, f"Now add the people taking part in {exchange.name}.")
    return redirect("circle", circle.pk)


@login_required
def exchange_delete(request, pk):
    """Delete a gift exchange, unless it's under way: participants hold
    links to it."""
    exchange = own_exchange(request, pk)
    busy = exchange.status in (Exchange.Status.JOINING, Exchange.Status.SENT)
    if request.method == "POST" and not busy:
        exchange.delete()
        record(request, "exchange.delete", f"exchange:{pk}")
        messages.success(request, f"Deleted {exchange.name}.")
        return redirect("home")
    return render(
        request, "exchanges/exchange_delete.html", {"exchange": exchange, "busy": busy}
    )


@login_required
@require_POST
def exchange_basics(request, pk):
    exchange = own_exchange(request, pk)
    if not editable(request, exchange):
        return redirect("exchange", pk)
    form = ExchangeForm(request.POST, instance=exchange)
    if not form.is_valid():
        return setup_page(request, exchange, basics=form)
    form.save()
    messages.success(request, "Saved.")
    return redirect("exchange", pk)


@login_required
@require_POST
def exchange_people(request, pk):
    exchange = own_exchange(request, pk)
    if not exchange.circle or not editable(request, exchange):
        return redirect("exchange", pk)
    form = ParticipantsForm(request.POST, exchange=exchange)
    if not form.is_valid():
        return setup_page(request, exchange, people=form)
    form.save()
    messages.success(request, "Saved who's taking part.")
    return redirect("exchange", pk)


@login_required
def exchange_rule_new(request, pk):
    exchange = own_exchange(request, pk)
    if not exchange.circle:
        return redirect("exchange", pk)
    if request.method == "POST" and not editable(request, exchange):
        return redirect("exchange", pk)
    form = RuleForm(request.POST or None, circle=exchange.circle, exchange=exchange)
    if request.method == "POST" and form.is_valid():
        for rule in form.save():
            record(request, "rule.add", f"rule:{rule.pk}")
        return redirect("exchange", pk)
    return render(
        request,
        "exchanges/rule_form.html",
        {"circle": exchange.circle, "exchange": exchange, "form": form},
    )


@login_required
@require_POST
def exchange_rule_delete(request, pk, rule_pk):
    exchange = own_exchange(request, pk)
    if not editable(request, exchange):
        return redirect("exchange", pk)
    rule = get_object_or_404(Rule, pk=rule_pk, exchange=exchange)
    rule.delete()
    record(request, "rule.delete", f"rule:{rule_pk}")
    return redirect("exchange", pk)
