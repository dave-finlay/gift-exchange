from random import Random

import pytest
from django.utils import timezone

from giftexchange import matching
from giftexchange.exchanges import draws, secrecy
from giftexchange.exchanges.models import Assignment, Exchange, Participant, Rule

EXCLUDE, REQUIRE = Rule.Kind.EXCLUDE, Rule.Kind.REQUIRE

FAMILY = [["Alice", "Bob"], ["Carol", "Dan"], ["Erin"], ["Frank"]]


def allowed_names(b, exchange=None):
    """The problem's allowed pairings, by name."""
    problem = draws.build_problem(exchange or b.exchange)
    name = {
        p.pk: p.person.name
        for p in Participant.objects.filter(pk__in=problem.participants)
    }
    return {(name[g], name[r]) for g, rs in problem.allowed.items() for r in rs}


def saved_pairings(exchange):
    """(giver, receiver) names from the saved, encrypted assignments."""
    name = {p.pk: p.person.name for p in exchange.participants.all()}
    return {
        (name[a.giver_id], name[secrecy.receiver_id(a)])
        for a in Assignment.objects.filter(giver__exchange=exchange).select_related(
            "giver"
        )
    }


def test_households_and_exclusions(build):
    b = build(FAMILY)
    b.rule("Erin", "Frank", EXCLUDE)
    allowed = allowed_names(b)
    assert ("Alice", "Bob") not in allowed
    assert ("Bob", "Alice") not in allowed
    assert ("Erin", "Frank") not in allowed
    assert ("Frank", "Erin") in allowed
    assert ("Alice", "Carol") in allowed


def test_rules_for_another_exchange_dont_apply(build):
    b = build(FAMILY)
    other = b.new_exchange(name="Birthdays")
    b.rule("Erin", "Frank", EXCLUDE, exchange=other)
    assert ("Erin", "Frank") in allowed_names(b)
    assert ("Erin", "Frank") not in allowed_names(b, other)


def test_declined_and_withdrawn_people_are_left_out(build):
    b = build(FAMILY)
    Participant.objects.filter(pk=b.participant("Erin").pk).update(
        response=Participant.Response.DECLINED
    )
    Participant.objects.filter(pk=b.participant("Frank").pk).update(
        withdrawn_at=timezone.now()
    )
    problem = draws.build_problem(b.exchange)
    assert len(problem.participants) == 4


def test_forced_pairing(build):
    b = build(FAMILY)
    b.rule("Bob", "Carol", REQUIRE)
    problem = draws.build_problem(b.exchange)
    assert problem.required == {b.participant("Bob").pk: b.participant("Carol").pk}


def test_forced_pairing_with_someone_not_taking_part_is_ignored(build):
    b = build(FAMILY)
    b.rule("Bob", "Carol", REQUIRE)
    b.participant("Carol").delete()
    assert draws.build_problem(b.exchange).required == {}


@pytest.mark.parametrize(
    "rules, message",
    [
        ([("Alice", "Bob", REQUIRE)], "same household"),
        ([("Alice", "Erin", REQUIRE), ("Alice", "Erin", EXCLUDE, "this")], "both"),
        ([("Alice", "Erin", REQUIRE), ("Alice", "Frank", REQUIRE, "this")], "two"),
        ([("Alice", "Erin", REQUIRE), ("Carol", "Erin", REQUIRE)], "two"),
        ([("Alice", "Erin", REQUIRE), ("Erin", "Alice", REQUIRE)], "each other"),
    ],
)
def test_conflicting_forced_pairings(build, rules, message):
    b = build(FAMILY)
    for giver, receiver, kind, *scope in rules:
        b.rule(giver, receiver, kind, exchange=b.exchange if scope else None)
    with pytest.raises(draws.RuleConflict) as error:
        draws.build_problem(b.exchange)
    assert message in str(error.value)


def test_draw_saves_a_valid_encrypted_assignment(build):
    b = build(FAMILY)
    result = draws.draw(b.exchange, Random(1))
    assert isinstance(result, matching.Draw)
    b.exchange.refresh_from_db()
    assert b.exchange.drawn_at is not None
    pairings = saved_pairings(b.exchange)
    assert len(pairings) == 6
    assert {g for g, _ in pairings} == {r for _, r in pairings} == set(b.person)
    assert pairings <= allowed_names(b)
    for a in Assignment.objects.all():
        assert "Alice" not in a.receiver_ciphertext


def test_exchange_is_drawn_only_once(build):
    b = build(FAMILY)
    draws.draw(b.exchange, Random(1))
    with pytest.raises(ValueError):
        draws.draw(b.exchange, Random(2))


def test_infeasible_draw_saves_nothing(build):
    b = build([["Alice", "Bob", "Carol"], ["Dan"]])
    result = draws.draw(b.exchange, Random(1))
    assert isinstance(result, matching.Infeasible)
    assert not Assignment.objects.exists()
    b.exchange.refresh_from_db()
    assert b.exchange.drawn_at is None


def test_recent_pairings_are_avoided(build):
    b = build(FAMILY)
    draws.draw(b.exchange, Random(1))
    last_year = saved_pairings(b.exchange)
    this_year = b.new_exchange(name="Next Christmas", avoid_repeats=1)
    assert not last_year & allowed_names(b, this_year)

    no_memory = b.new_exchange(name="Fresh start", avoid_repeats=0)
    assert last_year <= allowed_names(b, no_memory)


def test_forced_pairing_wins_over_avoiding_repeats(build):
    b = build(FAMILY)
    draws.draw(b.exchange, Random(1))
    giver, receiver = sorted(saved_pairings(b.exchange))[0]
    this_year = b.new_exchange(name="Next Christmas")
    b.rule(giver, receiver, REQUIRE, exchange=this_year)
    assert (giver, receiver) in allowed_names(b, this_year)


def test_recent_pairings_skip_deleted_people(build):
    b = build([["Alice"], ["Bob"], ["Carol"], ["Dan"]])
    draws.draw(b.exchange, Random(1))
    Exchange.objects.filter(pk=b.exchange.pk).update(status=Exchange.Status.ARCHIVED)
    b.person.pop("Dan").delete()
    this_year = b.new_exchange(name="Next")
    assert all("Dan" not in pair for pair in allowed_names(b, this_year))
