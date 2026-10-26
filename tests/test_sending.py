import base64
import json
import re

import pytest
import requests
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from giftexchange.accounts.models import User
from giftexchange.exchanges import links, secrecy
from giftexchange.exchanges.models import (
    Assignment,
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
    b = build(FAMILY, confirm_first=True)  # Tests start with "Are you in?"
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
    assert all(not m.track_clicks and not m.track_opens for m in mail.outbox)


def test_mailjet_is_told_not_to_track(client, b, settings, monkeypatch):
    """What actually goes to Mailjet's API, with the network faked."""
    settings.EMAIL_BACKEND = "anymail.backends.mailjet.EmailBackend"
    settings.ANYMAIL = {"MAILJET_API_KEY": "key", "MAILJET_SECRET_KEY": "secret"}
    sent = []

    def fake_request(session, method, url, **kwargs):
        sent.append(json.loads(kwargs["data"]))
        response = requests.Response()
        response.status_code = 200
        to = sent[-1]["Messages"][0]["To"][0]["Email"]
        response._content = json.dumps(
            {"Messages": [{"Status": "success", "To": [{"Email": to, "MessageID": 1}]}]}
        ).encode()
        return response

    monkeypatch.setattr(requests.Session, "request", fake_request)
    send_join(client, b)
    assert len(sent) == 6
    for payload in sent:
        assert payload["Globals"]["TrackClicks"] == "disabled"
        assert payload["Globals"]["TrackOpens"] == "disabled"
    assert EmailLog.objects.filter(kind="join", status="sent").count() == 6


def test_a_provider_that_cant_turn_off_tracking_sends_nothing(
    client, b, settings, caplog
):
    # Brevo can only turn tracking off for a whole account, on request.
    settings.EMAIL_BACKEND = "anymail.backends.brevo.EmailBackend"
    settings.ANYMAIL = {"BREVO_API_KEY": "key"}
    send_join(client, b)
    assert EmailLog.objects.filter(kind="join", status="failed").count() == 6
    assert "does not support track_clicks" in caplog.text


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


def test_gift_status(client, b):
    drawn(client, b)
    browser, url = open_page(email_to("Alice"))
    # Taking yourself out isn't offered (not decided yet).
    assert "take part anymore" not in browser.get(url).content.decode()
    alice = b.participant("Alice")
    browser.post(
        reverse("participant_gift_status", args=[alice.pk]), {"gift_status": "bought"}
    )
    alice.refresh_from_db()
    assert alice.gift_status == "bought"
    page = client.get(reverse("exchange", args=[b.exchange.pk])).content.decode()
    assert "✓ Gift bought" in page


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


# Calling off a gift exchange under way


def called_off_emails():
    return [m for m in mail.outbox if m.subject == "Christmas has been called off"]


def test_call_off_and_tell_everyone(client, b):
    send_join(client, b)
    Participant.objects.filter(pk=b.participant("Frank").pk).update(response="declined")
    page = client.get(reverse("exchange_delete", args=[b.exchange.pk]))
    assert b'name="notify" checked' in page.content
    client.post(reverse("exchange_delete", args=[b.exchange.pk]), {"notify": "on"})
    assert not Exchange.objects.exists()
    told = called_off_emails()
    assert len(told) == 5  # Not Frank, who'd said no
    assert "Olive has called off Christmas" in told[0].body
    assert "/p/" not in told[0].body  # The page is gone, so no link
    assert "buy a gift" not in told[0].body  # Nobody had been drawn yet


def test_call_off_after_the_draw(client, b):
    drawn(client, b)
    client.post(reverse("exchange_delete", args=[b.exchange.pk]), {"notify": "on"})
    told = called_off_emails()
    assert len(told) == 6
    assert "you don't need to buy a gift" in told[0].body


def test_call_off_quietly(client, b):
    drawn(client, b)
    mail.outbox.clear()
    client.post(reverse("exchange_delete", args=[b.exchange.pk]))
    assert not Exchange.objects.exists()
    assert not mail.outbox


def test_deleting_a_circle_calls_off_its_gift_exchanges(client, b):
    send_join(client, b)
    page = client.get(reverse("circle_delete", args=[b.circle.pk]))
    assert b"Christmas is under way" in page.content
    client.post(reverse("circle_delete", args=[b.circle.pk]), {"notify": "on"})
    assert not Exchange.objects.exists()
    assert len(called_off_emails()) == 6


def test_calling_off_respects_the_daily_email_limit(client, b, settings):
    send_join(client, b)
    settings.MAX_EMAILS_PER_EXCHANGE_PER_DAY = 8
    client.post(reverse("exchange_delete", args=[b.exchange.pk]), {"notify": "on"})
    assert Exchange.objects.exists()  # Not deleted: the emails couldn't go
    client.post(reverse("exchange_delete", args=[b.exchange.pk]))
    assert not Exchange.objects.exists()  # Quietly is still possible


# Messages, sign-off and preview


def test_emails_are_signed_by_the_organizer_with_their_message(client, b):
    Exchange.objects.filter(pk=b.exchange.pk).update(
        join_note="Are you *in*?", note="Have fun _shopping_!"
    )
    send_join(client, b)
    join = email_to("Alice")
    assert "Are you *in*?" in join.body  # Plain text: as typed
    assert "<strong>in</strong>" in join.alternatives[0][0]
    assert "Have fun" not in join.body  # That's the draw's message
    assert join.body.rstrip().endswith(
        "Olive\n\n--\nSent with Gift Exchange. This link is just for you, "
        "so please don't forward it."
    )
    assert "on behalf" not in join.body
    mail.outbox.clear()
    draw(client, b)
    drawn_email = email_to("Alice")
    assert "Have fun _shopping_!" in drawn_email.body
    assert "<em>shopping</em>" in drawn_email.alternatives[0][0]
    assert "Are you *in*?" not in drawn_email.body


def test_participant_page_shows_the_latest_message(client, b):
    Exchange.objects.filter(pk=b.exchange.pk).update(
        join_note="Join *now*", note="Drawn!"
    )
    send_join(client, b)
    browser, url = open_page(email_to("Alice"))
    assert b"Join <strong>now</strong>" in browser.get(url).content
    draw(client, b)
    page = browser.get(url).content
    assert b"Drawn!" in page and b"Join" not in page


def test_new_gift_exchanges_draw_without_asking_first(client, b):
    client.post(reverse("exchange_new"))
    assert Exchange.objects.latest("pk").confirm_first is False


@pytest.mark.parametrize("kind", ["join", "assignment"])
def test_preview_emails(client, b, kind):
    Exchange.objects.filter(pk=b.exchange.pk).update(note="Wrap it *nicely*")
    response = client.get(reverse("exchange_preview", args=[b.exchange.pk, kind]))
    content = response.content.decode()
    assert "Hi Alice," in content  # The first person taking part
    assert "Subject: <strong" in content
    assert "style-src 'unsafe-inline'" in response["Content-Security-Policy"]
    assert "script-src" not in response["Content-Security-Policy"]
    if kind == "assignment":
        assert "<strong>nicely</strong>" in content
    assert not mail.outbox and not EmailLog.objects.exists()


def test_preview_is_only_for_the_organizer(client, b):
    url = reverse("exchange_preview", args=[b.exchange.pk, "assignment"])
    assert (
        client.get(reverse("exchange_preview", args=[b.exchange.pk, "x"])).status_code
        == 404
    )
    eve = User.objects.create_user("eve@example.org", "unused password")
    client.force_login(eve)
    assert client.get(url).status_code == 404


def test_summary_pages_list_recipients_and_show_the_email(client, b):
    page = client.get(reverse("exchange_send_join", args=[b.exchange.pk]))
    content = page.content.decode()
    assert "alice@example.org" in content and "frank@example.org" in content
    preview = reverse("exchange_preview", args=[b.exchange.pk, "join"]) + "?embed"
    assert f'<iframe class="email-preview" src="{preview}"' in content
    assert "Subject: <strong>Are you in? Christmas</strong>" in content
    assert "frame-src 'self'" in page["Content-Security-Policy"]
    embedded = client.get(preview)
    assert embedded["X-Frame-Options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in embedded["Content-Security-Policy"]
    assert (
        b"Hi Alice," in embedded.content and b"Preview \xc2\xb7" not in embedded.content
    )
    assert not mail.outbox


# Sending again, and new links


def resend(client, b, name, **data):
    participant = b.participant(name)
    data.setdefault("email", participant.person.email)
    url = reverse("exchange_resend", args=[b.exchange.pk, participant.pk])
    return client.post(url, data)


def test_send_again_with_the_same_link(client, b):
    send_join(client, b)
    first = link_in(email_to("Alice"))
    mail.outbox.clear()
    page = client.get(reverse("exchange", args=[b.exchange.pk])).content.decode()
    assert page.count("Send again") == 6
    resend(client, b, "Alice")
    assert len(mail.outbox) == 1
    assert link_in(mail.outbox[0]) == first
    assert "new link" not in mail.outbox[0].body
    assert b.participant("Alice").emails.count() == 2


def test_a_new_link_shuts_out_the_old_one(client, b):
    drawn(client, b)
    old_browser, url = open_page(email_to("Alice"))
    old_link = link_in(email_to("Alice"))
    mail.outbox.clear()
    resend(client, b, "Alice", new_link="on")
    message = mail.outbox[0]
    assert message.subject == "Christmas: the names are drawn"
    assert "This email has a new link to your page." in message.body
    assert link_in(message) != old_link
    # The old link, and the browser that opened it, no longer work.
    assert Client().get(old_link).status_code == 404
    assert old_browser.get(url).status_code == 404
    browser, url = open_page(message)
    assert b"Tap to reveal" in browser.get(url).content


def test_changing_the_address_sends_a_new_link_there(client, b):
    send_join(client, b)
    old_link = link_in(email_to("Carol"))
    mail.outbox.clear()
    resend(client, b, "Carol", email="carol.new@example.org")
    assert mail.outbox[0].to == ["Carol <carol.new@example.org>"]
    assert link_in(mail.outbox[0]) != old_link
    b.person["Carol"].refresh_from_db()
    assert b.person["Carol"].email == "carol.new@example.org"
    assert resend(client, b, "Carol", email="not an address").status_code == 200


def test_nothing_to_send_again(client, b):
    page = client.get(
        reverse("exchange_resend", args=[b.exchange.pk, b.participant("Alice").pk])
    )
    assert page.status_code == 302  # Still a draft
    send_join(client, b)
    for name in ["Alice", "Bob", "Carol", "Dan", "Erin"]:
        Participant.objects.filter(pk=b.participant(name).pk).update(response="in")
    Participant.objects.filter(pk=b.participant("Frank").pk).update(response="declined")
    draw(client, b)
    mail.outbox.clear()
    resend(client, b, "Frank")  # Not in the draw
    assert not mail.outbox
    page = client.get(reverse("exchange", args=[b.exchange.pk])).content.decode()
    assert page.count("Send again") == 5


def test_send_again_is_only_for_the_organizer(client, b):
    send_join(client, b)
    mail.outbox.clear()
    client.force_login(User.objects.create_user("eve@example.org", "unused"))
    assert resend(client, b, "Alice").status_code == 404
    assert not mail.outbox


def test_send_again_respects_the_daily_email_limit(client, b, settings):
    send_join(client, b)
    mail.outbox.clear()
    settings.MAX_EMAILS_PER_EXCHANGE_PER_DAY = 6
    resend(client, b, "Alice", new_link="on")
    assert not mail.outbox
    assert b.participant("Alice").link_rotated_at is None


def test_participant_gets_a_new_link(client, b):
    send_join(client, b)
    old_link = link_in(email_to("Alice"))
    browser, url = open_page(email_to("Alice"))
    assert b"Email me a new link" in browser.get(url).content
    mail.outbox.clear()
    alice = b.participant("Alice")
    browser.post(reverse("participant_new_link", args=[alice.pk]))
    assert len(mail.outbox) == 1
    assert "This email has a new link" in mail.outbox[0].body
    assert Client().get(old_link).status_code == 404
    assert browser.get(url).status_code == 200  # This browser stays in
    # A few a day at most, so it can't flood their inbox.
    for _ in range(10):
        browser.post(reverse("participant_new_link", args=[alice.pk]))
    assert alice.emails.count() == 5


# Two submissions at once (a double tap, two tabs)


def meanwhile(monkeypatch, b, **changes):
    """Make a second submission pass the page's own checks just as the
    first finishes: the gift exchange changes in between."""
    from giftexchange.exchanges import views

    readiness = views.readiness

    def and_then(exchange):
        problems = readiness(exchange)
        Exchange.objects.filter(pk=b.exchange.pk).update(**changes)
        return problems

    monkeypatch.setattr(views, "readiness", and_then)


def test_are_you_in_goes_out_once(client, b, monkeypatch):
    meanwhile(monkeypatch, b, status=Exchange.Status.JOINING)
    response = send_join(client, b)
    assert not mail.outbox
    assert response.url == reverse("exchange", args=[b.exchange.pk])
    page = client.get(response.url).content.decode()
    assert "has already gone out" in page


def test_names_are_drawn_once(client, b, monkeypatch):
    send_join(client, b)
    mail.outbox.clear()
    meanwhile(monkeypatch, b, status=Exchange.Status.SENT)
    response = draw(client, b, leave_out_pending="on")
    assert not mail.outbox
    assert not Assignment.objects.exists()
    # The second submission's choices change nothing either.
    assert b.participant("Alice").response == "pending"
    page = client.get(response.url).content.decode()
    assert "has already been drawn" in page


def test_an_impossible_draw_undoes_leaving_people_out(client, b):
    send_join(client, b)
    for name in ["Alice", "Bob", "Carol", "Dan", "Erin"]:
        Participant.objects.filter(pk=b.participant(name).pk).update(response="in")
    for name in ["Alice", "Bob", "Carol", "Dan", "Frank"]:
        b.rule("Erin", name, Rule.Kind.EXCLUDE)
    response = draw(client, b, leave_out_pending="on")
    assert response.url.endswith("?check#check")
    b.exchange.refresh_from_db()
    assert b.exchange.status == Exchange.Status.JOINING
    assert b.participant("Frank").response == "pending"


def test_leaving_out_everyone_draws_nothing(client, b):
    send_join(client, b)  # Nobody has answered yet
    mail.outbox.clear()
    draw(client, b, leave_out_pending="on")
    assert not mail.outbox
    b.exchange.refresh_from_db()
    assert b.exchange.status == Exchange.Status.JOINING
    assert b.participant("Alice").response == "pending"
    page = client.get(reverse("exchange", args=[b.exchange.pk])).content.decode()
    assert "At least 2 people need to be taking part." in page


# The email provider's reports


def mailjet_report(client, message_id, event, password="webhook-secret", **fields):
    """Mailjet's webhook telling the site what happened to one email."""
    report = {"event": event, "time": 1793036676, "MessageID": message_id}
    report.update(fields)
    credentials = base64.b64encode(f"provider:{password}".encode()).decode()
    return client.post(
        "/anymail/mailjet/tracking/",
        json.dumps([report]),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Basic {credentials}",
    )


@pytest.fixture
def sent(client, b):
    """Everyone's "Are you in?" email, as if sent through Mailjet."""
    send_join(client, b)
    for number, log in enumerate(EmailLog.objects.order_by("pk"), start=1):
        log.provider_message_id = str(number)
        log.save()
    return {log.participant.person.name: log for log in EmailLog.objects.all()}


def status(log):
    log.refresh_from_db()
    return log.status


def test_a_bounce_shows_on_the_progress_page(client, b, sent):
    page = client.get(reverse("exchange", args=[b.exchange.pk]))
    assert b"Email bounced" not in page.content
    response = mailjet_report(
        client, 1, "bounce", hard_bounce=True, error="user unknown"
    )
    assert response.status_code == 200
    alice = sent["Alice"]
    assert status(alice) == "bounced"
    assert status(sent["Bob"]) == "sent"
    page = client.get(reverse("exchange", args=[b.exchange.pk]))
    assert b"Email bounced" in page.content
    resend = client.get(
        reverse("exchange_resend", args=[b.exchange.pk, alice.participant.pk])
    )
    assert b"check the address" in resend.content


def test_blocked_and_spam_reports(client, b, sent):
    mailjet_report(client, 1, "blocked", error="preblocked")
    mailjet_report(client, 2, "sent")
    mailjet_report(client, 2, "spam")
    assert status(sent["Alice"]) == "blocked"
    assert status(sent["Bob"]) == "complaint"
    page = client.get(reverse("exchange", args=[b.exchange.pk]))
    assert b"Email blocked" in page.content and b"Marked as spam" in page.content


def test_reports_in_any_order(client, b, sent):
    mailjet_report(client, 1, "bounce", hard_bounce=True)
    mailjet_report(client, 1, "sent")  # Late, or out of order
    assert status(sent["Alice"]) == "bounced"
    mailjet_report(client, 2, "sent")
    assert status(sent["Bob"]) == "delivered"
    # Greylisting, which Mailjet retries, changes nothing. (Other soft
    # bounces come once it has given up.)
    mailjet_report(client, 3, "bounce", hard_bounce=False, error="greylisted")
    assert status(sent["Carol"]) == "sent"


def test_reports_need_the_secret(client, b, sent):
    response = mailjet_report(client, 1, "bounce", hard_bounce=True, password="guess")
    assert response.status_code != 200
    assert status(sent["Alice"]) == "sent"
