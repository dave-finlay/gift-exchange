"""Organizer screens. Every view finds gift exchanges and circles through
the signed-in user, so nobody can reach another organizer's data by
changing an ID."""

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Case, Value, When
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from giftexchange import matching
from giftexchange.audit.models import record

from . import checks, draws, emails, links
from .forms import (
    CircleChoiceForm,
    CircleForm,
    ExchangeForm,
    ParticipantsForm,
    PasteForm,
    PersonForm,
    RenameExchangeForm,
    ResendForm,
    RuleForm,
    new_exchange,
    remove_empty_households,
    use_circle,
)
from .models import Circle, EmailLog, Exchange, Participant, Person, Rule


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
    exchanges = exchanges.prefetch_related("participants")
    cards = [(exchange, *card(exchange)) for exchange in exchanges]
    circles = request.user.circles.order_by("name")
    return render(request, "exchanges/home.html", {"cards": cards, "circles": circles})


def card(exchange):
    """A gift exchange's progress line and next action (label, URL) for its
    card on the home page."""
    people = [p for p in exchange.participants.all() if p.withdrawn_at is None]
    status = exchange.status
    if status == Exchange.Status.DRAFT:
        line = f"{len(people)} people" if exchange.circle else ""
        if exchange.confirm_first:
            action = ("Send “Are you in?”", "exchange_send_join")
        else:
            action = ("Draw and send", "exchange_draw")
    elif status == Exchange.Status.JOINING:
        yes = sum(p.response == Participant.Response.IN for p in people)
        line = f"{yes} of {len(people)} in"
        action = ("Draw and send", "exchange_draw")
    elif status == Exchange.Status.SENT:
        drawn = [p for p in people if p.response != Participant.Response.DECLINED]
        looked = sum(p.revealed_at is not None for p in drawn)
        bought = sum(p.gift_status != Participant.GiftStatus.NOT_YET for p in drawn)
        line = f"{looked} of {len(drawn)} have looked · {bought} gifts bought"
        action = ("End gift exchange", "exchange_end")
    else:
        line, action = "", None
    if action:
        action = (action[0], reverse(action[1], args=[exchange.pk]))
    return line, action


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
    """Delete a circle and its people. Gift exchanges under way are called
    off and deleted too (optionally emailing everyone), as are ended ones;
    drafts stay, without a circle."""
    circle = own_circle(request, pk)
    busy = list(
        circle.exchanges.filter(
            status__in=[Exchange.Status.JOINING, Exchange.Status.SENT]
        )
    )
    if request.method == "POST":
        notify = bool(request.POST.get("notify"))
        try:
            for exchange in busy:
                call_off(request, exchange, notify)
        except emails.TooManyEmails as error:
            messages.error(request, str(error))
            return redirect("circle_delete", pk)
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
    """A draft's setup form; once it's under way, its progress."""
    exchange = own_exchange(request, pk)
    if exchange.status != Exchange.Status.DRAFT:
        return render(
            request,
            "exchanges/exchange_progress.html",
            {
                "exchange": exchange,
                "circle": exchange.circle,
                **circle_names(exchange),
                "progress": progress(exchange),
            },
        )
    return setup_page(request, exchange)


