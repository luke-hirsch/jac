# Portfolio / personalized-link views — Phase 3+.

from allauth.account.internal.flows.reauthentication import did_recently_authenticate
from django.contrib.auth import logout
from llm_connector.conf import ExecutorError, resolve_executor
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from spa.models import PersonalityProfile, PersonalityQuestion, UserProfile
from spa.serializers import (
    PersonalityProfileSerializer,
    PersonalityQuestionSerializer,
    UserProfileSerializer,
)


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


class PersonalityProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = PersonalityProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return PersonalityProfile.objects.get(user=self.request.user)


class PersonalityDossierRebuildView(APIView):
    """POST: force-rebuild + return the dossier (preview the distilled text).
    Optional body {provider, model}; blank = the user's default executor."""

    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        try:
            executor = resolve_executor(
                request.user,
                request.data.get("provider", ""),
                request.data.get("model", ""),
            )
        except ExecutorError as exc:
            return Response(
                {"provider": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST
            )
        prof = PersonalityProfile.objects.get(user=request.user)
        prof.dossier_built_at = None
        return Response({"dossier": prof.ensure_dossier(executor)})


class PersonalityQuestionListCreateView(generics.ListCreateAPIView):
    """GET the user's visible questions (system defaults + own); POST adds one of the user's
    own. Small list — pagination off, so the response is a plain array matching the shape
    embedded in the personality endpoint."""

    serializer_class = PersonalityQuestionSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return PersonalityQuestion.objects.for_user(self.request.user)


class PersonalityQuestionDetailView(generics.RetrieveUpdateDestroyAPIView):
    """PATCH/DELETE a question the user OWNS. System defaults are read-only: they are absent
    from this queryset, so addressing one 404s (no guard needed)."""

    serializer_class = PersonalityQuestionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return PersonalityQuestion.objects.filter(user=self.request.user)
