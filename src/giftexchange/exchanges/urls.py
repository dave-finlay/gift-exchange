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
]
