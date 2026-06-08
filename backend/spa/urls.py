from django.urls import path

from spa.views import AccountDeleteView, UserProfileView

urlpatterns = [
    path("profile/", UserProfileView.as_view(), name="user-profile"),
    path("account/", AccountDeleteView.as_view(), name="account-delete"),
]
