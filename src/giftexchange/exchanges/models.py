"""The data model. See "Data model" in docs/DESIGN.md."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.db import models
from django.utils import timezone


class Circle(models.Model):
    """A lasting group, e.g. "the Smith family"."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="circles", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner", "name"], name="unique_circle"),
        ]

    def __str__(self):
        return self.name


class Household(models.Model):
    """People who never give to each other, in either direction."""

    circle = models.ForeignKey(
        Circle, related_name="households", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["circle", "name"], name="unique_household"),
        ]

    def __str__(self):
        return self.name


class Person(models.Model):
    circle = models.ForeignKey(Circle, related_name="people", on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    # Not unique: a child can share a parent's address.
    email = models.EmailField()
    household = models.ForeignKey(
        Household,
        null=True,
        blank=True,
        related_name="members",
        on_delete=models.SET_NULL,
    )

    class Meta:
        verbose_name_plural = "people"
        constraints = [
            models.UniqueConstraint(fields=["circle", "name"], name="unique_person"),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.household and self.household.circle_id != self.circle_id:
            raise ValidationError("The household belongs to a different circle.")


class Exchange(models.Model):
    """One gift exchange, e.g. "Christmas 2026": the main thing organizers
    create. A draft may not have a circle or a gift date yet."""

    class Status(models.TextChoices):
        DRAFT = "draft"
        JOINING = "joining"
        SENT = "sent"
        ARCHIVED = "archived"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="exchanges", on_delete=models.CASCADE
    )
    # The people it's for. Deleting a circle leaves a draft without one.
    circle = models.ForeignKey(
        Circle,
        null=True,
        blank=True,
        related_name="exchanges",
        on_delete=models.SET_NULL,
    )
    name = models.CharField(max_length=100)
    gift_date = models.DateField(null=True, blank=True)
    draw_date = models.DateField(null=True, blank=True)
    budget = models.CharField(max_length=50, blank=True)
    note = models.TextField(blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT
    )
    confirm_first = models.BooleanField(default=True)
    # How many of the circle's previous exchanges to avoid repeating
    # pairings from.
    avoid_repeats = models.PositiveSmallIntegerField(
        default=1, validators=[MaxValueValidator(5)]
    )
    allow_mutual = models.BooleanField(default=False)
    single_loop = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    drawn_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name


class Rule(models.Model):
    """One direction of an exclusion or forced pairing. A standing rule has
    no exchange and applies to every exchange in the circle."""

    class Kind(models.TextChoices):
        EXCLUDE = "exclude"
        REQUIRE = "require"

    circle = models.ForeignKey(Circle, related_name="rules", on_delete=models.CASCADE)
    exchange = models.ForeignKey(
        Exchange,
        null=True,
        blank=True,
        related_name="rules",
        on_delete=models.CASCADE,
    )
    giver = models.ForeignKey(Person, related_name="+", on_delete=models.CASCADE)
    receiver = models.ForeignKey(Person, related_name="+", on_delete=models.CASCADE)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    # Private to the organizer, e.g. the reason for a forced pairing.
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(giver=models.F("receiver")),
                name="rule_not_self",
            ),
            models.UniqueConstraint(
                fields=["giver", "receiver"],
                condition=models.Q(exchange=None),
                name="unique_standing_rule",
            ),
            models.UniqueConstraint(
                fields=["exchange", "giver", "receiver"],
                name="unique_exchange_rule",
            ),
        ]

    def __str__(self):
        arrow = "→" if self.kind == self.Kind.REQUIRE else "↛"
        return f"{self.giver} {arrow} {self.receiver}"

    def clean(self):
        circles = {self.giver.circle_id, self.receiver.circle_id}
        if self.exchange:
            circles.add(self.exchange.circle_id)
        if circles != {self.circle_id}:
            raise ValidationError("Everyone in a rule must be in the same circle.")


class ParticipantQuerySet(models.QuerySet):
    def in_draw(self):
        """The participants a draw includes: not withdrawn, and not declined.
        Before drawing, the organizer declines anyone who hasn't responded
        and shouldn't be included."""
        return self.filter(withdrawn_at=None).exclude(
            response=Participant.Response.DECLINED
        )


class Participant(models.Model):
    """Someone taking part in one exchange. Their private link points here."""

    class Response(models.TextChoices):
        PENDING = "pending"
        IN = "in"
        DECLINED = "declined"

    class GiftStatus(models.TextChoices):
        NOT_YET = "not_yet", "Not yet"
        BOUGHT = "bought"
        SENT = "sent"

    exchange = models.ForeignKey(
        Exchange, related_name="participants", on_delete=models.CASCADE
    )
    person = models.ForeignKey(
        Person, related_name="participations", on_delete=models.CASCADE
    )
    # Changing this invalidates the participant's old private links; see
    # links.py.
    link_rotated_at = models.DateTimeField(null=True, blank=True)
    response = models.CharField(
        max_length=10, choices=Response.choices, default=Response.PENDING
    )
    responded_at = models.DateTimeField(null=True, blank=True)
    gift_status = models.CharField(
        max_length=10, choices=GiftStatus.choices, default=GiftStatus.NOT_YET
    )
    gift_status_at = models.DateTimeField(null=True, blank=True)
    revealed_at = models.DateTimeField(null=True, blank=True)
    withdraw_requested_at = models.DateTimeField(null=True, blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ParticipantQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["exchange", "person"], name="unique_participant"
            ),
        ]

    def __str__(self):
        return str(self.person)

    def clean(self):
        if self.person.circle_id != self.exchange.circle_id:
            raise ValidationError("The person belongs to a different circle.")


class GiftIdea(models.Model):
    """An idea for yourself, seen only by your giver."""

    MAX_PER_PARTICIPANT = 10

    participant = models.ForeignKey(
        Participant, related_name="gift_ideas", on_delete=models.CASCADE
    )
    text = models.CharField(max_length=280)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.text


class Assignment(models.Model):
    """Who a participant gives to. Only the receiver is encrypted; see
    secrecy.py, the only module that reads it."""

    giver = models.OneToOneField(
        Participant, related_name="assignment", on_delete=models.CASCADE
    )
    receiver_ciphertext = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Assignment for {self.giver}"


class EmailLog(models.Model):
    """One email sent to a participant. Never stores the body."""

    class Kind(models.TextChoices):
        JOIN = "join"
        ASSIGNMENT = "assignment"
        REASSIGNMENT = "reassignment"
        REMOVED = "removed"
        REMINDER = "reminder"
        CALLED_OFF = "called_off"

    class Status(models.TextChoices):
        SENT = "sent"
        DELIVERED = "delivered"
        BOUNCED = "bounced"
        COMPLAINT = "complaint"
        FAILED = "failed"  # The provider refused it

    participant = models.ForeignKey(
        Participant, related_name="emails", on_delete=models.CASCADE
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    sent_at = models.DateTimeField(default=timezone.now)
    provider_message_id = models.CharField(max_length=200, blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.SENT
    )

    def __str__(self):
        return f"{self.kind} to {self.participant}"
