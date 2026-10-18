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
        "gift_date": "2026-12-25",
        "draw_date": "",
        "budget": "",
        "note": "",
        "confirm_first": "on",
        "avoid_repeats": "1",
    }
    data.update(changes)
    return {k: v for k, v in data.items() if v is not None}


def save(client, exchange, people=None, **changes):
    """Press Save on the gift exchange's form. Unless `people` is given,
    everyone currently taking part stays ticked."""
    if people is None:
        people = list(exchange.participants.values_list("person_id", flat=True))
    url = reverse("exchange_save", args=[exchange.pk])
    return client.post(url, {**basics(**changes), "people": people})


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


def test_the_circle_cant_be_changed_once_chosen(client, b):
    other = Circle.objects.create(owner=b.circle.owner, name="Friends")
    client.post(reverse("exchange_circle", args=[b.exchange.pk]), {"circle": other.pk})
    client.post(reverse("exchange_circle_new", args=[b.exchange.pk]))
    b.exchange.refresh_from_db()
    assert b.exchange.circle == b.circle
    assert Circle.objects.count() == 2


def test_circle_card_shows_some_names_and_links_to_the_circle(client, b, settings):
    response = client.get(reverse("exchange", args=[b.exchange.pk]))
    content = response.content.decode()
    assert f'<a class="card" href="{reverse("circle", args=[b.circle.pk])}">' in content
    assert "Alice, Bob, Carol, Dan, Erin, Frank<" in content
    for i in range(5):
        b.circle.people.create(name=f"Zed {i}", email="z@example.org")
    content = client.get(reverse("exchange", args=[b.exchange.pk])).content.decode()
    assert "Alice, Bob, Carol, Dan, Erin, Frank, Zed 0, Zed 1 and 3 more" in content


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
    response = client.post(reverse("exchange_rule_new", args=[exchange.pk]), {})
    assert response.url == reverse("exchange", args=[exchange.pk])
    save(client, exchange)  # Settings save fine without a circle
    assert not exchange.participants.exists()


def test_gift_exchanges_per_circle_per_year_limit(client, b, settings):
    settings.MAX_EXCHANGES_PER_CIRCLE_PER_YEAR = 1
    exchange = create(client)
    client.post(reverse("exchange_circle", args=[exchange.pk]), {"circle": b.circle.pk})
    response = save(client, exchange)  # Also 2026
    assert response.context["basics"].errors
    save(client, exchange, gift_date="2027-12-25")
    exchange.refresh_from_db()
    assert exchange.gift_date.year == 2027


def test_home_leads_with_gift_exchanges(client, b):
    create(client)
    Exchange.objects.filter(pk=b.exchange.pk).update(status=Exchange.Status.ARCHIVED)
    eve = User.objects.create_user("eve@example.org", "unused password")
    Exchange.objects.create(owner=eve, name="Eve's")
    response = client.get(reverse("home"))
    names = [card[0].name for card in response.context["cards"]]
    assert names == ["My Gift Exchange", "Christmas"]  # Ended ones last
    content = response.content.decode()
    assert content.index("Create a gift exchange") < content.index("Your Circles")
    assert "The Smiths · Ended" in content


@pytest.mark.parametrize(
    "status, confirm_first, action, line",
    [
        ("draft", False, "Draw and send", "6 people"),
        ("draft", True, "Send “Are you in?”", "6 people"),
        ("joining", True, "Draw and send", "0 of 6 in"),
        ("sent", False, "End gift exchange", "0 of 6 have looked · 0 gifts bought"),
        ("archived", False, None, ""),
    ],
)
def test_home_cards_show_progress_and_the_next_step(
    client, b, status, confirm_first, action, line
):
    Exchange.objects.filter(pk=b.exchange.pk).update(
        status=status, confirm_first=confirm_first
    )
    [(exchange, card_line, card_action)] = client.get(reverse("home")).context["cards"]
    assert card_line == line
    assert (card_action[0] if card_action else None) == action


def test_delete_gift_exchange(client, b):
    url = reverse("exchange_delete", args=[b.exchange.pk])
    assert client.get(url).status_code == 200
    response = client.post(url)
    assert response.url == reverse("home")
    assert not Exchange.objects.exists()
    assert b.circle.people.count() == 6  # The circle stays


def test_deleting_a_circle_keeps_drafts_and_removes_ended(client, b):
    ended = b.new_exchange(name="Last year", status=Exchange.Status.ARCHIVED)
    client.post(reverse("circle_delete", args=[b.circle.pk]))
    b.exchange.refresh_from_db()
    assert b.exchange.circle is None
    assert not b.exchange.participants.exists()
    assert not Exchange.objects.filter(pk=ended.pk).exists()


