"""Participants' private links.

A link carries the participant's ID and a version, signed with the site's
secret key (and checked against SECRET_KEY_FALLBACKS while keys rotate).
Nothing about it is stored, so every email to a participant can carry the
same working link. "Get a new link" bumps the version (link_rotated_at),
and older links stop working, as do browsers that opened them (see
participant.py).
"""

from django.core import signing
from django.utils import timezone

from .models import Participant

SALT = "giftexchange.participant-link"


def version(participant):
    rotated = participant.link_rotated_at
    return int(rotated.timestamp() * 1_000_000) if rotated else 0


def token(participant):
    return signing.Signer(salt=SALT).sign(f"{participant.pk}.{version(participant)}")


def rotate(participant):
    """Give the participant a new link. Their old ones stop working."""
    participant.link_rotated_at = timezone.now()
    participant.save(update_fields=["link_rotated_at"])


def participant_for(token):
    """The participant a token belongs to, or None if it's forged, mangled
    or out of date."""
    try:
        value = signing.Signer(salt=SALT).unsign(token)
        pk, link_version = (int(part) for part in value.split("."))
    except (signing.BadSignature, ValueError):
        return None
    participant = (
        Participant.objects.select_related("exchange", "person", "exchange__owner")
        .filter(pk=pk)
        .first()
    )
    if participant is None or version(participant) != link_version:
        return None
    return participant
