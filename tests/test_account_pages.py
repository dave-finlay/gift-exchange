import pytest
from django.urls import reverse

from giftexchange.accounts.models import Invitation, User
from giftexchange.audit.models import AuditEvent

pytestmark = pytest.mark.django_db

PASSWORD = "correct horse battery"


@pytest.fixture
def staff(client):
    user = User.objects.create_user("op@example.org", PASSWORD, is_staff=True)
    client.force_login(user)
    return user


def sign_in(client, email, password):
    return client.post(reverse("signin"), {"username": email, "password": password})


def test_sign_in_is_recorded(client):
    User.objects.create_user("alice@example.org", PASSWORD)
    response = sign_in(client, "alice@example.org", PASSWORD)
    assert response.status_code == 302
    assert AuditEvent.objects.filter(action="signin").count() == 1


def test_lockout_after_five_failures(client):
    User.objects.create_user("alice@example.org", PASSWORD)
    for _ in range(5):
        sign_in(client, "alice@example.org", "wrong")
    response = sign_in(client, "alice@example.org", PASSWORD)
    assert response.status_code == 429
    assert b"Too Many Attempts" in response.content


def test_pages_need_sign_in(client):
    response = client.get(reverse("home"))
    assert response.status_code == 302
    assert response.url.startswith(reverse("signin"))


def test_security_headers(client):
    response = client.get(reverse("signin"))
    assert "script-src" not in response["Content-Security-Policy"]
    assert "default-src 'none'" in response["Content-Security-Policy"]
    assert response["X-Robots-Tag"] == "noindex, nofollow"
    assert response["Referrer-Policy"] == "same-origin"
    assert response["X-Frame-Options"] == "DENY"


def test_only_staff_can_invite(client):
    user = User.objects.create_user("alice@example.org", PASSWORD)
    client.force_login(user)
    assert client.get(reverse("invitations")).status_code == 403
    assert (
        client.post(reverse("invitations"), {"email": "x@example.org"}).status_code
        == 403
    )
    assert not Invitation.objects.exists()


def issue(client, email="bob@example.org"):
    response = client.post(reverse("invitations"), {"email": email})
    assert response.status_code == 200
    link = response.context["link"]
    return link.split("/invite/")[1].rstrip("/")


def test_invitation_link_is_shown_once_and_stored_hashed(client, staff):
    token = issue(client)
    invitation = Invitation.objects.get()
    assert invitation.invited_by == staff
    assert token not in invitation.token_hash
    assert token.encode() not in client.get(reverse("invitations")).content


def test_new_invitation_cancels_the_old_one(client, staff):
    old = issue(client)
    new = issue(client)
    assert Invitation.usable(old) is None
    assert Invitation.usable(new)


def test_cannot_invite_an_existing_user(client, staff):
    response = client.post(reverse("invitations"), {"email": "OP@example.org"})
    assert response.context["form"].errors
    assert not Invitation.objects.exists()


def test_revoke(client, staff):
    token = issue(client)
    invitation = Invitation.objects.get()
    client.post(reverse("revoke_invitation", args=[invitation.pk]))
    assert Invitation.usable(token) is None


def accept(client, token, password=PASSWORD, again=None):
    return client.post(
        reverse("accept", args=[token]),
        {"display_name": "Bob", "password1": password, "password2": again or password},
    )


def test_accepting_creates_the_account_and_signs_in(client, staff):
    token = issue(client)
    client.logout()
    # Opening the link changes nothing.
    assert client.get(reverse("accept", args=[token])).status_code == 200
    assert Invitation.usable(token)

    response = accept(client, token)
    assert response.status_code == 302
    bob = User.objects.get(email="bob@example.org")
    assert bob.display_name == "Bob"
    assert not bob.is_staff
    assert client.get(reverse("home")).context["user"] == bob
    assert Invitation.objects.get().accepted_at


def test_invitation_works_only_once(client, staff):
    token = issue(client)
    client.logout()
    accept(client, token)
    client.logout()
    assert accept(client, token).status_code == 410
    assert client.get(reverse("accept", args=[token])).status_code == 410
    assert User.objects.filter(email="bob@example.org").count() == 1


def test_unknown_token(client):
    assert client.get(reverse("accept", args=["nonsense"])).status_code == 410


@pytest.mark.parametrize(
    "password, again",
    [("short", None), (PASSWORD, PASSWORD + "x"), ("12345678901", None)],
)
def test_bad_passwords_are_rejected(client, staff, password, again):
    token = issue(client)
    client.logout()
    response = accept(client, token, password, again)
    assert response.status_code == 200
    assert response.context["form"].errors
    assert not User.objects.filter(email="bob@example.org").exists()


@pytest.mark.parametrize(
    "host, status",
    [
        ("example.org", 200),
        ("evil.example", 400),
        # Development extras (the tests run with development settings)
        ("localhost", 200),
        ("Some-Laptop.local", 200),
    ],
)
def test_only_known_addresses_are_served(client, host, status):
    assert client.get(reverse("signin"), HTTP_HOST=host).status_code == status
