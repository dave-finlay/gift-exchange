import pytest

from giftexchange.accounts.models import User
from giftexchange.exchanges.models import Circle, Exchange, Household, Person, Rule


class Build:
    """Builds a circle and an exchange from plain names."""

    def __init__(self, households, **exchange_fields):
        owner = User.objects.create_user("organizer@example.org", "unused")
        self.circle = Circle.objects.create(owner=owner, name="The Smiths")
        self.person = {}
        for i, members in enumerate(households):
            household = None
            if len(members) > 1:
                household = Household.objects.create(circle=self.circle, name=f"H{i}")
            for name in members:
                self.person[name] = Person.objects.create(
                    circle=self.circle,
                    name=name,
                    email=f"{name.lower()}@example.org",
                    household=household,
                )
        self.exchange = self.new_exchange(**exchange_fields)

    def new_exchange(self, name="Christmas", **fields):
        exchange = Exchange.objects.create(
            owner=self.circle.owner,
            circle=self.circle,
            name=name,
            gift_date="2026-12-25",
            **fields,
        )
        for person in self.person.values():
            exchange.participants.create(person=person)
        return exchange

    def participant(self, name, exchange=None):
        exchange = exchange or self.exchange
        return exchange.participants.get(person=self.person[name])

    def rule(self, giver, receiver, kind, exchange=None):
        return Rule.objects.create(
            circle=self.circle,
            exchange=exchange,
            giver=self.person[giver],
            receiver=self.person[receiver],
            kind=kind,
        )


@pytest.fixture
def build(db):
    return Build
