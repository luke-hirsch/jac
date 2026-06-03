from django.urls import path

from spa.views import UserProfileView

urlpatterns = [
    path("profile/", UserProfileView.as_view(), name="user-profile"),
    # Account deletion: DELETE /_allauth/browser/v1/auth/account (allauth headless)
]
