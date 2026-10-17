from random import Random

from giftexchange.exchanges import checks, draws
from giftexchange.exchanges.models import Rule

FAMILY = [["Alice", "Bob"], ["Carol", "Dan"], ["Erin"], ["Frank"]]


def check(exchange):
    return checks.check(exchange, Random(1), samples=300)


def test_names():
    assert checks.names([]) == ""
    assert checks.names(["Bob"]) == "Bob"
    assert checks.names(["Bob", "Alice"]) == "Alice and Bob"
    assert checks.names(["Carol", "Alice", "Bob"]) == "Alice, Bob and Carol"


def test_a_relaxed_exchange_is_fine(build):
    result = check(build(FAMILY).exchange)
    assert result.ok
    assert result.participants == 6
    assert result.predictable == []
    assert result.exact


def test_household_bottleneck_is_explained(build):
    result = check(build([["Alice", "Bob", "Carol"], ["Dan"]]).exchange)
    assert not result.ok
    [problem] = result.problems
    assert "can only give to Dan" in problem
    assert "Remove a rule" in result.hints[0]
    assert "Add more people." in result.hints
    # There are no previous exchanges, so avoiding repeats isn't the cause.
    assert not any("previous exchanges" in hint for hint in result.hints)


def test_two_people_need_mutual_pairs(build):
    result = check(build([["Alice"], ["Bob"]]).exchange)
    assert "two people giving to each other" in result.problems[0]
    assert "Allow mutual pairs." in result.hints


def test_too_few_people(build):
    b = build([["Alice"], ["Bob"]])
    b.participant("Bob").delete()
    assert check(b.exchange).problems == ["An exchange needs at least 2 people."]


def test_rule_conflicts_are_reported(build):
    b = build(FAMILY)
    b.rule("Alice", "Bob", Rule.Kind.REQUIRE)
    assert "same household" in check(b.exchange).problems[0]


def test_predictable_assignment(build):
    # Erin can't give to Alice, Bob, Carol or Dan, so only to Frank.
    b = build(FAMILY)
    for name in ["Alice", "Bob", "Carol", "Dan"]:
        b.rule("Erin", name, Rule.Kind.EXCLUDE)
    result = check(b.exchange)
    assert result.ok
    erin = [p for p in result.predictable if p.giver == "Erin"]
    assert erin and erin[0].receiver == "Frank" and erin[0].only


def test_forced_pairings_are_not_reported_as_predictable(build):
    b = build(FAMILY)
    b.rule("Erin", "Frank", Rule.Kind.REQUIRE)
    assert "Erin" not in {p.giver for p in check(b.exchange).predictable}


def test_check_saves_nothing(build):
    b = build(FAMILY)
    check(b.exchange)
    b.exchange.refresh_from_db()
    assert b.exchange.drawn_at is None
    assert not b.exchange.participants.filter(assignment__isnull=False).exists()


def test_avoiding_repeats_can_make_a_draw_predictable(build):
    b = build([["Alice"], ["Bob"], ["Carol"]])
    draws.draw(b.exchange, Random(1))
    # Three people with no mutual pairs: the only draws are the two loops,
    # and last year's loop is now ruled out, so only the reverse is left.
    this_year = b.new_exchange(name="Next", avoid_repeats=1)
    result = check(this_year)
    assert result.ok
    assert len(result.predictable) == 3


def test_hint_about_recent_pairings(build):
    b = build([["Alice"], ["Bob"], ["Carol"]])
    draws.draw(b.exchange, Random(1))
    # Three people without mutual pairs can only form one of two loops.
    # Last year's loop is now ruled out; ruling out Alice's step in the
    # other one leaves no draw at all.
    this_year = b.new_exchange(name="Next", avoid_repeats=1)
    alice = b.participant("Alice", this_year)
    [receiver] = draws.build_problem(this_year).allowed[alice.pk]
    other = this_year.participants.get(pk=receiver).person.name
    b.rule("Alice", other, Rule.Kind.EXCLUDE, exchange=this_year)
    result = check(this_year)
    assert not result.ok
    assert "Avoid pairings from fewer previous exchanges." in result.hints
