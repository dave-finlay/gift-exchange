"""Fair random draws for gift exchanges.

The caller turns the exchange's rules (households, exclusions, recent
pairings) into a directed graph: an arrow G -> R for every pairing they
allow. A draw gives every participant exactly one receiver, so that
everyone also receives exactly once. That's a perfect matching between
givers and receivers that uses only allowed arrows. On top of that, by
default no two people may give to each other ("mutual pairs"), and
optionally the draw must be a single loop through everyone (a
Hamiltonian cycle).

The approach:

1. Feasibility. Maximum bipartite matching (augmenting paths). If there's
   no perfect matching, Hall's theorem gives an explanation: a set of k
   givers who between them can only give to fewer than k people. If the
   structure settings add constraints, a randomised backtracking search
   either finds a valid draw or proves that none exists.
2. A fair draw, by rejection sampling: shuffle the receivers (or, for a
   single loop, the order around the loop) and keep the result if it
   follows every rule. Every valid draw is then exactly equally likely.
   If the rules are so tight that this keeps failing, fall back to a
   Markov chain: start from the draw the search found and randomise it
   with many small random changes that keep it valid. That's only
   approximately uniform, and the result says so.
3. Test mode: many practice draws, counting how often each giver gets
   each receiver.
4. Repairs after someone withdraws or joins late: the smallest change to
   the existing draw that follows every rule.

This module has no Django or database code and no dependencies, so it can
be tested on its own. Participants can be any hashable values.
"""

from __future__ import annotations

import itertools
import secrets
from collections import Counter
from collections.abc import Hashable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from random import Random

# Work budgets. They're counts rather than times, so that results are
# reproducible in tests.
MAX_ATTEMPTS = 200_000
"""Rejection-sampling attempts before falling back to the Markov chain."""

MAX_SEARCH_NODES = 20_000
"""Backtracking steps before the search gives up as undetermined."""

MIX_STEPS_PER_PARTICIPANT = 2_000
"""Markov chain steps per participant when mixing from a starting draw."""

THIN_STEPS_PER_PARTICIPANT = 20
"""Markov chain steps per participant between test-mode samples."""

DEFAULT_SAMPLES = 1_000
"""Practice draws for test mode."""


@dataclass(frozen=True)
class Problem:
    """Everything a draw needs.

    participants: everyone taking part.
    allowed: for each giver, the receivers the rules allow. A giver missing
        from the mapping is allowed nobody.
    required: forced pairings, giver -> receiver. Each must also be allowed.
    allow_mutual: whether A -> B and B -> A may both appear.
    single_loop: whether the draw must be one loop through everyone. When
        it's set, allow_mutual has no effect.
    """

    participants: tuple[Hashable, ...]
    allowed: Mapping[Hashable, Iterable[Hashable]]
    required: Mapping[Hashable, Hashable] = field(default_factory=dict)
    allow_mutual: bool = False
    single_loop: bool = False

    def __post_init__(self) -> None:
        people = set(self.participants)
        if len(people) != len(self.participants):
            raise ValueError("participants must be unique")
        for giver, receivers in self.allowed.items():
            receivers = set(receivers)
            if giver not in people:
                raise ValueError(f"unknown giver in allowed: {giver!r}")
            if not receivers <= people:
                raise ValueError(f"unknown receiver allowed for {giver!r}")
            if giver in receivers:
                raise ValueError(f"{giver!r} is allowed to give to themselves")
        taken = set()
        for giver, receiver in self.required.items():
            if giver not in people or receiver not in people:
                raise ValueError(f"unknown participant in {giver!r} -> {receiver!r}")
            if receiver not in set(self.allowed.get(giver, ())):
                raise ValueError(
                    f"required pairing {giver!r} -> {receiver!r} isn't allowed"
                )
            if receiver in taken:
                raise ValueError(f"{receiver!r} is required for more than one giver")
            taken.add(receiver)
            mutual_banned = not self.allow_mutual and not self.single_loop
            if mutual_banned and self.required.get(receiver) == giver:
                raise ValueError(
                    f"required pairings {giver!r} and {receiver!r} are mutual"
                )


@dataclass(frozen=True)
class Draw:
    """A valid draw.

    assignment: giver -> receiver, for every participant.
    exact: True if every valid draw was equally likely (rejection
        sampling); False if only approximately so (Markov chain fallback).
    """

    assignment: dict[Hashable, Hashable]
    exact: bool


