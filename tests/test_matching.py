import math
import time
from collections import Counter
from itertools import permutations
from random import Random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from giftexchange.matching import (
    Draw,
    Infeasible,
    Predictability,
    Problem,
    Undetermined,
    draw,
    is_valid,
    predictability,
    repair,
)

# Helpers


def households_problem(households, *, excluded=(), **kwargs):
    """Participants grouped into households; nobody gives within their
    own household, nor along any `excluded` (giver, receiver) pair."""
    people = tuple(p for household in households for p in household)
    home = {p: i for i, household in enumerate(households) for p in household}
    excluded = set(excluded)
    allowed = {
        g: frozenset(r for r in people if home[r] != home[g] and (g, r) not in excluded)
        for g in people
    }
    return Problem(people, allowed, **kwargs)


def follows_rules(problem, assignment):
    """An independent check of a draw, written separately from the module."""
    people = set(problem.participants)
    if set(assignment) != people or set(assignment.values()) != people:
        return False
    for giver, receiver in assignment.items():
        if receiver not in set(problem.allowed.get(giver, ())):
            return False
    if any(assignment[g] != r for g, r in problem.required.items()):
        return False
    if problem.single_loop:
        if people:
            start = problem.participants[0]
            seen, u = {start}, assignment[start]
            while u != start:
                seen.add(u)
                u = assignment[u]
            if seen != people:
                return False
    elif not problem.allow_mutual and any(
        assignment[r] == g for g, r in assignment.items()
    ):
        return False
    return True


def all_valid_draws(problem):
    """Brute force: every valid draw. Only for small problems."""
    people = problem.participants
    draws = (dict(zip(people, p, strict=True)) for p in permutations(people))
    return [d for d in draws if follows_rules(problem, d)]


def options(problem, giver):
    """The receivers a giver can actually get, given the forced pairings."""
    if giver in problem.required:
        return {problem.required[giver]}
    taken = set(problem.required.values())
    return set(problem.allowed.get(giver, ())) - taken


def key(assignment):
    return tuple(sorted(assignment.items()))


@st.composite
def problems(data, max_size=6):
    """Random small problems: mostly-allowed pairings, some forced pairings,
    and random structure settings."""
    n = data(st.integers(0, max_size))
    people = tuple(range(n))
    pairs = [(g, r) for g in people for r in people if g != r]
    excluded = data(st.sets(st.sampled_from(pairs))) if pairs else set()
    allowed = {
        g: frozenset(r for r in people if r != g and (g, r) not in excluded)
        for g in people
    }
    allow_mutual = data(st.booleans())
    single_loop = data(st.booleans())
    required = {}
    for giver in data(st.permutations(people)):
        if not data(st.booleans()) or data(st.booleans()):
            continue  # about a quarter of givers get a forced pairing
        choices = sorted(
            r
            for r in allowed[giver]
            if r not in required.values()
            and (allow_mutual or single_loop or required.get(r) != giver)
        )
        if choices:
            required[giver] = data(st.sampled_from(choices))
    return Problem(people, allowed, required, allow_mutual, single_loop)


# Draws


@settings(max_examples=300, deadline=None)
@given(problem=problems(), seed=st.integers(0, 2**32))
def test_draw_agrees_with_brute_force(problem, seed):
    valid = all_valid_draws(problem)
    result = draw(problem, Random(seed))
    if valid:
        assert isinstance(result, Draw)
        assert follows_rules(problem, result.assignment)
        assert is_valid(problem, result.assignment)
    else:
        assert isinstance(result, Infeasible)


@settings(max_examples=300, deadline=None)
@given(problem=problems(), seed=st.integers(0, 2**32))
def test_hall_explanation_is_a_real_bottleneck(problem, seed):
    result = draw(problem, Random(seed))
    if isinstance(result, Infeasible) and result.reason == "hall":
        assert len(result.receivers) < len(result.givers)
        for giver in result.givers:
            assert options(problem, giver) <= result.receivers


