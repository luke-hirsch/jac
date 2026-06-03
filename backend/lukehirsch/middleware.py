"""lukehirsch.middleware — site-level middleware.

AdminRequireMfaMiddleware enforces that staff users with TOTP or WebAuthn
enrolled must complete an MFA challenge before accessing /admin/.  The signal
handler in spa.signals sets session["mfa_authenticated"] = True once allauth
confirms a successful authenticator use.
"""

from django.conf import settings
from django.http import HttpResponseRedirect

from allauth.mfa.models import Authenticator


class AdminRequireMfaMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._should_challenge(request):
            next_url = request.get_full_path()
            return HttpResponseRedirect(
                f"{settings.FRONTEND_URL}/auth/mfa-challenge?next={next_url}"
            )
        return self.get_response(request)

    def _should_challenge(self, request):
        if not request.path.startswith("/admin/"):
            return False
        if not request.user.is_authenticated:
            return False
        if not request.user.is_staff:
            return False
        if request.session.get("mfa_authenticated"):
            return False
        return Authenticator.objects.filter(
            user=request.user,
            type__in=[Authenticator.Type.TOTP, Authenticator.Type.WEBAUTHN],
        ).exists()
