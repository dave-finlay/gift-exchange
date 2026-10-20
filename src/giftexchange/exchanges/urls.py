from django.urls import path

from . import participant, views

urlpatterns = [
    path("", views.home, name="home"),
    path("circles/new/", views.circle_new, name="circle_new"),
    path("circles/<int:pk>/", views.circle, name="circle"),
    path("circles/<int:pk>/edit/", views.circle_edit, name="circle_edit"),
    path("circles/<int:pk>/delete/", views.circle_delete, name="circle_delete"),
    path("circles/<int:pk>/people/new/", views.person_new, name="person_new"),
    path(
        "circles/<int:circle_pk>/people/<int:pk>/",
        views.person_edit,
        name="person_edit",
    ),
    path(
        "circles/<int:circle_pk>/people/<int:pk>/delete/",
        views.person_delete,
        name="person_delete",
    ),
    path("circles/<int:pk>/paste/", views.paste, name="paste"),
    path("circles/<int:pk>/rules/new/", views.rule_new, name="rule_new"),
    path(
        "circles/<int:circle_pk>/rules/<int:pk>/delete/",
        views.rule_delete,
        name="rule_delete",
    ),
    path("exchanges/new/", views.exchange_new, name="exchange_new"),
    path("exchanges/<int:pk>/", views.exchange, name="exchange"),
    path("exchanges/<int:pk>/circle/", views.exchange_circle, name="exchange_circle"),
    path(
        "exchanges/<int:pk>/circle/new/",
        views.exchange_circle_new,
        name="exchange_circle_new",
    ),
    path("exchanges/<int:pk>/delete/", views.exchange_delete, name="exchange_delete"),
    path("exchanges/<int:pk>/rename/", views.exchange_rename, name="exchange_rename"),
    path(
        "exchanges/<int:pk>/preview/<str:kind>/",
        views.exchange_preview,
        name="exchange_preview",
    ),
    path(
        "exchanges/<int:pk>/send/", views.exchange_send_join, name="exchange_send_join"
    ),
    path("exchanges/<int:pk>/draw/", views.exchange_draw, name="exchange_draw"),
    path(
        "exchanges/<int:pk>/people/<int:participant_pk>/resend/",
        views.exchange_resend,
        name="exchange_resend",
    ),
    # Participants
    path("p/<str:token>/", participant.open_link, name="participant_link"),
    path("you/<int:pk>/", participant.page, name="participant"),
    path("you/<int:pk>/respond/", participant.respond, name="participant_respond"),
    path("you/<int:pk>/reveal/", participant.reveal, name="participant_reveal"),
    path("you/<int:pk>/ideas/", participant.add_idea, name="participant_add_idea"),
    path(
        "you/<int:pk>/ideas/<int:idea_pk>/delete/",
        participant.delete_idea,
        name="participant_delete_idea",
    ),
    path("you/<int:pk>/gift/", participant.gift_status, name="participant_gift_status"),
    path("you/<int:pk>/withdraw/", participant.withdraw, name="participant_withdraw"),
    path("you/<int:pk>/new-link/", participant.new_link, name="participant_new_link"),
    path("exchanges/<int:pk>/save/", views.exchange_save, name="exchange_save"),
    path("exchanges/<int:pk>/end/", views.exchange_end, name="exchange_end"),
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
