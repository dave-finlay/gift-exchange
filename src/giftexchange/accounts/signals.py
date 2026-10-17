from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from giftexchange.audit.models import record


@receiver(user_logged_in)
def record_sign_in(sender, request, user, **kwargs):
    record(request, "signin", actor=user)