@dataclass(frozen=True)
class Infeasible:
    """No valid draw exists.

    reason:
        "hall": the people in `givers` can, between them, only give to the
            people in `receivers`, and there are fewer of those.
        "mutual": every possible draw has two people giving to each other.
        "loop": no single loop through everyone is possible.
    """

    reason: str
    givers: frozenset[Hashable] = frozenset()
    receivers: frozenset[Hashable] = frozenset()


@dataclass(frozen=True)
class Undetermined:
    """The search ran out of budget before finding a draw or proving that
    none exists. `reason` is "mutual" or "loop", as for Infeasible."""

    reason: str


@dataclass(frozen=True)
class Predictability:
    """Test-mode statistics.

    counts: giver -> Counter of receiver -> number of practice draws.
    samples: the number of practice draws.
    exact: False if some draws came from the Markov chain fallback.
    """

    counts: dict[Hashable, Counter[Hashable]]
    samples: int
    exact: bool


@dataclass(frozen=True)
class Repair:
    """A repaired draw.

    assignment: giver -> receiver, for every participant.
    changed: givers from the previous draw whose receiver changed. They
        need a new assignment email.
    """

    assignment: dict[Hashable, Hashable]
    changed: frozenset[Hashable]


def draw(
    problem: Problem,
    rng: Random | None = None,
    *,
    max_attempts: int = MAX_ATTEMPTS,
    max_nodes: int = MAX_SEARCH_NODES,
) -> Draw | Infeasible | Undetermined:
    """Make a fair random draw, or explain why none is possible."""
    rng = rng or secrets.SystemRandom()
    graph = _Graph(problem)
    start, failure = _prepare(graph, rng, max_nodes)
    if isinstance(failure, Infeasible):
        return failure
    samples = _samples(graph, start, rng, max_attempts)
    assignment, exact = next(samples, (None, False))
    if assignment is None:
        return failure
    return Draw(graph.to_ids(assignment), exact)


def predictability(
    problem: Problem,
    rng: Random | None = None,
    *,
    samples: int = DEFAULT_SAMPLES,
    max_attempts: int = MAX_ATTEMPTS,
    max_nodes: int = MAX_SEARCH_NODES,
) -> Predictability | Infeasible | Undetermined:
    """Run practice draws and count how often each giver gets each receiver.

    Nothing here is a real draw; the caller must not store the results.
    """
    rng = rng or secrets.SystemRandom()
    graph = _Graph(problem)
    start, failure = _prepare(graph, rng, max_nodes)
    if isinstance(failure, Infeasible):
        return failure
    stream = _samples(graph, start, rng, max_attempts)
    counts: dict[Hashable, Counter[Hashable]] = {p: Counter() for p in graph.ids}
    exact = True
    for _ in range(samples):
        assignment, sample_exact = next(stream, (None, False))
        if assignment is None:
            return failure
        exact = exact and sample_exact
        for giver, receiver in enumerate(assignment):
            counts[graph.ids[giver]][graph.ids[receiver]] += 1
    return Predictability(counts, samples, exact)


def repair(
    problem: Problem,
    current: Mapping[Hashable, Hashable],
    rng: Random | None = None,
    *,
    max_extra: int = 2,
) -> Repair | None:
    """Find the smallest valid change to a draw after people withdraw or join.

    `problem` describes the exchange after the change. `current` is the draw
    before it. Givers with no usable receiver (the giver of someone who
    withdrew, or someone who joined late) must get a new one. The search
    tries changing no other giver, then 1, up to `max_extra`, and picks at
    random among the smallest valid changes. Returns None if nothing that
    small works, in which case the caller should offer a full redraw.
    """
    rng = rng or secrets.SystemRandom()
    graph = _Graph(problem)
    index = graph.index
    kept_before = {
        index[giver]: index[receiver]
        for giver, receiver in current.items()
        if giver in index and receiver in index
    }
    must_change = [u for u in range(graph.n) if u not in kept_before]
    others = list(kept_before)
    for extra in range(max_extra + 1):
        found = []
        for chosen in itertools.combinations(others, extra):
            changing = must_change + list(chosen)
            assignment = [-1] * graph.n
            for giver, receiver in kept_before.items():
                if giver not in chosen:
                    assignment[giver] = receiver
            pool = sorted(set(range(graph.n)) - set(assignment))
            for receivers in itertools.permutations(pool):
                for giver, receiver in zip(changing, receivers, strict=True):
                    assignment[giver] = receiver
                if graph.is_valid(assignment):
                    found.append(assignment.copy())
        if found:
            result = graph.to_ids(rng.choice(found))
            changed = frozenset(
                giver
                for giver, receiver in current.items()
                if giver in result and result[giver] != receiver
            )
            return Repair(result, changed)
    return None


