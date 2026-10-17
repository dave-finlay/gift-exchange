"""From an exchange's rules to a matching problem, and saving draws.

matching.py works on any IDs; here they're Participant IDs.
"""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from giftexchange import matching

from . import secrecy
from .models import Assignment, Exchange, Participant, Rule


class RuleConflict(Exception):
    """Forced pairings that can't all be kept. One message per problem."""

    def __init__(self, messages):
        super().__init__("; ".join(messages))
        self.messages = messages


def recent_pairings(exchange):
    """(giver, receiver) Person IDs from the circle's last `avoid_repeats`
    drawn exchanges. Never shown to anyone."""
    if not exchange.avoid_repeats:
        return set()
    previous = list(
        Exchange.objects.filter(circle_id=exchange.circle_id, drawn_at__isnull=False)
        .exclude(pk=exchange.pk)
        .order_by("-drawn_at")
        .values_list("pk", flat=True)[: exchange.avoid_repeats]
    )
    person_of = dict(
        Participant.objects.filter(exchange__in=previous).values_list("pk", "person_id")
    )
    assignments = Assignment.objects.filter(
        giver__exchange__in=previous
    ).select_related("giver")
    return {(a.giver.person_id, person_of[secrecy.receiver_id(a)]) for a in assignments}


def build_problem(exchange):
    """The matching problem for an exchange's participants and rules.

    A forced pairing wins over avoiding recent pairings: otherwise a
    rejected forced pairing would tell the organizer who gave to whom last
    time. Forced pairings that involve someone who isn't taking part are
    ignored. Raises RuleConflict if forced pairings break other rules.
    """
    participants = list(exchange.participants.in_draw().select_related("person"))
    by_person = {p.person_id: p for p in participants}
    name = {p.person_id: p.person.name for p in participants}
    household = {p.person_id: p.person.household_id for p in participants}

    rules = Rule.objects.filter(circle_id=exchange.circle_id).filter(
        Q(exchange=None) | Q(exchange=exchange)
    )
    excluded, required = set(), {}
    conflicts = []
    for rule in rules:
        pair = (rule.giver_id, rule.receiver_id)
        if not set(pair) <= by_person.keys():
            continue
        if rule.kind == Rule.Kind.EXCLUDE:
            excluded.add(pair)
            continue
        giver, receiver = pair
        if giver in required and required[giver] != receiver:
            conflicts.append(f"{name[giver]} is forced to give to two people")
        elif receiver in required.values() and required.get(giver) != receiver:
            conflicts.append(f"{name[receiver]} is forced to receive from two people")
        required[giver] = receiver

    for giver, receiver in required.items():
        if household[giver] is not None and household[giver] == household[receiver]:
            conflicts.append(
                f"{name[giver]} is forced to give to {name[receiver]}, "
                "but they're in the same household"
            )
        if (giver, receiver) in excluded:
            conflicts.append(
                f"{name[giver]} giving to {name[receiver]} is both forced and excluded"
            )
        mutual_banned = not exchange.allow_mutual and not exchange.single_loop
        if mutual_banned and required.get(receiver) == giver and giver < receiver:
            conflicts.append(
                f"{name[giver]} and {name[receiver]} are forced to give to each "
                "other, but mutual pairs are off"
            )
    if conflicts:
        raise RuleConflict(conflicts)

    recent = recent_pairings(exchange) - set(required.items())
    allowed = {
        by_person[g].pk: frozenset(
            by_person[r].pk
            for r in by_person
            if r != g
            and (household[g] is None or household[g] != household[r])
            and (g, r) not in excluded
            and (g, r) not in recent
        )
        for g in by_person
    }
    return matching.Problem(
        participants=tuple(p.pk for p in participants),
        allowed=allowed,
        required={by_person[g].pk: by_person[r].pk for g, r in required.items()},
        allow_mutual=exchange.allow_mutual,
        single_loop=exchange.single_loop,
    )


def draw(exchange, rng=None):
    """Draw an exchange and save the encrypted assignments, all in one
    transaction. Returns matching's result: a Draw, or why there isn't one.
    Once drawn, an exchange can't be drawn again; changes after that go
    through repairs."""
    with transaction.atomic():
        exchange = Exchange.objects.get(pk=exchange.pk)
        if exchange.drawn_at:
            raise ValueError(f"{exchange} has already been drawn")
        problem = build_problem(exchange)
        result = matching.draw(problem, rng)
        if not isinstance(result, matching.Draw):
            return result
        if not matching.is_valid(problem, result.assignment):
            raise AssertionError("The draw broke the rules")
        participants = Participant.objects.in_bulk(problem.participants)
        Assignment.objects.bulk_create(
            Assignment(
                giver=participants[g],
                receiver_ciphertext=secrecy.seal(participants[g], participants[r]),
            )
            for g, r in result.assignment.items()
        )
        exchange.drawn_at = timezone.now()
        exchange.save(update_fields=["drawn_at"])
        return result
