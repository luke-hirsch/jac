from django.urls import path

from spa.views import (
    AccountDeleteView,
    PersonalityDossierRebuildView,
    PersonalityProfileView,
    UserProfileView,
)

urlpatterns = [
    path("profile/", UserProfileView.as_view(), name="user-profile"),
    path("personality/", PersonalityProfileView.as_view(), name="personality-profile"),
    path(
        "personality/rebuild/",
        PersonalityDossierRebuildView.as_view(),
        name="personality-rebuild",
    ),
    path("account/", AccountDeleteView.as_view(), name="account-delete"),
]