def is_valid(problem: Problem, assignment: Mapping[Hashable, Hashable]) -> bool:
    """Whether `assignment` is a valid draw for `problem`. The caller checks
    this again before storing a draw."""
    graph = _Graph(problem)
    if set(assignment) != set(graph.ids) or not set(assignment.values()) <= set(
        graph.ids
    ):
        return False
    return graph.is_valid([graph.index[assignment[p]] for p in graph.ids])


class _Graph:
    """The problem with participants numbered 0..n-1, which keeps the inner
    loops fast and the code simple."""

    def __init__(self, problem: Problem):
        self.ids = list(problem.participants)
        self.index = {p: i for i, p in enumerate(self.ids)}
        self.n = len(self.ids)
        self.required = {
            self.index[g]: self.index[r] for g, r in problem.required.items()
        }
        taken = set(self.required.values())
        self.out: list[frozenset[int]] = []
        for i, p in enumerate(self.ids):
            if i in self.required:
                self.out.append(frozenset({self.required[i]}))
            else:
                allowed = {self.index[r] for r in problem.allowed.get(p, ())}
                self.out.append(frozenset(allowed - taken))
        self.single_loop = problem.single_loop
        self.no_mutual = not problem.allow_mutual and not problem.single_loop

    def to_ids(self, assignment: list[int]) -> dict[Hashable, Hashable]:
        return {self.ids[g]: self.ids[r] for g, r in enumerate(assignment)}

    def is_valid(self, assignment: list[int]) -> bool:
        if sorted(assignment) != list(range(self.n)):
            return False
        if any(r not in self.out[g] for g, r in enumerate(assignment)):
            return False
        if self.no_mutual and any(assignment[r] == g for g, r in enumerate(assignment)):
            return False
        return not (
            self.single_loop and self.n and _loop_length(assignment, 0) != self.n
        )


# A starting point for sampling: a valid draw as a receiver list, or for a
# single loop, the chains of forced pairings in a valid order around the loop.
_Start = list[int] | list[list[int]]


def _prepare(
    graph: _Graph, rng: Random, max_nodes: int
) -> tuple[_Start | None, Infeasible | Undetermined | None]:
    """Check feasibility and find one valid draw to start from."""
    if graph.n == 0:
        return [], None
    matching, violator = _match(range(graph.n), graph.out)
    if violator is not None:
        givers, receivers = violator
        return None, Infeasible(
            "hall",
            frozenset(graph.ids[g] for g in givers),
            frozenset(graph.ids[r] for r in receivers),
        )
    if graph.single_loop:
        chains = _chains(graph)
        if chains is None:
            return None, Infeasible("loop")
        return _search_loop(graph, chains, rng, max_nodes)
    assignment = [matching[u] for u in range(graph.n)]
    if graph.is_valid(assignment):
        return assignment, None
    return _search_matching(graph, rng, max_nodes)


def _samples(
    graph: _Graph, start: _Start | None, rng: Random, max_attempts: int
) -> Iterator[tuple[list[int], bool]]:
    """An endless stream of valid draws as (assignment, exact).

    Rejection sampling while it works, then the Markov chain from `start`.
    If rejection sampling fails and there's no start, the stream ends.
    """
    if graph.n == 0:
        while True:
            yield [], True
    if graph.single_loop:
        chains = _chains(graph)
        assert chains is not None
        while (order := _sample_loop(graph, chains, rng, max_attempts)) is not None:
            yield _loop_assignment(graph.n, order), True
        if start is None:
            return
        order = _mix_loop(graph, start, rng, MIX_STEPS_PER_PARTICIPANT * graph.n)
        while True:
            yield _loop_assignment(graph.n, order), False
            order = _mix_loop(graph, order, rng, THIN_STEPS_PER_PARTICIPANT * graph.n)
    else:
        while (assignment := _sample_matching(graph, rng, max_attempts)) is not None:
            yield assignment, True
        if start is None:
            return
        assignment = _mix_matching(
            graph, start, rng, MIX_STEPS_PER_PARTICIPANT * graph.n
        )
        while True:
            yield assignment, False
            assignment = _mix_matching(
                graph, assignment, rng, THIN_STEPS_PER_PARTICIPANT * graph.n
            )


