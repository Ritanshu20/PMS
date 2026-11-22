from django.urls import path

from accounts import api_views

urlpatterns = [
    path("register/", api_views.RegisterView.as_view(), name="auth-register"),
    path("login/", api_views.LoginView.as_view(), name="auth-login"),
    path("refresh/", api_views.RefreshView.as_view(), name="auth-refresh"),
    path("logout/", api_views.LogoutView.as_view(), name="auth-logout"),
    path("me/", api_views.MeView.as_view(), name="auth-me"),
]
