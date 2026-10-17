from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("giftexchange.accounts.urls")),
    path("", include("giftexchange.exchanges.urls")),
]
