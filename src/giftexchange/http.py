"""Request and response helpers shared by every page."""

from django.conf import settings

# Nothing third-party, and no inline scripts. Pages work without
# JavaScript; the site's own small scripts only add conveniences, such as
# swiping between pages.
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)
# The admin site needs its own scripts.
ADMIN_CSP = (
    "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
)


def security_headers(get_response):
    def middleware(request):
        response = get_response(request)
        admin = request.path.startswith("/admin/")
        response.headers.setdefault(
            "Content-Security-Policy", ADMIN_CSP if admin else CSP
        )
        # Invite-only: nothing here belongs in search engines.
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        return response

    return middleware


def client_ip(request):
    if settings.TRUST_X_FORWARDED_FOR:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.META.get("REMOTE_ADDR")


def site(request):
    """Template context: the site's name and operator."""
    return {
        "SITE_NAME": settings.SITE_NAME,
        "OPERATOR_NAME": settings.OPERATOR_NAME,
        "OPERATOR_EMAIL": settings.OPERATOR_EMAIL,
    }
