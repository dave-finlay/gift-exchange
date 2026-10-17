from datetime import timedelta

import pytest
from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone

from giftexchange.accounts.models import Invitation, User

pytestmark = pytest.mark.django_db


def test_email_is_case_insensitive(client):
    User.objects.create_user("Alice@Example.org", "a long password")
    response = client.post(
        reverse("signin"),
        {"username": "ALICE@example.ORG", "password": "a long password"},
    )
    assert response.status_code == 302
    with pytest.raises(IntegrityError):
        User.objects.create_user("alice@example.org", "another password")


def test_invitation_is_stored_hashed_and_single_use():
    inviter = User.objects.create_user("alice@example.org", "a long password")
    invitation, token = Invitation.issue("bob@example.org", inviter)
    assert token not in invitation.token_hash
    assert Invitation.usable(token) == invitation
    assert Invitation.usable(token + "x") is None

    invitation.accepted_at = timezone.now()
    invitation.save()
    assert Invitation.usable(token) is None


def test_invitation_expires():
    invitation, token = Invitation.issue("bob@example.org", None)
    invitation.expires_at = timezone.now() - timedelta(seconds=1)
    invitation.save()
    assert Invitation.usable(token) is None


def test_revoked_invitation_is_unusable():
    invitation, token = Invitation.issue("bob@example.org", None)
    invitation.revoked_at = timezone.now()
    invitation.save()
    assert Invitation.usable(token) is None
