from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from giftexchange.audit.models import record

from .forms import AcceptForm, InviteForm
from .models import Invitation, User


class SignInView(auth_views.LoginView):
    template_name = "accounts/signin.html"
    redirect_authenticated_user = True


class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("home")

    def form_valid(self, form):
        response = super().form_valid(form)
        record(self.request, "password.change")
        messages.success(self.request, "Your password has been changed.")
        return response


def staff_only(view):
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


@staff_only
@never_cache
def invitations(request):
    """Invite someone, and see pending invitations. The new link is shown
    only once, in the response to the form; only its hash is stored."""
    link = None
    form = InviteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        with transaction.atomic():
            Invitation.objects.filter(
                email=email, accepted_at=None, revoked_at=None
            ).update(revoked_at=timezone.now())
            invitation, token = Invitation.issue(email, request.user)
            record(request, "invitation.issue", f"invitation:{invitation.pk}")
        link = request.build_absolute_uri(reverse("accept", args=[token]))
        form = InviteForm()
    pending = Invitation.objects.filter(
        accepted_at=None, revoked_at=None, expires_at__gt=timezone.now()
    ).order_by("-created_at")
    return render(
        request,
        "accounts/invitations.html",
        {
            "form": form,
            "link": link,
            "pending": pending,
            "days": settings.INVITATION_DAYS,
        },
    )


@staff_only
@require_POST
def revoke(request, pk):
    invitation = get_object_or_404(Invitation, pk=pk, accepted_at=None)
    invitation.revoked_at = timezone.now()
    invitation.save(update_fields=["revoked_at"])
    record(request, "invitation.revoke", f"invitation:{pk}")
    messages.success(request, f"The invitation for {invitation.email} is canceled.")
    return redirect("invitations")


@never_cache
def accept(request, token):
    """Opening the link changes nothing (email scanners open links); only
    submitting the form does."""
    invitation = Invitation.usable(token)
    if invitation is None:
        return render(request, "accounts/invitation_unusable.html", status=410)
    form = AcceptForm(request.POST or None, email=invitation.email)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            # Check again inside the transaction, so a double submit can't
            # use the invitation twice.
            invitation = Invitation.usable(token)
            if invitation is None:
                return render(request, "accounts/invitation_unusable.html", status=410)
            if User.objects.filter(email=invitation.email).exists():
                form.add_error(None, "There's already an account for this address.")
                return render(request, "accounts/accept.html", {"form": form})
            user = User.objects.create_user(
                invitation.email,
                form.cleaned_data["password1"],
                display_name=form.cleaned_data["display_name"],
            )
            invitation.accepted_at = timezone.now()
            invitation.save(update_fields=["accepted_at"])
            record(
                request, "invitation.accept", f"invitation:{invitation.pk}", actor=user
            )
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "Welcome! Your account is ready.")
        return redirect("home")
    return render(
        request, "accounts/accept.html", {"form": form, "invitation": invitation}
    )
