"""Test mode: "Check the rules". It runs practice draws and saves nothing.

The result is in names, for the organizer. By default the page shows only
counts; names appear only when the organizer asks for details, because
they can hint at likely assignments, and at pairings from recent
exchanges (which the rules avoid).
"""

from dataclasses import dataclass, field

from giftexchange import matching

from . import draws

# A giver is "very predictable" if one receiver comes up in at least this
# share of practice draws.
PREDICTABLE = 0.75

SAMPLES = 1_000


@dataclass
class Predictable:
    giver: str
    receiver: str
    share: float

    @property
    def only(self):
        return self.share == 1


@dataclass
class Check:
    participants: int
    problems: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    predictable: list[Predictable] = field(default_factory=list)
    exact: bool = True

    @property
    def ok(self):
        return not self.problems


def names(people):
    """ "Alice", "Alice and Bob", "Alice, Bob and Carol"."""
    people = sorted(people)
    if len(people) <= 1:
        return "".join(people)
    return f"{', '.join(people[:-1])} and {people[-1]}"


def check(exchange, rng=None, samples=SAMPLES):
    participants = exchange.participants.in_draw().select_related("person")
    name = {p.pk: p.person.name for p in participants}
    result = Check(participants=len(name))
    if len(name) < 2:
        result.problems.append("An exchange needs at least 2 people.")
        return result
    try:
        problem = draws.build_problem(exchange)
    except draws.RuleConflict as conflict:
        result.problems.extend(f"{message}." for message in conflict.messages)
        return result

    stats = matching.predictability(problem, rng, samples=samples)
    if isinstance(stats, matching.Infeasible):
        result.problems.append(explain(stats, name))
        result.hints.extend(hints(exchange, stats.reason))
        return result
    if isinstance(stats, matching.Undetermined):
        result.problems.append(
            "No draw was found quickly enough, so there may not be one."
        )
        result.hints.extend(hints(exchange, stats.reason))
        return result

    result.exact = stats.exact
    for giver, receivers in stats.counts.items():
        if giver in problem.required:
            continue  # A forced pairing is predictable on purpose.
        receiver, count = receivers.most_common(1)[0]
        share = count / stats.samples
        if share >= PREDICTABLE:
            result.predictable.append(Predictable(name[giver], name[receiver], share))
    result.predictable.sort(key=lambda p: (-p.share, p.giver))
    return result


def explain(infeasible, name):
    if infeasible.reason == "hall":
        givers = names(name[g] for g in infeasible.givers)
        if not infeasible.receivers:
            return f"With these rules, {givers} can't give to anyone."
        receivers = names(name[r] for r in infeasible.receivers)
        return (
            f"With these rules, {givers} can only give to {receivers}: "
            "not enough people to go round."
        )
    if infeasible.reason == "mutual":
        return "Every possible draw has two people giving to each other."
    return "No single loop through everyone is possible with these rules."


def hints(exchange, reason):
    hints = ["Remove a rule, or change who's in which household."]
    if reason == "hall":
        hints.append("Add more people.")
    if exchange.avoid_repeats and draws.recent_pairings(exchange):
        hints.append("Avoid pairings from fewer previous exchanges.")
    if reason == "mutual":
        hints.append("Allow mutual pairs.")
    if reason == "loop":
        hints.append("Turn off the single loop.")
    return hints
