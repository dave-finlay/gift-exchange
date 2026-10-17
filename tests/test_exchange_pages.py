import datetime

import pytest
from django.urls import reverse

from giftexchange.accounts.models import User
from giftexchange.exchanges.models import Circle, Exchange, Rule

pytestmark = pytest.mark.django_db

FAMILY = [["Alice", "Bob"], ["Carol", "Dan"], ["Erin"], ["Frank"]]


@pytest.fixture
def b(build, client):
    b = build(FAMILY)
    client.force_login(b.circle.owner)
    return b


def basics(**changes):
    data = {
        "name": "Christmas",
        "gift_date": "2026-12-25",
        "draw_date": "",
        "budget": "",
        "note": "",
        "confirm_first": "on",
        "avoid_repeats": "1",
    }
    data.update(changes)
    return {k: v for k, v in data.items() if v is not None}


def create(client):
    """Tap "Create a gift exchange"; returns the new draft."""
    response = client.post(reverse("exchange_new"))
    exchange = Exchange.objects.latest("pk")
    assert response.url == reverse("exchange", args=[exchange.pk])
    return exchange


def test_create_a_gift_exchange_straight_away(client, b):
    first, second = create(client), create(client)
    assert (first.name, second.name) == ("My Gift Exchange", "My Gift Exchange 2")
    assert first.owner == b.circle.owner
    assert first.circle is None and first.gift_date is None
    assert not first.participants.exists()
    page = client.get(reverse("exchange", args=[first.pk]))
    assert b"Who's It For?" in page.content
    assert b"Who's Taking Part" not in page.content
    assert b"Check the Rules" not in page.content


def test_gift_exchanges_per_user_limit(client, b, settings):
    settings.MAX_EXCHANGES_PER_USER = 2  # b.exchange is one
    create(client)
    response = client.post(reverse("exchange_new"))
    assert response.url == reverse("home")
    assert Exchange.objects.count() == 2


def test_choose_a_circle(client, b):
    exchange = create(client)
    client.post(reverse("exchange_circle", args=[exchange.pk]), {"circle": b.circle.pk})
    exchange.refresh_from_db()
    assert exchange.circle == b.circle
    names = {p.person.name for p in exchange.participants.all()}
    assert names == set(b.person)


def test_changing_the_circle_resets_people_and_rules(client, b):
    other = Circle.objects.create(owner=b.circle.owner, name="Friends")
    other.people.create(name="Zoe", email="zoe@example.org")
    b.rule("Erin", "Frank", Rule.Kind.EXCLUDE, exchange=b.exchange)
    client.post(reverse("exchange_circle", args=[b.exchange.pk]), {"circle": other.pk})
    assert [p.person.name for p in b.exchange.participants.all()] == ["Zoe"]
    assert not b.exchange.rules.exists()


def test_cannot_choose_another_organizers_circle(client, b):
    eve = User.objects.create_user("eve@example.org", "unused password")
    theirs = Circle.objects.create(owner=eve, name="Eve's")
    exchange = create(client)
    url = reverse("exchange_circle", args=[exchange.pk])
    response = client.post(url, {"circle": theirs.pk})
    assert response.context["choose"].errors
    exchange.refresh_from_db()
    assert exchange.circle is None


def test_create_a_circle_for_a_gift_exchange(client, b):
    exchange = create(client)
    response = client.post(reverse("exchange_circle_new", args=[exchange.pk]))
    exchange.refresh_from_db()
    assert exchange.circle.name == "My Circle"
    assert response.url == reverse("circle", args=[exchange.circle.pk])
    # People added to the circle join its draft gift exchanges.
    client.post(
        reverse("person_new", args=[exchange.circle.pk]),
        {"name": "Ann", "email": "ann@example.org"},
    )
    client.post(
        reverse("paste", args=[exchange.circle.pk]),
        {"text": "Ben, ben@example.org\nCat, cat@example.org"},
    )
    names = {p.person.name for p in exchange.participants.all()}
    assert names == {"Ann", "Ben", "Cat"}


