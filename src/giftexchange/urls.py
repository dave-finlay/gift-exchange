from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("giftexchange.accounts.urls")),
    path("", include("giftexchange.exchanges.urls")),
]

# The email provider's reports on each email, only with a secret to check
# them by (see settings).
if settings.ANYMAIL.get("WEBHOOK_SECRET"):
    urlpatterns.append(path("anymail/", include("anymail.urls")))
