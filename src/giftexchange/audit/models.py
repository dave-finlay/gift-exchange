from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    """Sign-ins, invitations, draws, sends, rule changes, withdrawals and
    admin actions. Never records who drew whom."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    action = models.CharField(max_length=50)
    target = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} {self.target}"


def record(action, *, actor=None, target="", ip=None):
    return AuditEvent.objects.create(action=action, actor=actor, target=target, ip=ip)