def test_new_people_dont_join_gift_exchanges_that_have_started(client, b):
    Exchange.objects.filter(pk=b.exchange.pk).update(status=Exchange.Status.SENT)
    client.post(
        reverse("person_new", args=[b.circle.pk]),
        {"name": "Zoe", "email": "zoe@example.org"},
    )
    assert not b.exchange.participants.filter(person__name="Zoe").exists()


def test_people_and_rules_need_a_circle_first(client, b):
    exchange = create(client)
    for name in ["exchange_people", "exchange_rule_new"]:
        response = client.post(reverse(name, args=[exchange.pk]), {})
        assert response.url == reverse("exchange", args=[exchange.pk])


def test_gift_exchanges_per_circle_per_year_limit(client, b, settings):
    settings.MAX_EXCHANGES_PER_CIRCLE_PER_YEAR = 1
    exchange = create(client)
    client.post(reverse("exchange_circle", args=[exchange.pk]), {"circle": b.circle.pk})
    url = reverse("exchange_basics", args=[exchange.pk])
    response = client.post(url, basics(name="Again"))  # Also 2026
    assert response.context["basics"].errors
    client.post(url, basics(name="Next year", gift_date="2027-12-25"))
    exchange.refresh_from_db()
    assert exchange.name == "Next year"


def test_home_leads_with_gift_exchanges(client, b):
    create(client)
    Exchange.objects.filter(pk=b.exchange.pk).update(status=Exchange.Status.ARCHIVED)
    eve = User.objects.create_user("eve@example.org", "unused password")
    Exchange.objects.create(owner=eve, name="Eve's")
    response = client.get(reverse("home"))
    names = [e.name for e in response.context["exchanges"]]
    assert names == ["My Gift Exchange", "Christmas"]  # Ended ones last
    content = response.content.decode()
    assert content.index("Create a gift exchange") < content.index("Your Circles")
    assert "The Smiths · Archived" in content


def test_delete_gift_exchange(client, b):
    url = reverse("exchange_delete", args=[b.exchange.pk])
    assert client.get(url).status_code == 200
    response = client.post(url)
    assert response.url == reverse("home")
    assert not Exchange.objects.exists()
    assert b.circle.people.count() == 6  # The circle stays


def test_cannot_delete_a_gift_exchange_under_way(client, b):
    Exchange.objects.filter(pk=b.exchange.pk).update(status=Exchange.Status.SENT)
    client.post(reverse("exchange_delete", args=[b.exchange.pk]))
    assert Exchange.objects.exists()


def test_deleting_a_circle_keeps_drafts_and_removes_ended(client, b):
    ended = b.new_exchange(name="Last year", status=Exchange.Status.ARCHIVED)
    client.post(reverse("circle_delete", args=[b.circle.pk]))
    b.exchange.refresh_from_db()
    assert b.exchange.circle is None
    assert not b.exchange.participants.exists()
    assert not Exchange.objects.filter(pk=ended.pk).exists()


def test_draw_date_must_be_before_gift_date(client, b):
    url = reverse("exchange_basics", args=[b.exchange.pk])
    response = client.post(url, basics(draw_date="2026-12-26"))
    assert "draw_date" in response.context["basics"].errors


def test_save_settings(client, b):
    url = reverse("exchange_basics", args=[b.exchange.pk])
    client.post(url, basics(name="Xmas", single_loop="on", avoid_repeats="2"))
    b.exchange.refresh_from_db()
    assert b.exchange.name == "Xmas"
    assert b.exchange.single_loop
    assert b.exchange.avoid_repeats == 2
    assert b.exchange.gift_date == datetime.date(2026, 12, 25)


def test_choose_who_takes_part(client, b):
    grace = b.circle.people.create(name="Grace", email="g@example.org")
    keep = [b.person[n].pk for n in ["Alice", "Carol", "Erin"]] + [grace.pk]
    client.post(reverse("exchange_people", args=[b.exchange.pk]), {"people": keep})
    names = {p.person.name for p in b.exchange.participants.all()}
    assert names == {"Alice", "Carol", "Erin", "Grace"}


