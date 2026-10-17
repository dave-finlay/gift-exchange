import pytest
from django.urls import reverse

from giftexchange.accounts.models import User
from giftexchange.exchanges.models import Circle, Household, Person, Rule

pytestmark = pytest.mark.django_db


@pytest.fixture
def organiser(client):
    user = User.objects.create_user("alice@example.org", "unused password")
    client.force_login(user)
    return user


@pytest.fixture
def circle(organiser):
    circle = Circle.objects.create(owner=organiser, name="The Smiths")
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


def test_create_circle(client, organiser):
    response = client.post(reverse("circle_new"), {"name": "Friends"})
    circle = Circle.objects.get(name="Friends")
    assert circle.owner == organiser
    assert response.url == reverse("circle", args=[circle.pk])


def test_circle_limit(client, organiser, settings):
    settings.MAX_CIRCLES_PER_USER = 1
    client.post(reverse("circle_new"), {"name": "One"})
    response = client.post(reverse("circle_new"), {"name": "Two"})
    assert response.status_code == 200
    assert Circle.objects.count() == 1


def test_another_organisers_circle_is_not_found(client, circle):
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


def test_person_from_another_circle_is_not_found(client, circle, organiser):
    mine = Circle.objects.create(owner=organiser, name="Friends")
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
            "active": "on",
        },
    )
    assert not circle.households.filter(name="Carol's").exists()


def test_deactivate(client, circle):
    bob = person(circle, "Bob")
    client.post(
        reverse("person_edit", args=[circle.pk, bob.pk]),
        {"name": "Bob", "email": "bob@example.org", "household": "Smiths"},
    )
    bob.refresh_from_db()
    assert not bob.active
    response = client.get(reverse("circle", args=[circle.pk]))
    assert [p.name for p in response.context["inactive"]] == ["Bob"]


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
