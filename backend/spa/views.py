# Portfolio / personalized-link views — Phase 3+.

from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

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
