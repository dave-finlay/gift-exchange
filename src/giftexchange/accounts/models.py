from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from django.db import models
from django.utils import timezone

from giftexchange.tokens import hash_token, new_token


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **fields):
        if not email:
            raise ValueError("An email address is required")
        user = self.model(email=self.normalize_email(email), **fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def get_by_natural_key(self, email):
        return self.get(email=email.lower())

    def create_superuser(self, email, password=None, **fields):
        fields.update(is_staff=True, is_superuser=True)
        return self.create_user(email, password, **fields)


class User(AbstractBaseUser, PermissionsMixin):
    """An organizer. Participants don't have accounts."""

    # Stored in lower case, so it's unique regardless of case.
    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"

    def __str__(self):
        return self.display_name or self.email

    def save(self, *args, **kwargs):
        self.email = self.email.lower()
        super().save(*args, **kwargs)

    @classmethod
    def normalize_username(cls, username):
        return super().normalize_username(username).lower()


class Invitation(models.Model):
    """A single-use link that lets someone create an organizer account."""

    email = models.EmailField()
    token_hash = models.CharField(max_length=64, unique=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.email

    @classmethod
    def issue(cls, email, invited_by):
        """A new invitation and the token for its link."""
        token, token_hash = new_token()
        invitation = cls.objects.create(
            email=email,
            token_hash=token_hash,
            invited_by=invited_by,
            expires_at=timezone.now() + timedelta(days=settings.INVITATION_DAYS),
        )
        return invitation, token

    @classmethod
    def usable(cls, token):
        """The invitation for a token, if it can still be accepted."""
        return cls.objects.filter(
            token_hash=hash_token(token),
            accepted_at=None,
            revoked_at=None,
            expires_at__gt=timezone.now(),
        ).first()
