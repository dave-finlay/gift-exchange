import pytest
from django.urls import reverse

from giftexchange.accounts.models import User
from giftexchange.exchanges.models import Circle, Household, Person, Rule

pytestmark = pytest.mark.django_db


@pytest.fixture
def organizer(client):
    user = User.objects.create_user("alice@example.org", "unused password")
    client.force_login(user)
    return user


@pytest.fixture
def circle(organizer):
    circle = Circle.objects.create(owner=organizer, name="The Smiths")
    smiths = Household.objects.create(circle=circle, name="Smiths")
    for name, household in [("Alice", smiths), ("Bob", smiths), ("Carol", None)]:
        Person.objects.create(
            circle=circle,
            name=name,
            email=f"{name.lower()}@example.org",
            household=household,
        )
    return circle


def person(circle, name):
    return circle.people.get(name=name)


def test_home_shows_only_your_circles(client, circle):
    other = User.objects.create_user("eve@example.org", "unused password")
    Circle.objects.create(owner=other, name="Someone else's")
    response = client.get(reverse("home"))
    assert list(response.context["circles"]) == [circle]


def test_create_circle(client, organizer):
    response = client.post(reverse("circle_new"), {"name": "Friends"})
    circle = Circle.objects.get(name="Friends")
    assert circle.owner == organizer
    assert response.url == reverse("circle", args=[circle.pk])


def test_circle_limit(client, organizer, settings):
    settings.MAX_CIRCLES_PER_USER = 1
    client.post(reverse("circle_new"), {"name": "One"})
    response = client.post(reverse("circle_new"), {"name": "Two"})
    assert response.status_code == 200
    assert Circle.objects.count() == 1


def test_another_organizers_circle_is_not_found(client, circle):
    rule = Rule.objects.create(
        circle=circle,
        giver=person(circle, "Alice"),
        receiver=person(circle, "Carol"),
        kind=Rule.Kind.EXCLUDE,
    )
    eve = User.objects.create_user("eve@example.org", "unused password")
    client.force_login(eve)
    alice = person(circle, "Alice")
    for url, method in [
        (reverse("circle", args=[circle.pk]), "get"),
        (reverse("person_new", args=[circle.pk]), "post"),
        (reverse("person_edit", args=[circle.pk, alice.pk]), "post"),
        (reverse("paste", args=[circle.pk]), "post"),
        (reverse("rule_new", args=[circle.pk]), "post"),
        (reverse("rule_delete", args=[circle.pk, rule.pk]), "post"),
    ]:
        assert getattr(client, method)(url, {"name": "X"}).status_code == 404, url
    assert Rule.objects.filter(pk=rule.pk).exists()


def test_person_from_another_circle_is_not_found(client, circle, organizer):
    mine = Circle.objects.create(owner=organizer, name="Friends")
    url = reverse("person_edit", args=[mine.pk, person(circle, "Alice").pk])
    assert client.get(url).status_code == 404


def test_circle_page_groups_people_by_household(client, circle):
    response = client.get(reverse("circle", args=[circle.pk]))
    [(household, members)] = response.context["households"]
    assert household.name == "Smiths"
    assert [p.name for p in members] == ["Alice", "Bob"]
    assert [p.name for p in response.context["on_their_own"]] == ["Carol"]


def test_add_person_with_a_new_household(client, circle):
    client.post(
        reverse("person_new", args=[circle.pk]),
        {"name": "Dan", "email": "dan@example.org", "household": "Joneses"},
    )
    assert person(circle, "Dan").household.name == "Joneses"


def test_add_person_to_an_existing_household_ignores_case(client, circle):
    client.post(
        reverse("person_new", args=[circle.pk]),
        {"name": "Dan", "email": "alice@example.org", "household": "smiths"},
    )
    assert person(circle, "Dan").household.name == "Smiths"


def test_names_are_unique_in_a_circle(client, circle):
    response = client.post(
        reverse("person_new", args=[circle.pk]),
        {"name": "alice", "email": "other@example.org"},
    )
    assert response.context["form"].errors
    assert circle.people.count() == 3


def test_moving_the_last_member_removes_the_household(client, circle):
    carol = person(circle, "Carol")
    carol.household = Household.objects.create(circle=circle, name="Carol's")
    carol.save()
    client.post(
        reverse("person_edit", args=[circle.pk, carol.pk]),
        {
            "name": "Carol",
            "email": "carol@example.org",
            "household": "",
        },
    )
    assert not circle.households.filter(name="Carol's").exists()


def test_paste_a_list(client, circle):
    text = (
        "Dan, dan@example.org, Joneses\n"
        "\n"
        "Erin\terin@example.org\n"
        "Frank, frank@example.org, joneses\n"
    )
    client.post(reverse("paste", args=[circle.pk]), {"text": text})
    assert person(circle, "Dan").household == person(circle, "Frank").household
    assert person(circle, "Erin").household is None


