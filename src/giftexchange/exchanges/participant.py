"""The participant's page, reached through their private link. Participants
have no accounts: the link is their only identity.

Opening the link changes nothing (email scanners open links): it remembers
the participant in this browser's session and redirects to a clean URL,
so the token doesn't linger in the address bar or history. The session
remembers which version of the link it opened, so a new link also shuts
out browsers that opened an old one.
"""

from datetime import timedelta

from django import forms
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from giftexchange.audit.models import record

from . import emails, links, secrecy
from .emails import organizer_name
from .models import Exchange, GiftIdea, Participant

SESSION_KEY = "participant_links"  # {participant ID: link version}

# Emails a participant can have in a day before "Get a new link" stops
# working for them, so it can't be used to flood their inbox.
MAX_EMAILS_PER_PARTICIPANT_PER_DAY = 5


def remember(request, participant):
    known = request.session.get(SESSION_KEY, {})
    request.session[SESSION_KEY] = {
        **known,
        str(participant.pk): links.version(participant),
    }


def open_link(request, token):
    participant = links.participant_for(token)
    if participant is None or participant.exchange.status == Exchange.Status.DRAFT:
        return render(request, "participant/link_unusable.html", status=404)
    remember(request, participant)
    return redirect("participant", participant.pk)


def own_participant(request, pk):
    """The participant, if this browser has opened their current link."""
    opened = request.session.get(SESSION_KEY, {}).get(str(pk))
    if opened is None:
        raise Http404
    participant = get_object_or_404(
        Participant.objects.select_related(
            "exchange__owner", "exchange__circle", "person"
        ),
        pk=pk,
    )
    if (
        opened != links.version(participant)
        or participant.exchange.status == Exchange.Status.DRAFT
    ):
        raise Http404
    return participant


class IdeaForm(forms.ModelForm):
    class Meta:
        model = GiftIdea
        fields = ["text"]
        labels = {"text": "Add a gift idea"}
        widgets = {
            "text": forms.TextInput(
                attrs={"placeholder": "A book, a scarf, https://…", "maxlength": 280}
            )
        }


@never_cache
def page(request, pk, *, receiver=None, idea_form=None):
    participant = own_participant(request, pk)
    exchange = participant.exchange
    if exchange.status == Exchange.Status.ARCHIVED:
        return render(request, "participant/ended.html", {"exchange": exchange})
    taking_part = exchange.participants.in_draw().select_related("person")
    days = (
        (exchange.gift_date - timezone.localdate()).days if exchange.gift_date else None
    )
    context = {
        "participant": participant,
        "exchange": exchange,
        "organizer": organizer_name(exchange),
        "drawn": exchange.status == Exchange.Status.SENT,
        "in_draw": hasattr(participant, "assignment"),
        "days": days,
        "names": sorted(p.person.name for p in taking_part),
        "ideas": participant.gift_ideas.order_by("created_at"),
        "idea_form": idea_form or IdeaForm(),
        "can_add_idea": participant.gift_ideas.count() < GiftIdea.MAX_PER_PARTICIPANT,
        "receiver": receiver,
        "statuses": Participant.GiftStatus.choices,
        "can_get_new_link": emails.latest_kind(participant) is not None,
        # The message from the latest email they've had
        "message": exchange.note
        if exchange.status == Exchange.Status.SENT
        else exchange.join_note,
    }
    return render(request, "participant/page.html", context)


@require_POST
def respond(request, pk):
    participant = own_participant(request, pk)
    if participant.exchange.status != Exchange.Status.JOINING:
        return redirect("participant", pk)
    answer = request.POST.get("response")
    if answer in (Participant.Response.IN, Participant.Response.DECLINED):
        participant.response = answer
        participant.responded_at = timezone.now()
        participant.save(update_fields=["response", "responded_at"])
    return redirect("participant", pk)


@require_POST
@never_cache
def reveal(request, pk):
    """Show who they're buying for: a button press, never just opening the
    page. This is the only place an assignment is decrypted for anyone."""
    participant = own_participant(request, pk)
    try:
        assignment = participant.assignment
    except Participant.assignment.RelatedObjectDoesNotExist:
        return redirect("participant", pk)
    receiver = Participant.objects.select_related("person").get(
        pk=secrecy.receiver_id(assignment), exchange=participant.exchange
    )
    if participant.revealed_at is None:
        participant.revealed_at = timezone.now()
        participant.save(update_fields=["revealed_at"])
    return page(request, pk, receiver=receiver)


@require_POST
def add_idea(request, pk):
    participant = own_participant(request, pk)
    if participant.gift_ideas.count() >= GiftIdea.MAX_PER_PARTICIPANT:
        return redirect("participant", pk)
    form = IdeaForm(request.POST)
    if not form.is_valid():
        return page(request, pk, idea_form=form)
    form.instance.participant = participant
    form.save()
    return redirect("participant", pk)


@require_POST
def delete_idea(request, pk, idea_pk):
    participant = own_participant(request, pk)
    participant.gift_ideas.filter(pk=idea_pk).delete()
    return redirect("participant", pk)


@require_POST
def gift_status(request, pk):
    participant = own_participant(request, pk)
    status = request.POST.get("gift_status")
    if status in Participant.GiftStatus.values and hasattr(participant, "assignment"):
        participant.gift_status = status
        participant.gift_status_at = timezone.now()
        participant.save(update_fields=["gift_status", "gift_status_at"])
    return redirect("participant", pk)


@require_POST
def withdraw(request, pk):
    """Ask the organizer to take them out. It never happens straight away,
    because of forwarded links and accidental taps."""
    participant = own_participant(request, pk)
    if participant.exchange.status == Exchange.Status.SENT:
        participant.withdraw_requested_at = timezone.now()
        participant.save(update_fields=["withdraw_requested_at"])
    return redirect("participant", pk)


@require_POST
def new_link(request, pk):
    """For a link shared by mistake: email them a new one, and the old one
    stops working. This browser stays signed in. Anyone with the old link
    can do this, but the new one goes only to the participant's address."""
    participant = own_participant(request, pk)
    kind = emails.latest_kind(participant)
    if kind is None:
        return redirect("participant", pk)
    organizer = organizer_name(participant.exchange)
    since = timezone.now() - timedelta(days=1)
    recent = participant.emails.filter(sent_at__gte=since).count()
    try:
        emails.check_daily_limit(participant.exchange, 1)
        if recent >= MAX_EMAILS_PER_PARTICIPANT_PER_DAY:
            raise emails.TooManyEmails
    except emails.TooManyEmails:
        messages.error(
            request, f"Too many emails today. Try tomorrow, or ask {organizer}."
        )
        return redirect("participant", pk)
    links.rotate(participant)
    remember(request, participant)
    record(request, "participant.new_link", f"participant:{pk}")
    if emails.send(request, participant.exchange, [participant], kind, new_link=True):
        messages.error(
            request,
            f"Your new link couldn't be emailed. Ask {organizer} to send it again.",
        )
    else:
        messages.success(
            request,
            f"Sent a new link to {participant.person.email}. Older links no "
            "longer work.",
        )
    return redirect("participant", pk)
