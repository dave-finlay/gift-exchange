import re

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from giftexchange.accounts.models import User
from giftexchange.exchanges import links, secrecy
from giftexchange.exchanges.models import (
    EmailLog,
    Exchange,
    GiftIdea,
    Participant,
    Rule,
)

pytestmark = pytest.mark.django_db

FAMILY = [["Alice", "Bob"], ["Carol", "Dan"], ["Erin"], ["Frank"]]


@pytest.fixture
def b(build, client):
    b = build(FAMILY)
    b.circle.owner.display_name = "Olive"
    b.circle.owner.save()
    client.force_login(b.circle.owner)
    return b


def link_in(message):
    return re.search(r"http://testserver(/p/\S+/)", message.body).group(1)


def open_page(message):
    """A participant's browser opening the link in their email."""
    browser = Client()
    response = browser.get(link_in(message))
    assert response.status_code == 302
    return browser, response.url


def email_to(name):
    return next(m for m in mail.outbox if f"{name.lower()}@example.org" in m.to[0])


def send_join(client, b):
    return client.post(reverse("exchange_send_join", args=[b.exchange.pk]))


def draw(client, b, **data):
    return client.post(reverse("exchange_draw", args=[b.exchange.pk]), data)


# Links


def test_links_are_signed_and_can_be_replaced(b):
    alice = b.participant("Alice")
    token = links.token(alice)
    assert links.participant_for(token) == alice
    assert links.participant_for(token[:-2] + "xx") is None
    assert links.participant_for("nonsense") is None
    alice.link_rotated_at = timezone.now()
    alice.save()
    assert links.participant_for(token) is None
    assert links.participant_for(links.token(alice)) == alice


# "Are you in?" emails


def test_send_join_emails(client, b):
    page = client.get(reverse("exchange_send_join", args=[b.exchange.pk]))
    assert page.status_code == 200 and not mail.outbox  # Confirmation first
    send_join(client, b)
    b.exchange.refresh_from_db()
    assert b.exchange.status == Exchange.Status.JOINING
    assert len(mail.outbox) == 6
    message = email_to("Alice")
    assert message.subject == "Are you in? Christmas"
    assert message.from_email.startswith("Olive via Gift Exchange <")
    assert message.reply_to == ["organizer@example.org"]
    assert "Hi Alice," in message.body
    assert message.alternatives[0][1] == "text/html"
    assert EmailLog.objects.filter(kind="join", status="sent").count() == 6


def test_people_sharing_an_address_are_named_in_the_subject(client, b):
    b.person["Frank"].email = "erin@example.org"
    b.person["Frank"].save()
    send_join(client, b)
    subjects = {
        m.subject for m in mail.outbox if m.to[0].endswith("<erin@example.org>")
    }
    assert subjects == {
        "For Erin: Are you in? Christmas",
        "For Frank: Are you in? Christmas",
    }


def test_cannot_send_before_its_ready(client, b):
    Exchange.objects.filter(pk=b.exchange.pk).update(gift_date=None)
    response = send_join(client, b)
    assert b"Set the gift date" in response.content
    assert not mail.outbox
    b.exchange.refresh_from_db()
    assert b.exchange.status == Exchange.Status.DRAFT


def test_daily_email_limit(client, b, settings):
    settings.MAX_EMAILS_PER_EXCHANGE_PER_DAY = 5
    send_join(client, b)
    assert not mail.outbox
    b.exchange.refresh_from_db()
    assert b.exchange.status == Exchange.Status.DRAFT


def test_a_failed_email_is_logged_and_flagged(client, b, monkeypatch):
    from django.core.mail import EmailMultiAlternatives

    original = EmailMultiAlternatives.send

    def flaky(self, *args, **kwargs):
        if "carol@example.org" in self.to[0]:
            raise ConnectionError("provider down")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(EmailMultiAlternatives, "send", flaky)
    send_join(client, b)
    assert len(mail.outbox) == 5
    assert EmailLog.objects.get(participant__person__name="Carol").status == "failed"
    page = client.get(reverse("exchange", args=[b.exchange.pk]))
    assert b"Email failed" in page.content


# The participant's page


def test_the_link_signs_the_browser_in_and_moves_to_a_clean_url(client, b):
    send_join(client, b)
    message = email_to("Alice")
    browser, url = open_page(message)
    assert url == reverse("participant", args=[b.participant("Alice").pk])
    assert "/p/" not in url
    assert b"Are you in?" in browser.get(url).content
    # Another browser, without the link, gets nothing.
    assert Client().get(url).status_code == 404
    # Nor can Alice's browser see Bob's page.
    bob = reverse("participant", args=[b.participant("Bob").pk])
    assert browser.get(bob).status_code == 404


def test_bad_or_draft_links_dont_work(b):
    assert (
        Client().get(reverse("participant_link", args=["nonsense"])).status_code == 404
    )
    token = links.token(b.participant("Alice"))  # Still a draft
    assert Client().get(reverse("participant_link", args=[token])).status_code == 404


def test_answer_and_change_your_mind(client, b):
    send_join(client, b)
    browser, url = open_page(email_to("Alice"))
    alice = b.participant("Alice")
    respond = reverse("participant_respond", args=[alice.pk])
    browser.post(respond, {"response": "declined"})
    alice.refresh_from_db()
    assert alice.response == "declined"
    browser.post(respond, {"response": "in"})
    alice.refresh_from_db()
    assert alice.response == "in"
    assert b"Christmas" in client.get(reverse("exchange", args=[b.exchange.pk])).content


