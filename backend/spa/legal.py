"""Imprint, privacy policy, terms: Django-rendered (no JS), identical on every host.

The site operator, not a portfolio's owner, answers for every portfolio, so the operator's identity
comes from the PORTFOLIO_OWNER_USERNAME user's profile. Nothing personal is hardcoded: the repo is
public, and a fork must not inherit the operator's address.
"""

from django.conf import settings
from django.http import Http404
from django.shortcuts import render

from spa.portfolio import site_owner

LEGAL_PAGES = {
    "imprint": "Impressum",
    "privacy": "Datenschutzerklärung",
    "terms": "Nutzungsbedingungen",
}


def operator() -> dict | None:
    """The operator's imprint facts, or None when PORTFOLIO_OWNER_USERNAME names nobody. The full
    name comes first: § 5 DDG wants the real name, and a display name can be anything."""
    user = site_owner()
    if user is None:
        return None
    profile = user.profile
    return {
        "name": user.get_full_name() or profile.display_name or user.username,
        "street": profile.street,
        "address_line2": profile.address_line2,
        "zip": profile.zip,
        "city": profile.city,
        "country": profile.country,
        "phone": profile.phone,
        "email": settings.LEGAL_CONTACT_EMAIL or user.email,
    }


def legal_page(request, page: str):
    if page not in LEGAL_PAGES:
        raise Http404
    return render(
        request,
        f"spa/legal/{page}.html",
        {
            "title": LEGAL_PAGES[page],
            "operator": operator(),
            "base_domain": settings.BASE_DOMAIN,
            "llm_log_retention_days": settings.LLM_LOG_RETENTION_DAYS,
        },
    )
