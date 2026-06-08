# Portfolio / personalized-link views — Phase 3+.

from allauth.account.internal.flows.reauthentication import did_recently_authenticate
from django.contrib.auth import logout
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from spa.models import UserProfile
from spa.serializers import UserProfileSerializer


class UserProfileView(generics.RetrieveUpdateAPIView):
    """GET/PUT/PATCH the request user's profile.

    UserProfile rows are auto-created on user creation via post_save signal,
    so `request.user.profile` is guaranteed to exist.
    """

    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return UserProfile.objects.get(user=self.request.user)


class AccountDeleteView(APIView):
    """DELETE the requesting user (and everything that cascades from auth.User).

    allauth headless has no built-in account-delete endpoint, so we roll our own.
    The response shape on the reauth-gate matches allauth's ReauthenticationResponse
    so the frontend's `withReauth` helper picks it up and prompts for password.
    """

    permission_classes = [IsAuthenticated]

    def delete(self, request, *args, **kwargs):
        if not did_recently_authenticate(request):
            return Response(
                {"data": {"flows": [{"id": "reauthenticate"}]}},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        user = request.user
        logout(request)
        user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
