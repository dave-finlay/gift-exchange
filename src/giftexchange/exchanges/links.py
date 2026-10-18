"""Participants' private links.

A link carries the participant's ID and a version, signed with the site's
secret key (and checked against SECRET_KEY_FALLBACKS while keys rotate).
Nothing about it is stored, so every email to a participant can carry the
same working link. "Get a new link" bumps the version (link_rotated_at),
and older links stop working.
"""

from django.core import signing

from .models import Participant

SALT = "giftexchange.participant-link"


def _version(participant):
    rotated = participant.link_rotated_at
    return int(rotated.timestamp()) if rotated else 0


def token(participant):
    return signing.Signer(salt=SALT).sign(f"{participant.pk}.{_version(participant)}")


def participant_for(token):
    """The participant a token belongs to, or None if it's forged, mangled
    or out of date."""
    try:
        value = signing.Signer(salt=SALT).unsign(token)
        pk, version = (int(part) for part in value.split("."))
    except (signing.BadSignature, ValueError):
        return None
    participant = (
        Participant.objects.select_related("exchange", "person", "exchange__owner")
        .filter(pk=pk)
        .first()
    )
    if participant is None or _version(participant) != version:
        return None
    return participant
