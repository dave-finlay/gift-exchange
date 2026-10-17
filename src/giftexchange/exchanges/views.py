"""Organizer screens. Every view finds the circle through the signed-in
user, so nobody can reach another organizer's data by changing an ID."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from giftexchange.audit.models import record

from .forms import CircleForm, PasteForm, PersonForm, RuleForm
from .models import Circle, Exchange, Person, Rule


def own_circle(request, pk):
    return get_object_or_404(Circle, pk=pk, owner=request.user)


@login_required
def home(request):
    latest = Exchange.objects.order_by("-gift_date")
    circles = (
        Circle.objects.filter(owner=request.user)
        .prefetch_related(Prefetch("exchanges", queryset=latest))
        .order_by("name")
    )
    form = CircleForm(owner=request.user)
    return render(request, "exchanges/home.html", {"circles": circles, "form": form})


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
    active = [p for p in people if p.active]
    households = {}
    for person in active:
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
            "inactive": [p for p in people if not p.active],
            "rules": rules,
            "exchanges": circle.exchanges.order_by("-gift_date"),
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
