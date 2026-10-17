import datetime

import pytest
from django.urls import reverse

from giftexchange.accounts.models import User
from giftexchange.exchanges.models import Exchange, Rule

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


def test_new_exchange_includes_everyone(client, b):
    response = client.post(
        reverse("exchange_new", args=[b.circle.pk]), basics(name="Birthdays")
    )
    exchange = Exchange.objects.get(name="Birthdays")
    assert response.url == reverse("exchange", args=[exchange.pk])
    people = {p.person.name for p in exchange.participants.all()}
    assert people == set(b.person)


def test_exchanges_per_year_limit(client, b, settings):
    settings.MAX_EXCHANGES_PER_CIRCLE_PER_YEAR = 1
    response = client.post(reverse("exchange_new", args=[b.circle.pk]), basics())
    assert response.status_code == 200
    assert response.context["form"].errors
    client.post(
        reverse("exchange_new", args=[b.circle.pk]), basics(gift_date="2027-12-25")
    )
    assert Exchange.objects.count() == 2


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
        (reverse("exchange_new", args=[b.circle.pk]), "post"),
        (reverse("exchange_basics", args=[pk]), "post"),
        (reverse("exchange_people", args=[pk]), "post"),
        (reverse("exchange_rule_new", args=[pk]), "post"),
        (reverse("exchange_rule_delete", args=[pk, rule.pk]), "post"),
    ]:
        assert getattr(client, method)(url, basics()).status_code == 404, url
    assert Rule.objects.filter(pk=rule.pk).exists()


def test_dates_are_shown_in_iso_format_for_date_pickers(client, b):
    response = client.get(reverse("exchange", args=[b.exchange.pk]))
    assert b'type="date" name="gift_date" value="2026-12-25"' in response.content