def circle_names(exchange):
    """Some of the circle's people, for its card."""
    names = []
    if exchange.circle:
        names = list(
            exchange.circle.people.order_by("name").values_list("name", flat=True)
        )
    return {"names": names[:NAMES_ON_CARD], "more": max(0, len(names) - NAMES_ON_CARD)}


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
    context = {
        "exchange": exchange,
        "circle": exchange.circle,
        **circle_names(exchange),
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


def progress(exchange):
    """One row per participant, once emails have gone out. Never shows who
    has whom: only responses, emails, whether they've looked, and gifts."""
    if exchange.status == Exchange.Status.DRAFT:
        return None
    latest = {}
    for log in EmailLog.objects.filter(participant__exchange=exchange).order_by(
        "sent_at"
    ):
        latest[log.participant_id] = log
    rows = []
    participants = exchange.participants.select_related("person", "assignment")
    for participant in participants.order_by("person__name"):
        participant.exchange = exchange
        participant.latest_email = latest.get(participant.pk)
        participant.can_resend = emails.latest_kind(participant) is not None
        rows.append(participant)
    return rows


def readiness(exchange):
    """What's missing before emails can go out."""
    problems = []
    if not exchange.circle:
        problems.append("Choose who it's for.")
    if not exchange.gift_date:
        problems.append("Set the gift date, under Details and Settings.")
    if exchange.participants.in_draw().count() < 2:
        problems.append("At least 2 people need to be taking part.")
    return problems


@login_required
def exchange_send_join(request, pk):
    """Send the "Are you in?" emails: Draft → Joining."""
    exchange = own_exchange(request, pk)
    if exchange.status != Exchange.Status.DRAFT or not exchange.confirm_first:
        return redirect("exchange", pk)
    problems = readiness(exchange)
    participants = exchange.participants.select_related("person").order_by(
        "person__name"
    )
    if request.method == "POST" and not problems:
        try:
            failed = emails.send(request, exchange, participants, "join")
        except emails.TooManyEmails as error:
            messages.error(request, str(error))
            return redirect("exchange", pk)
        exchange.status = Exchange.Status.JOINING
        exchange.save(update_fields=["status"])
        record(request, "exchange.join_sent", f"exchange:{pk}")
        sent_message(request, len(participants), failed)
        return redirect("home")
    return render(
        request,
        "exchanges/send_join.html",
        {
            "exchange": exchange,
            "problems": problems,
            "participants": participants,
            **email_summary(exchange, participants, "join"),
        },
    )


@login_required
def exchange_draw(request, pk):
    """Draw and send: make the draw and email everyone their private link.
    One action, after a confirmation page; the organizer never sees it."""
    exchange = own_exchange(request, pk)
    joining = exchange.status == Exchange.Status.JOINING
    direct = exchange.status == Exchange.Status.DRAFT and not exchange.confirm_first
    if not (joining or direct):
        return redirect("exchange", pk)
    pending = exchange.participants.in_draw().filter(
        response=Participant.Response.PENDING
    )
    problems = readiness(exchange)
    if request.method == "POST" and not problems:
        if joining and request.POST.get("leave_out_pending"):
            pending.update(response=Participant.Response.DECLINED)
        taking_part = exchange.participants.in_draw()
        try:
            emails.check_daily_limit(exchange, taking_part.count())
        except emails.TooManyEmails as error:
            messages.error(request, str(error))
            return redirect("exchange", pk)
        try:
            result = draws.draw(exchange)
        except draws.RuleConflict:
            result = None
        if not isinstance(result, matching.Draw):
            messages.error(
                request, "No draw is possible with these rules. Check the rules below."
            )
            return redirect(reverse("exchange", args=[pk]) + "?check#check")
        exchange.status = Exchange.Status.SENT
        exchange.sent_at = timezone.now()
        exchange.save(update_fields=["status", "sent_at"])
        record(request, "exchange.draw", f"exchange:{pk}")
        failed = emails.send(
            request, exchange, taking_part.select_related("person"), "assignment"
        )
        sent_message(request, taking_part.count(), failed)
        return redirect("home")
    taking_part = exchange.participants.in_draw().select_related("person")
    return render(
        request,
        "exchanges/draw.html",
        {
            "exchange": exchange,
            "problems": problems,
            "joining": joining,
            "pending": pending.count(),
            "participants": taking_part.order_by("person__name"),
            "check": None if problems else checks.check(exchange),
            **email_summary(exchange, taking_part, "assignment"),
        },
    )


@login_required
def exchange_resend(request, pk, participant_pk):
    """Send one person their latest email again: for one that failed, went
    to the wrong address, or was lost. Optionally with a new link, so the
    old one stops working; always when the address changes, since the old
    link went somewhere else."""
    exchange = own_exchange(request, pk)
    participant = get_object_or_404(
        exchange.participants.select_related("person"), pk=participant_pk
    )
    person = participant.person
    kind = emails.latest_kind(participant)
    if kind is None:
        messages.error(request, f"There's nothing to send {person.name} now.")
        return redirect("exchange", pk)
    form = ResendForm(request.POST or None, initial={"email": person.email})
    if request.method == "POST" and form.is_valid():
        try:
            emails.check_daily_limit(exchange, 1)
        except emails.TooManyEmails as error:
            messages.error(request, str(error))
            return redirect("exchange", pk)
        email = form.cleaned_data["email"]
        new_link = form.cleaned_data["new_link"] or email != person.email
        if email != person.email:
            person.email = email
            person.save(update_fields=["email"])
            record(request, "person.email", f"person:{person.pk}")
        if new_link:
            links.rotate(participant)
            record(request, "participant.new_link", f"participant:{participant.pk}")
        if emails.send(request, exchange, [participant], kind, new_link=new_link):
            messages.error(request, f"The email to {person.name} couldn't be sent.")
        elif new_link:
            messages.success(request, f"Sent {person.name} a new link.")
        else:
            messages.success(request, f"Sent {person.name} their email again.")
        return redirect("exchange", pk)
    subject, _, _ = emails.compose(exchange, person, kind, "#")
    return render(
        request,
        "exchanges/exchange_resend.html",
        {
            "exchange": exchange,
            "person": person,
            "form": form,
            "subject": subject,
            "latest": participant.emails.order_by("-sent_at").first(),
        },
    )


def email_summary(exchange, participants, kind):
    """What a summary page shows about the email it's about to send."""
    first = next(iter(participants), None)
    person = first.person if first else Person(name="Alex")
    subject, _, _ = emails.compose(exchange, person, kind, "#")
    return {
        "subject": subject,
        "sender": emails.sender(exchange),
        "preview": reverse("exchange_preview", args=[exchange.pk, kind]) + "?embed",
    }


def sent_message(request, count, failed):
    if failed:
        messages.error(
            request,
            f"{failed} of {count} emails couldn't be sent. See the list below.",
        )
    else:
        messages.success(request, f"Sent {count} emails.")


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


PREVIEW_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; "
    "form-action 'none'; frame-ancestors 'self'; base-uri 'none'"
)