def test_hall_explanation_names_the_household():
    problem = households_problem([["Ann", "Bob", "Cat"], ["Dan"]])
    result = draw(problem, Random(1))
    assert isinstance(result, Infeasible)
    assert result.reason == "hall"
    assert result.receivers == {"Dan"}
    assert len(result.givers) >= 2
    assert result.givers <= {"Ann", "Bob", "Cat"}


def test_two_people_only_work_with_mutual_pairs():
    problem = households_problem([["Ann"], ["Bob"]])
    assert draw(problem, Random(1)) == Infeasible("mutual")
    allowed = households_problem([["Ann"], ["Bob"]], allow_mutual=True)
    assert draw(allowed, Random(1)).assignment == {"Ann": "Bob", "Bob": "Ann"}


def test_forced_pairing_is_kept():
    problem = households_problem(
        [["Alice", "Bob"], ["Carol", "Frank"], ["Dan"], ["Erin"]],
        required={"Bob": "Carol"},
    )
    for seed in range(50):
        assert draw(problem, Random(seed)).assignment["Bob"] == "Carol"


def test_single_loop_goes_through_everyone():
    problem = households_problem(
        [["A", "B"], ["C", "D"], ["E", "F"], ["G"]], single_loop=True
    )
    for seed in range(50):
        assignment = draw(problem, Random(seed)).assignment
        seen, person = {"A"}, assignment["A"]
        while person != "A":
            seen.add(person)
            person = assignment[person]
        assert seen == set(problem.participants)


def test_forced_pairings_closing_a_small_loop():
    problem = households_problem(
        [["A"], ["B"], ["C"], ["D"]],
        required={"A": "B", "B": "A"},
        single_loop=True,
    )
    assert draw(problem, Random(1)) == Infeasible("loop")


def test_empty_exchange():
    assert draw(Problem((), {}), Random(1)) == Draw({}, True)


def test_search_budget_gives_undetermined():
    problem = households_problem([["Ann"], ["Bob"]])
    result = draw(problem, Random(1), max_attempts=0, max_nodes=0)
    assert result == Undetermined("mutual")


@pytest.mark.parametrize(
    "bad",
    [
        dict(participants=("A", "A"), allowed={}),
        dict(participants=("A", "B"), allowed={"A": {"A"}}),
        dict(participants=("A", "B"), allowed={"A": {"C"}}),
        dict(participants=("A", "B"), allowed={"A": {"B"}}, required={"B": "A"}),
        dict(
            participants=("A", "B", "C"),
            allowed={"A": {"C"}, "B": {"C"}},
            required={"A": "C", "B": "C"},
        ),
        dict(
            participants=("A", "B"),
            allowed={"A": {"B"}, "B": {"A"}},
            required={"A": "B", "B": "A"},
        ),
    ],
)
def test_problem_rejects_inconsistent_rules(bad):
    with pytest.raises(ValueError):
        Problem(**bad)


# Fairness


def chi_squared(counts, expected):
    return sum((counts.get(k, 0) - expected) ** 2 / expected for k in counts)


@pytest.mark.parametrize("single_loop", [False, True])
def test_every_valid_draw_is_equally_likely(single_loop):
    problem = households_problem(
        [["A", "B"], ["C", "D"], ["E", "F"], ["G"]], single_loop=single_loop
    )
    valid = {key(d) for d in all_valid_draws(problem)}
    rng = Random(2026)
    samples = 100 * len(valid)
    counts = Counter(key(draw(problem, rng).assignment) for _ in range(samples))
    assert set(counts) == valid
    # With len(valid) - 1 degrees of freedom, a fair draw almost never
    # lands more than 6 standard deviations above the mean.
    df = len(valid) - 1
    assert chi_squared(counts, samples / len(valid)) < df + 6 * math.sqrt(2 * df)


@pytest.mark.parametrize("single_loop", [False, True])
def test_markov_fallback_is_valid_and_reaches_every_pairing(single_loop):
    problem = households_problem(
        [["A", "B"], ["C", "D"], ["E", "F"], ["G"]], single_loop=single_loop
    )
    for seed in range(30):
        result = draw(problem, Random(seed), max_attempts=0)
        assert result.exact is False
        assert follows_rules(problem, result.assignment)
    # Every pairing that's in some valid draw turns up in the chain's draws.
    possible = {pair for d in all_valid_draws(problem) for pair in d.items()}
    stats = predictability(problem, Random(1), samples=2000, max_attempts=0)
    assert stats.exact is False
    seen = {(g, r) for g, receivers in stats.counts.items() for r in receivers}
    assert seen == possible