def _match(
    givers: Iterable[int], out: list[frozenset[int]], available: set[int] | None = None
) -> tuple[dict[int, int] | None, tuple[set[int], set[int]] | None]:
    """Maximum bipartite matching by augmenting paths (Kuhn's algorithm).

    Returns (giver -> receiver, None) if every giver can be matched to a
    distinct receiver from `available` (default: anyone). Otherwise returns
    (None, (S, N)): a set S of givers who between them can only give to the
    receivers N, where len(N) < len(S). That's the Hall's theorem
    explanation. The search from an unmatched giver reaches S and N; every
    receiver it reaches is already matched to another giver in S.
    """
    giver_of: dict[int, int] = {}

    def augment(u: int, seen: set[int]) -> bool:
        for r in out[u]:
            if r in seen or (available is not None and r not in available):
                continue
            seen.add(r)
            if r not in giver_of or augment(giver_of[r], seen):
                giver_of[r] = u
                return True
        return False

    for u in givers:
        seen: set[int] = set()
        if not augment(u, seen):
            return None, ({u} | {giver_of[r] for r in seen}, seen)
    return {u: r for r, u in giver_of.items()}, None


def _sample_matching(graph: _Graph, rng: Random, max_attempts: int) -> list[int] | None:
    """Rejection sampling: a uniformly random valid draw, or None.

    Each attempt is a Fisher-Yates shuffle of the free receivers, checked
    pairing by pairing as it goes. Stopping an attempt as soon as one
    pairing breaks a rule doesn't affect fairness: every complete shuffle
    is equally likely, and the accepted ones are exactly the valid draws.
    """
    free_givers = [u for u in range(graph.n) if u not in graph.required]
    taken = set(graph.required.values())
    free_receivers = [r for r in range(graph.n) if r not in taken]
    base = [-1] * graph.n
    for u, r in graph.required.items():
        base[u] = r
    m = len(free_givers)
    for _ in range(max_attempts):
        assignment = base.copy()
        receivers = free_receivers.copy()
        for i, u in enumerate(free_givers):
            j = rng.randrange(i, m)
            receivers[i], receivers[j] = receivers[j], receivers[i]
            r = receivers[i]
            if r not in graph.out[u] or (graph.no_mutual and assignment[r] == u):
                break
            assignment[u] = r
        else:
            return assignment
    return None


def _search_matching(
    graph: _Graph, rng: Random, max_nodes: int
) -> tuple[list[int] | None, Infeasible | Undetermined | None]:
    """Backtracking search for a draw with no mutual pairs.

    It takes the giver with the fewest options first and tries their
    options in random order. After each choice it checks that the
    remaining givers can still be matched (ignoring the mutual rule), which
    prunes most dead ends. Exhausting the search proves that no valid draw
    exists.
    """
    assignment = [-1] * graph.n
    for u, r in graph.required.items():
        assignment[u] = r
    used = set(graph.required.values())
    todo = {u for u in range(graph.n) if u not in graph.required}
    nodes = 0

    def options(u: int) -> list[int]:
        return [
            r
            for r in graph.out[u]
            if r not in used and not (graph.no_mutual and assignment[r] == u)
        ]

    def solve() -> bool:
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes:
            raise _OutOfBudget
        if not todo:
            return True
        u = min(todo, key=lambda v: len(options(v)))
        choices = options(u)
        rng.shuffle(choices)
        todo.remove(u)
        for r in choices:
            assignment[u] = r
            used.add(r)
            available = set(range(graph.n)) - used
            if _match(todo, graph.out, available)[0] is not None and solve():
                return True
            used.remove(r)
            assignment[u] = -1
        todo.add(u)
        return False

    try:
        if solve():
            return assignment, None
        return None, Infeasible("mutual")
    except _OutOfBudget:
        return None, Undetermined("mutual")


def _mix_matching(
    graph: _Graph, assignment: list[int], rng: Random, steps: int
) -> list[int]:
    """Markov chain fallback: randomise a valid draw with small changes.

    Each step proposes swapping the receivers of two random givers, or
    rotating the receivers of three, and keeps the change only if the draw
    stays valid. Every proposal is as likely as its reverse, so the chain's
    long-run distribution is uniform over the draws it can reach.
    """
    free = [u for u in range(graph.n) if u not in graph.required]
    assignment = assignment.copy()
    if len(free) < 2:
        return assignment
    for _ in range(steps):
        if len(free) >= 3 and rng.random() < 0.5:
            u, v, w = rng.sample(free, 3)
            proposal = {u: assignment[v], v: assignment[w], w: assignment[u]}
        else:
            u, v = rng.sample(free, 2)
            proposal = {u: assignment[v], v: assignment[u]}
        if any(r not in graph.out[g] for g, r in proposal.items()):
            continue
        previous = {g: assignment[g] for g in proposal}
        for g, r in proposal.items():
            assignment[g] = r
        if graph.no_mutual and any(assignment[assignment[g]] == g for g in proposal):
            for g, r in previous.items():
                assignment[g] = r
    return assignment