def test_rule_for_one_exchange(client, b):
    url = reverse("exchange_rule_new", args=[b.exchange.pk])
    client.post(
        url,
        {
            "giver": b.person["Erin"].pk,
            "receiver": b.person["Frank"].pk,
            "kind": "exclude",
        },
    )
    rule = Rule.objects.get()
    assert rule.exchange == b.exchange


def test_exchange_rule_must_fit_standing_rules(client, b):
    b.rule("Erin", "Frank", Rule.Kind.REQUIRE)
    url = reverse("exchange_rule_new", args=[b.exchange.pk])
    response = client.post(
        url,
        {
            "giver": b.person["Erin"].pk,
            "receiver": b.person["Alice"].pk,
            "kind": "require",
        },
    )
    assert response.context["form"].errors
    assert Rule.objects.count() == 1


def test_remove_exchange_rule(client, b):
    rule = b.rule("Erin", "Frank", Rule.Kind.EXCLUDE, exchange=b.exchange)
    client.post(reverse("exchange_rule_delete", args=[b.exchange.pk, rule.pk]))
    assert not Rule.objects.exists()


def test_check_shows_counts_but_names_only_on_request(client, b):
    for name in ["Alice", "Bob", "Carol", "Dan"]:
        b.rule("Erin", name, Rule.Kind.EXCLUDE)
    url = reverse("exchange", args=[b.exchange.pk])
    assert client.get(url).context["check"] is None
    summary = client.get(url + "?check")
    assert summary.context["check"].ok
    assert b"very predictable" in summary.content
    assert b"can only ever get" not in summary.content
    details = client.get(url + "?check&details")
    assert b"Erin can only ever get Frank" in details.content


def test_check_explains_an_impossible_draw(client, b):
    b.rule("Alice", "Bob", Rule.Kind.REQUIRE)
    response = client.get(reverse("exchange", args=[b.exchange.pk]) + "?check")
    assert b"No draw is possible" in response.content
    assert b"same household" in response.content


def test_setup_is_locked_once_started(client, b):
    Exchange.objects.filter(pk=b.exchange.pk).update(status=Exchange.Status.SENT)
    client.post(reverse("exchange_basics", args=[b.exchange.pk]), basics(name="New"))
    client.post(reverse("exchange_people", args=[b.exchange.pk]), {"people": []})
    client.post(
        reverse("exchange_rule_new", args=[b.exchange.pk]),
        {
            "giver": b.person["Erin"].pk,
            "receiver": b.person["Frank"].pk,
            "kind": "exclude",
        },
    )
    b.exchange.refresh_from_db()
    assert b.exchange.name == "Christmas"
    assert b.exchange.participants.count() == 6
    assert not Rule.objects.exists()


def test_another_organizers_exchange_is_not_found(client, b):
    rule = b.rule("Erin", "Frank", Rule.Kind.EXCLUDE, exchange=b.exchange)
    eve = User.objects.create_user("eve@example.org", "unused password")
    client.force_login(eve)
    pk = b.exchange.pk
    for url, method in [
        (reverse("exchange", args=[pk]), "get"),
        (reverse("exchange", args=[pk]) + "?check&details", "get"),
        (reverse("exchange_basics", args=[pk]), "post"),
        (reverse("exchange_circle", args=[pk]), "post"),
        (reverse("exchange_circle_new", args=[pk]), "post"),
        (reverse("exchange_delete", args=[pk]), "post"),
        (reverse("exchange_people", args=[pk]), "post"),
        (reverse("exchange_rule_new", args=[pk]), "post"),
        (reverse("exchange_rule_delete", args=[pk, rule.pk]), "post"),
    ]:
        assert getattr(client, method)(url, basics()).status_code == 404, url
    assert Rule.objects.filter(pk=rule.pk).exists()
    assert Exchange.objects.filter(pk=pk).exists()
    assert client.get(reverse("home")).context["exchanges"].count() == 0


def test_dates_are_shown_in_iso_format_for_date_pickers(client, b):
    response = client.get(reverse("exchange", args=[b.exchange.pk]))
    assert b'type="date" name="gift_date" value="2026-12-25"' in response.content