def test_draw_date_must_be_before_gift_date(client, b):
    response = save(client, b.exchange, draw_date="2026-12-26")
    assert "draw_date" in response.context["basics"].errors


def test_save_settings(client, b):
    response = save(
        client, b.exchange, budget="$20", single_loop="on", avoid_repeats="2"
    )
    assert response.url == reverse("home")
    b.exchange.refresh_from_db()
    assert b.exchange.budget == "$20"
    assert b.exchange.single_loop
    assert b.exchange.avoid_repeats == 2
    assert b.exchange.gift_date == datetime.date(2026, 12, 25)


def test_choose_who_takes_part(client, b):
    grace = b.circle.people.create(name="Grace", email="g@example.org")
    keep = [b.person[n].pk for n in ["Alice", "Carol", "Erin"]] + [grace.pk]
    save(client, b.exchange, people=keep)
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
    save(client, b.exchange, people=[], budget="$5")
    client.post(
        reverse("exchange_rule_new", args=[b.exchange.pk]),
        {
            "giver": b.person["Erin"].pk,
            "receiver": b.person["Frank"].pk,
            "kind": "exclude",
        },
    )
    b.exchange.refresh_from_db()
    assert b.exchange.budget == ""
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
        (reverse("exchange_save", args=[pk]), "post"),
        (reverse("exchange_end", args=[pk]), "post"),
        (reverse("exchange_circle", args=[pk]), "post"),
        (reverse("exchange_circle_new", args=[pk]), "post"),
        (reverse("exchange_delete", args=[pk]), "post"),
        (reverse("exchange_rename", args=[pk]), "post"),
        (reverse("exchange_rule_new", args=[pk]), "post"),
        (reverse("exchange_rule_delete", args=[pk, rule.pk]), "post"),
    ]:
        assert getattr(client, method)(url, basics()).status_code == 404, url
    assert Rule.objects.filter(pk=rule.pk).exists()
    assert Exchange.objects.filter(pk=pk).exists()
    assert client.get(reverse("home")).context["cards"] == []


def test_dates_are_shown_in_iso_format_for_date_pickers(client, b):
    response = client.get(reverse("exchange", args=[b.exchange.pk]))
    assert b'type="date" name="gift_date" value="2026-12-25"' in response.content


@pytest.mark.parametrize("status", ["draft", "joining", "sent"])
def test_rename_at_any_stage(client, b, status):
    Exchange.objects.filter(pk=b.exchange.pk).update(status=status)
    url = reverse("exchange_rename", args=[b.exchange.pk])
    response = client.post(url, {"name": "Christmas 2026"})
    assert response.url == reverse("exchange", args=[b.exchange.pk])
    b.exchange.refresh_from_db()
    assert b.exchange.name == "Christmas 2026"


def test_rename_needs_a_name(client, b):
    response = client.post(
        reverse("exchange_rename", args=[b.exchange.pk]), {"name": ""}
    )
    assert response.context["form"].errors
    b.exchange.refresh_from_db()
    assert b.exchange.name == "Christmas"


def test_save_then_check_or_add_a_rule_keeps_what_was_typed(client, b):
    url = reverse("exchange_save", args=[b.exchange.pk])
    people = list(b.exchange.participants.values_list("person_id", flat=True))
    response = client.post(
        url, {**basics(budget="$9"), "people": people, "then": "check"}
    )
    assert response.url.endswith("?check#check")
    response = client.post(
        url, {**basics(budget="$10"), "people": people, "then": "add_rule"}
    )
    assert response.url == reverse("exchange_rule_new", args=[b.exchange.pk])
    b.exchange.refresh_from_db()
    assert b.exchange.budget == "$10"


def test_under_way_shows_progress_instead_of_the_form(client, b):
    Exchange.objects.filter(pk=b.exchange.pk).update(status=Exchange.Status.JOINING)
    page = client.get(reverse("exchange", args=[b.exchange.pk])).content.decode()
    assert "<h2>Progress</h2>" in page
    assert "No answer yet" in page
    assert 'action="/exchanges/' not in page.split("danger-zone")[0]  # No form


def test_end_a_gift_exchange(client, b):
    Exchange.objects.filter(pk=b.exchange.pk).update(status=Exchange.Status.SENT)
    url = reverse("exchange_end", args=[b.exchange.pk])
    assert b"Nobody is emailed" in client.get(url).content
    assert client.post(url).url == reverse("home")
    b.exchange.refresh_from_db()
    assert b.exchange.status == Exchange.Status.ARCHIVED
    assert b.exchange.archived_at is not None


def test_only_drawn_gift_exchanges_can_be_ended(client, b):
    client.post(reverse("exchange_end", args=[b.exchange.pk]))
    b.exchange.refresh_from_db()
    assert b.exchange.status == Exchange.Status.DRAFT