def _chains(graph: _Graph) -> list[list[int]] | None:
    """Split participants into chains of forced pairings, for a single loop.

    Someone with no forced pairing is a chain of one. Returns None if the
    forced pairings close a loop that doesn't include everyone.
    """
    receivers = set(graph.required.values())
    chains = []
    for head in range(graph.n):
        if head in receivers:
            continue
        chain = [head]
        while chain[-1] in graph.required:
            chain.append(graph.required[chain[-1]])
        chains.append(chain)
    if sum(len(chain) for chain in chains) == graph.n:
        return chains
    if not chains:
        # Every pairing is forced. That's fine if it's one loop through
        # everyone; represent it as a single chain starting anywhere.
        loop = [0]
        while len(loop) < graph.n and graph.required[loop[-1]] != 0:
            loop.append(graph.required[loop[-1]])
        if len(loop) == graph.n:
            return [loop]
    return None


def _sample_loop(
    graph: _Graph, chains: list[list[int]], rng: Random, max_attempts: int
) -> list[list[int]] | None:
    """Rejection sampling for a single loop: shuffle the order of the chains
    around the loop (keeping the first one first, since a loop has no
    start), checking each join as it goes. Every valid loop corresponds to
    exactly one such order, so each is equally likely."""
    first, rest = chains[0], chains[1:]
    k = len(rest)
    for _ in range(max_attempts):
        order = rest.copy()
        tail = first[-1]
        for i in range(k):
            j = rng.randrange(i, k)
            order[i], order[j] = order[j], order[i]
            if order[i][0] not in graph.out[tail]:
                break
            tail = order[i][-1]
        else:
            if first[0] in graph.out[tail]:
                return [first, *order]
    return None


def _search_loop(
    graph: _Graph, chains: list[list[int]], rng: Random, max_nodes: int
) -> tuple[list[list[int]] | None, Infeasible | Undetermined | None]:
    """Backtracking search for a single loop (a Hamiltonian cycle).

    It builds the loop chain by chain from the first one. Each step tries
    the chains with the fewest onward options first (Warnsdorff's rule),
    and gives up on a branch as soon as some remaining chain has no
    possible neighbour on either side.
    """
    heads = [chain[0] for chain in chains]
    tails = [chain[-1] for chain in chains]
    remaining = set(range(1, len(chains)))
    order = [0]
    nodes = 0

    def joins(a: int, b: int) -> bool:
        return heads[b] in graph.out[tails[a]]

    def solve() -> bool:
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes:
            raise _OutOfBudget
        last = order[-1]
        if not remaining:
            return joins(last, 0)
        for c in remaining:
            before = [last, *(d for d in remaining if d != c)]
            after = [0, *(d for d in remaining if d != c)]
            if not any(joins(d, c) for d in before):
                return False
            if not any(joins(c, d) for d in after):
                return False
        choices = [c for c in remaining if joins(last, c)]
        rng.shuffle(choices)
        choices.sort(key=lambda c: sum(joins(c, d) for d in remaining if d != c))
        for c in choices:
            order.append(c)
            remaining.remove(c)
            if solve():
                return True
            remaining.add(c)
            order.pop()
        return False

    try:
        if solve():
            return [chains[c] for c in order], None
        return None, Infeasible("loop")
    except _OutOfBudget:
        return None, Undetermined("loop")


def _mix_loop(
    graph: _Graph, order: list[list[int]], rng: Random, steps: int
) -> list[list[int]]:
    """Markov chain fallback for a single loop: swap two chains' positions
    around the loop (never the first), keeping the change only if every
    join stays allowed. As with _mix_matching, the long-run distribution is
    uniform over the loops the chain can reach."""
    order = order.copy()
    k = len(order)
    if k < 3:
        return order

    def joins_at(i: int) -> bool:
        return order[(i + 1) % k][0] in graph.out[order[i][-1]]

    for _ in range(steps):
        i, j = rng.sample(range(1, k), 2)
        order[i], order[j] = order[j], order[i]
        affected = {i - 1, i, j - 1, j}
        if not all(joins_at(p % k) for p in affected):
            order[i], order[j] = order[j], order[i]
    return order


def _loop_assignment(n: int, order: list[list[int]]) -> list[int]:
    sequence = [u for chain in order for u in chain]
    assignment = [-1] * n
    for i, u in enumerate(sequence):
        assignment[u] = sequence[(i + 1) % n]
    return assignment


def _loop_length(assignment: list[int], start: int) -> int:
    length, u = 1, assignment[start]
    while u != start:
        u = assignment[u]
        length += 1
    return length


class _OutOfBudget(Exception):
    pass