@login_required
@xframe_options_sameorigin
def exchange_preview(request, pk, kind):
    """An email exactly as it will look, addressed to one of the people
    taking part, with a bar on top to switch between the emails."""
    exchange = own_exchange(request, pk)
    if kind not in ("join", "assignment"):
        raise Http404
    first = exchange.participants.select_related("person").order_by("person__name")
    person = first[0].person if first else Person(name="Alex")
    subject, _, html = emails.compose(exchange, person, kind, "#preview")
    if "embed" in request.GET:  # Inside a summary page, which shows the rest
        response = HttpResponse(html)
        response["Content-Security-Policy"] = PREVIEW_CSP
        return response
    bar = render_to_string(
        "exchanges/preview_bar.html",
        {
            "exchange": exchange,
            "kind": kind,
            "subject": subject,
            "sender": emails.sender(exchange),
        },
        request=request,
    )
    start = html.index(">", html.index("<body")) + 1
    response = HttpResponse(html[:start] + bar + html[start:])
    # Emails need inline styles, which the site's own pages don't allow.
    response["Content-Security-Policy"] = PREVIEW_CSP
    return response


@login_required
def exchange_rename(request, pk):
    """Rename a gift exchange, at any stage: participants just see the new
    name on their page."""
    exchange = own_exchange(request, pk)
    form = RenameExchangeForm(request.POST or None, instance=exchange)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("exchange", pk)
    return render(
        request, "exchanges/exchange_rename.html", {"exchange": exchange, "form": form}
    )


@login_required
def exchange_delete(request, pk):
    """Delete a gift exchange. If it's under way, it's called off, and the
    organizer can email everyone to say so."""
    exchange = own_exchange(request, pk)
    busy = exchange.status in (Exchange.Status.JOINING, Exchange.Status.SENT)
    if request.method == "POST":
        try:
            call_off(request, exchange, notify=bool(request.POST.get("notify")))
        except emails.TooManyEmails as error:
            messages.error(request, str(error))
            return redirect("exchange_delete", pk)
        messages.success(request, f"Deleted {exchange.name}.")
        return redirect("home")
    return render(
        request, "exchanges/exchange_delete.html", {"exchange": exchange, "busy": busy}
    )


def call_off(request, exchange, notify):
    """Delete a gift exchange, first emailing the people still in it that
    it's been called off, if it's under way and the organizer wants to."""
    under_way = exchange.status in (Exchange.Status.JOINING, Exchange.Status.SENT)
    if notify and under_way:
        still_in = (
            exchange.participants.filter(withdrawn_at=None)
            .exclude(response=Participant.Response.DECLINED)
            .select_related("person")
        )
        emails.send(request, exchange, still_in, EmailLog.Kind.CALLED_OFF)
    record(request, "exchange.delete", f"exchange:{exchange.pk}")
    exchange.delete()


@login_required
@require_POST
def exchange_save(request, pk):
    """Save the setup form: who's taking part, details and settings. Then
    home, or on to adding a rule or checking the rules, without losing
    what was typed."""
    exchange = own_exchange(request, pk)
    if not editable(request, exchange):
        return redirect("exchange", pk)
    basics = ExchangeForm(request.POST, instance=exchange)
    people = (
        ParticipantsForm(request.POST, exchange=exchange) if exchange.circle else None
    )
    if not basics.is_valid() or (people and not people.is_valid()):
        return setup_page(request, exchange, basics=basics, people=people)
    with transaction.atomic():
        basics.save()
        if people:
            people.save()
    then = request.POST.get("then")
    if then == "check":
        return redirect(reverse("exchange", args=[pk]) + "?check#check")
    if then == "add_rule" and exchange.circle:
        return redirect("exchange_rule_new", pk)
    messages.success(request, f"Saved {exchange.name}.")
    return redirect("home")


@login_required
def exchange_end(request, pk):
    """End a gift exchange once the gifts are given: everyone's link then
    says it has ended. Nobody is emailed."""
    exchange = own_exchange(request, pk)
    if exchange.status != Exchange.Status.SENT:
        return redirect("exchange", pk)
    if request.method == "POST":
        exchange.status = Exchange.Status.ARCHIVED
        exchange.archived_at = timezone.now()
        exchange.save(update_fields=["status", "archived_at"])
        record(request, "exchange.end", f"exchange:{pk}")
        messages.success(request, f"Ended {exchange.name}.")
        return redirect("home")
    line, _ = card(exchange)
    return render(
        request, "exchanges/exchange_end.html", {"exchange": exchange, "line": line}
    )


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