def test_gift_ideas(client, b):
    send_join(client, b)
    browser, url = open_page(email_to("Alice"))
    alice = b.participant("Alice")
    add = reverse("participant_add_idea", args=[alice.pk])
    browser.post(add, {"text": "A <b>scarf</b> https://example.org/scarf"})
    content = browser.get(url).content.decode()
    assert "&lt;b&gt;scarf&lt;/b&gt;" in content
    assert '<a href="https://example.org/scarf" rel="noopener nofollow">' in content
    for i in range(12):
        browser.post(add, {"text": f"Idea {i}"})
    assert alice.gift_ideas.count() == GiftIdea.MAX_PER_PARTICIPANT
    idea = alice.gift_ideas.first()
    browser.post(reverse("participant_delete_idea", args=[alice.pk, idea.pk]))
    assert alice.gift_ideas.count() == GiftIdea.MAX_PER_PARTICIPANT - 1


# Draw and send


def test_draw_and_send(client, b):
    send_join(client, b)
    for name in ["Alice", "Bob", "Carol", "Dan", "Erin"]:
        Participant.objects.filter(pk=b.participant(name).pk).update(response="in")
    mail.outbox.clear()
    page = client.get(reverse("exchange_draw", args=[b.exchange.pk]))
    assert b"1 person hasn" in page.content
    draw(client, b, leave_out_pending="on")
    b.exchange.refresh_from_db()
    assert b.exchange.status == Exchange.Status.SENT
    assert b.participant("Frank").response == "declined"
    assert len(mail.outbox) == 5
    assert all(m.subject == "Christmas: the names are drawn" for m in mail.outbox)
    # No email gives away an assignment.
    for message in mail.outbox:
        giver = Participant.objects.get(
            pk=int(link_in(message).split("/")[2].split(".")[0])
        )
        receiver = Participant.objects.get(pk=secrecy.receiver_id(giver.assignment))
        assert receiver.person.name not in message.body
        assert receiver.person.name not in message.alternatives[0][0]


def test_draw_without_asking_first(client, b):
    Exchange.objects.filter(pk=b.exchange.pk).update(confirm_first=False)
    draw(client, b)
    b.exchange.refresh_from_db()
    assert b.exchange.status == Exchange.Status.SENT
    assert len(mail.outbox) == 6


def test_an_impossible_draw_sends_nothing(client, b):
    Exchange.objects.filter(pk=b.exchange.pk).update(confirm_first=False)
    for name in ["Alice", "Bob", "Carol", "Dan", "Frank"]:
        b.rule("Erin", name, Rule.Kind.EXCLUDE)
    confirm = client.get(reverse("exchange_draw", args=[b.exchange.pk]))
    assert b"No draw is possible" in confirm.content
    assert b"<button>Draw and send</button>" not in confirm.content
    response = draw(client, b)
    assert response.url.endswith("?check#check")
    assert not mail.outbox
    b.exchange.refresh_from_db()
    assert b.exchange.status == Exchange.Status.DRAFT


def drawn(client, b):
    Exchange.objects.filter(pk=b.exchange.pk).update(confirm_first=False)
    draw(client, b)


def test_tap_to_reveal(client, b):
    drawn(client, b)
    browser, url = open_page(email_to("Alice"))
    alice = b.participant("Alice")
    receiver = Participant.objects.get(pk=secrecy.receiver_id(alice.assignment))
    receiver.gift_ideas.create(text="Socks")
    page = browser.get(url).content.decode()
    assert "Tap to reveal" in page
    assert receiver.person.name not in page.split("Who's Taking Part")[0]
    revealed = browser.post(reverse("participant_reveal", args=[alice.pk]))
    content = revealed.content.decode()
    assert f'<p class="receiver">{receiver.person.name}</p>' in content
    assert "Socks" in content
    alice.refresh_from_db()
    assert alice.revealed_at is not None
    assert (
        b"Has looked" in client.get(reverse("exchange", args=[b.exchange.pk])).content
    )


def test_gift_status_and_asking_to_be_taken_out(client, b):
    drawn(client, b)
    browser, url = open_page(email_to("Alice"))
    alice = b.participant("Alice")
    browser.post(
        reverse("participant_gift_status", args=[alice.pk]), {"gift_status": "bought"}
    )
    browser.post(reverse("participant_withdraw", args=[alice.pk]))
    alice.refresh_from_db()
    assert alice.gift_status == "bought"
    assert alice.withdraw_requested_at is not None
    page = client.get(reverse("exchange", args=[b.exchange.pk])).content.decode()
    assert "Gift: bought" in page
    assert "Asked to be taken out" in page


def test_ended_exchange(client, b):
    drawn(client, b)
    browser, url = open_page(email_to("Alice"))
    Exchange.objects.filter(pk=b.exchange.pk).update(status=Exchange.Status.ARCHIVED)
    assert b"has ended" in browser.get(url).content


def test_cannot_send_or_draw_another_organizers_exchange(client, b):
    eve = User.objects.create_user("eve@example.org", "unused password")
    client.force_login(eve)
    assert send_join(client, b).status_code == 404
    assert draw(client, b).status_code == 404
    assert not mail.outbox
