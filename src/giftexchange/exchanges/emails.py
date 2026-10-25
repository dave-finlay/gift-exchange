"""Emails to participants. They carry a private link, never the assignment
itself, so the email provider never learns who has whom."""

import logging
from datetime import timedelta
from email.utils import formataddr, parseaddr

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from . import links
from .models import EmailLog, Exchange

logger = logging.getLogger(__name__)


class TooManyEmails(Exception):
    pass


def organizer_name(exchange):
    return exchange.owner.display_name or exchange.owner.email


def sender(exchange):
    """ "Bob via Gift Exchange <hello@…>": the site's address, with the
    organizer's name, since it's sent on their behalf."""
    _, address = parseaddr(settings.EMAIL_FROM)
    return formataddr((f"{organizer_name(exchange)} via {settings.SITE_NAME}", address))


def check_daily_limit(exchange, adding):
    limit = settings.MAX_EMAILS_PER_EXCHANGE_PER_DAY
    since = timezone.now() - timedelta(days=1)
    sent = EmailLog.objects.filter(
        participant__exchange=exchange, sent_at__gte=since
    ).count()
    if sent + adding > limit:
        raise TooManyEmails(
            f"A gift exchange can send up to {limit} emails a day. Try again tomorrow."
        )


def latest_kind(participant):
    """The email that brings a participant up to date, to send again (or
    with a new link): None if they've nothing to look at."""
    status = participant.exchange.status
    if participant.withdrawn_at:
        return None
    if status == Exchange.Status.JOINING:
        return EmailLog.Kind.JOIN
    if status == Exchange.Status.SENT and hasattr(participant, "assignment"):
        return EmailLog.Kind.ASSIGNMENT
    return None


def compose(exchange, person, kind, link, *, shared_address=False, new_link=False):
    """An email's (subject, plain text, HTML). Used for sending and for the
    organizer's preview, so the preview is exactly what's sent."""
    context = {
        "exchange": exchange,
        "person": person,
        "organizer": organizer_name(exchange),
        "link": link,
        "new_link": new_link,
        "site_name": settings.SITE_NAME,
        # The organizer's own message for this email, if any
        "message": exchange.join_note if kind == "join" else exchange.note,
    }
    subject = render_to_string(f"emails/{kind}_subject.txt", context).strip()
    if shared_address:
        subject = f"For {person.name}: {subject}"
    text = render_to_string(f"emails/{kind}.txt", context)
    html = render_to_string(f"emails/{kind}.html", context)
    return subject, text, html


def send(request, exchange, participants, kind, *, new_link=False):
    """Send one `kind` email (join, assignment, …) to each participant.
    Returns how many failed; each attempt is logged, without its body.
    `new_link` says their link has changed."""
    participants = list(participants)
    check_daily_limit(exchange, len(participants))
    # People sharing an address each get their own email, named in the
    # subject.
    addresses = [p.person.email.lower() for p in exchange.participants.all()]
    failed = 0
    for participant in participants:
        person = participant.person
        link = request.build_absolute_uri(
            reverse("participant_link", args=[links.token(participant)])
        )
        shared = addresses.count(person.email.lower()) > 1
        subject, text, html = compose(
            exchange, person, kind, link, shared_address=shared, new_link=new_link
        )
        message = EmailMultiAlternatives(
            subject=subject,
            body=text,
            from_email=sender(exchange),
            to=[formataddr((person.name, person.email))],
            reply_to=[exchange.owner.email],
        )
        message.attach_alternative(html, "text/html")
        # Tracking would send the link through the provider's servers, and
        # record when each person opens the email and clicks. A provider
        # that can't turn it off per message refuses to send.
        message.track_clicks = False
        message.track_opens = False
        status, message_id = EmailLog.Status.SENT, ""
        try:
            message.send()
            anymail = getattr(message, "anymail_status", None)
            message_id = (anymail and anymail.message_id) or ""
        except Exception:
            logger.exception("Sending a %s email failed", kind)
            status = EmailLog.Status.FAILED
            failed += 1
        EmailLog.objects.create(
            participant=participant,
            kind=kind,
            status=status,
            provider_message_id=message_id,
        )
    return failed
