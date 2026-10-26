from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("signin/", views.SignInView.as_view(), name="signin"),
    path("signout/", auth_views.LogoutView.as_view(), name="signout"),
    path("name/", views.name_change, name="name_change"),
    path("password/", views.PasswordChangeView.as_view(), name="password_change"),
    path("invitations/", views.invitations, name="invitations"),
    path("invitations/<int:pk>/revoke/", views.revoke, name="revoke_invitation"),
    path("invite/<str:token>/", views.accept, name="accept"),
]
