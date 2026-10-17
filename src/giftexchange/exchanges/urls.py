from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("circles/new/", views.circle_new, name="circle_new"),
    path("circles/<int:pk>/", views.circle, name="circle"),
    path("circles/<int:pk>/people/new/", views.person_new, name="person_new"),
    path(
        "circles/<int:circle_pk>/people/<int:pk>/",
        views.person_edit,
        name="person_edit",
    ),
    path("circles/<int:pk>/paste/", views.paste, name="paste"),
    path("circles/<int:pk>/rules/new/", views.rule_new, name="rule_new"),
    path(
        "circles/<int:circle_pk>/rules/<int:pk>/delete/",
        views.rule_delete,
        name="rule_delete",
    ),
    path("circles/<int:pk>/exchanges/new/", views.exchange_new, name="exchange_new"),
    path("exchanges/<int:pk>/", views.exchange, name="exchange"),
    path("exchanges/<int:pk>/basics/", views.exchange_basics, name="exchange_basics"),
    path("exchanges/<int:pk>/people/", views.exchange_people, name="exchange_people"),
    path(
        "exchanges/<int:pk>/rules/new/",
        views.exchange_rule_new,
        name="exchange_rule_new",
    ),
    path(
        "exchanges/<int:pk>/rules/<int:rule_pk>/delete/",
        views.exchange_rule_delete,
        name="exchange_rule_delete",
    ),
]