def test_paste_adds_nobody_if_any_line_is_wrong(client, circle):
    text = "Dan, dan@example.org\nErin, not-an-email\nAlice, a@example.org\nFrank"
    response = client.post(reverse("paste", args=[circle.pk]), {"text": text})
    errors = response.context["form"].errors["text"]
    assert [e[:6] for e in errors] == ["Line 2", "Line 3", "Line 4"]
    assert circle.people.count() == 3


def test_paste_respects_the_size_limit(client, circle, settings):
    settings.MAX_PEOPLE_PER_CIRCLE = 4
    text = "Dan, dan@example.org\nErin, erin@example.org"
    response = client.post(reverse("paste", args=[circle.pk]), {"text": text})
    assert response.context["form"].errors
    assert circle.people.count() == 3


def add_rule(client, circle, giver, kind, receiver, both_ways=False):
    data = {
        "giver": person(circle, giver).pk,
        "receiver": person(circle, receiver).pk,
        "kind": kind,
    }
    if both_ways:
        data["both_ways"] = "on"
    return client.post(reverse("rule_new", args=[circle.pk]), data)


def test_exclusion_both_ways(client, circle):
    add_rule(client, circle, "Alice", "exclude", "Carol", both_ways=True)
    pairs = {(r.giver.name, r.receiver.name) for r in Rule.objects.all()}
    assert pairs == {("Alice", "Carol"), ("Carol", "Alice")}


@pytest.mark.parametrize(
    "giver, kind, receiver, both_ways",
    [
        ("Alice", "require", "Carol", True),  # forced pairings go one way
        ("Alice", "require", "Bob", False),  # same household
        ("Alice", "exclude", "Alice", False),
    ],
)
def test_rejected_rules(client, circle, giver, kind, receiver, both_ways):
    response = add_rule(client, circle, giver, kind, receiver, both_ways)
    assert response.context["form"].errors
    assert not Rule.objects.exists()


def test_conflicting_forced_pairings_are_rejected(client, circle):
    add_rule(client, circle, "Alice", "require", "Carol")
    for giver, receiver in [("Alice", "Bob"), ("Bob", "Carol")]:
        response = add_rule(client, circle, giver, "require", receiver)
        assert response.status_code == 200
    add_rule(client, circle, "Alice", "exclude", "Carol")
    assert Rule.objects.count() == 1


def test_remove_rule(client, circle):
    add_rule(client, circle, "Alice", "exclude", "Carol")
    rule = Rule.objects.get()
    assert (
        client.get(reverse("rule_delete", args=[circle.pk, rule.pk])).status_code == 405
    )
    client.post(reverse("rule_delete", args=[circle.pk, rule.pk]))
    assert not Rule.objects.exists()


def test_delete_person(client, circle):
    bob = person(circle, "Bob")
    url = reverse("person_delete", args=[circle.pk, bob.pk])
    assert client.get(url).status_code == 200  # A confirmation page
    assert circle.people.filter(name="Bob").exists()
    client.post(url)
    assert not circle.people.filter(name="Bob").exists()


def test_deleting_the_last_member_removes_the_household(client, circle):
    for name in ["Alice", "Bob"]:
        client.post(reverse("person_delete", args=[circle.pk, person(circle, name).pk]))
    assert not circle.households.exists()


def test_cannot_delete_someone_in_a_started_exchange(client, circle):
    from giftexchange.exchanges.models import Exchange

    bob = person(circle, "Bob")
    exchange = Exchange.objects.create(
        circle=circle, name="Christmas", gift_date="2026-12-25", status="sent"
    )
    exchange.participants.create(person=bob)
    url = reverse("person_delete", args=[circle.pk, bob.pk])
    response = client.post(url)
    assert b"can't be deleted yet" in response.content
    assert circle.people.filter(name="Bob").exists()


def test_cannot_delete_another_organizers_person(client, circle):
    eve = User.objects.create_user("eve@example.org", "unused password")
    client.force_login(eve)
    url = reverse("person_delete", args=[circle.pk, person(circle, "Bob").pk])
    assert client.post(url).status_code == 404
    assert circle.people.filter(name="Bob").exists()


def test_header_has_home_and_a_menu(client, circle):
    content = client.get(reverse("home")).content
    assert b"Home</a>" in content
    assert b'<details class="menu">' in content


def test_swiping_left_on_a_circle_goes_home(client, circle):
    content = client.get(reverse("circle", args=[circle.pk])).content
    assert f'data-swipe-left="{reverse("home")}"'.encode() in content
    home = client.get(reverse("home")).content
    assert b'data-swipe-left=""' in home


def test_person_rows_link_to_edit_and_swipe_to_delete(client, circle):
    content = client.get(reverse("circle", args=[circle.pk])).content.decode()
    alice = person(circle, "Alice")
    edit = reverse("person_edit", args=[circle.pk, alice.pk])
    delete = reverse("person_delete", args=[circle.pk, alice.pk])
    assert f'href="{edit}" data-swipe-left="{delete}"' in content