# Test mode


def test_predictability_counts_every_draw():
    problem = households_problem(
        [["Alice", "Bob"], ["Carol", "Frank"], ["Dan"], ["Erin"]],
        required={"Bob": "Carol"},
    )
    result = predictability(problem, Random(1), samples=200)
    assert isinstance(result, Predictability)
    assert result.exact
    assert result.counts["Bob"] == Counter({"Carol": 200})
    for receivers in result.counts.values():
        assert sum(receivers.values()) == 200


def test_predictability_reports_infeasible():
    problem = households_problem([["Ann", "Bob", "Cat"], ["Dan"]])
    assert isinstance(predictability(problem, Random(1)), Infeasible)


# Repairs


def test_withdrawal_simple_fix():
    before = {"A": "B", "B": "C", "C": "D", "D": "A"}
    after = households_problem([["A"], ["C"], ["D"]])
    result = repair(after, before, Random(1))
    assert result.assignment == {"A": "C", "C": "D", "D": "A"}
    assert result.changed == {"A"}


def test_withdrawal_from_a_mutual_pair_needs_a_swap():
    before = {"A": "B", "B": "A", "C": "D", "D": "C"}
    after = households_problem([["A"], ["C"], ["D"]], allow_mutual=True)
    result = repair(after, before, Random(1))
    assert follows_rules(after, result.assignment)
    assert len(result.changed) == 2
    assert "A" in result.changed


def test_late_joiner_changes_one_giver():
    before = {"A": "B", "B": "C", "C": "A"}
    after = households_problem([["A"], ["B"], ["C"], ["D"]], single_loop=True)
    result = repair(after, before, Random(1))
    assert follows_rules(after, result.assignment)
    assert len(result.changed) == 1


@st.composite
def repair_cases(data):
    """A problem after a change, and a draw from before it (one person fewer
    or one more)."""
    problem = data(problems(max_size=5))
    people = list(problem.participants)
    if data(st.booleans()):
        before_people = [*people, "withdrawn"]
    else:
        if not people:
            return problem, {}
        late = data(st.sampled_from(people))
        before_people = [p for p in people if p != late]
    receivers = data(st.permutations(before_people))
    return problem, dict(zip(before_people, receivers, strict=True))


@settings(max_examples=300, deadline=None)
@given(case=repair_cases(), seed=st.integers(0, 2**32))
def test_repair_is_the_smallest_valid_change(case, seed):
    problem, before = case
    existing = [g for g in problem.participants if g in before]
    must = [g for g in existing if before[g] not in problem.participants]

    def changed(assignment):
        return sum(assignment[g] != before[g] for g in existing)

    smallest = min((changed(d) for d in all_valid_draws(problem)), default=None)
    result = repair(problem, before, Random(seed), max_extra=2)
    if result is None:
        assert smallest is None or smallest - len(must) > 2
    else:
        assert follows_rules(problem, result.assignment)
        assert len(result.changed) == smallest


# Speed


def big_family(seed, single_loop=False):
    """50 people in households of 2 to 4, avoiding one previous year."""
    rng = Random(seed)
    people = [f"p{i}" for i in range(50)]
    households, i = [], 0
    while i < len(people):
        size = rng.randint(2, 4)
        households.append(people[i : i + size])
        i += size
    last_year = dict(zip(people, rng.sample(people, len(people)), strict=True))
    return households_problem(
        households, excluded=last_year.items(), single_loop=single_loop
    )


@pytest.mark.parametrize("single_loop", [False, True])
def test_fifty_people_draw_quickly(single_loop):
    problem = big_family(7, single_loop)
    started = time.perf_counter()
    result = draw(problem, Random(7))
    elapsed = time.perf_counter() - started
    assert isinstance(result, Draw)
    assert result.exact
    assert follows_rules(problem, result.assignment)
    assert elapsed < 2.0
