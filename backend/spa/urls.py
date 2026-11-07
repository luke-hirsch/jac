from django.urls import path

from spa.views import (
    AccountDeleteView,
    PersonalityDossierRebuildView,
    PersonalityProfileView,
    PersonalityQuestionDetailView,
    PersonalityQuestionListCreateView,
    PortfolioBlockDetailView,
    PortfolioBlockListCreateView,
    PortfolioIntroStatusView,
    PortfolioIntroView,
    PortfolioLinkDetailView,
    PortfolioLinkListCreateView,
    PortfolioLinkRevokeView,
    PortfolioMetaView,
    PortfolioNativeView,
    PortfolioPrewarmView,
    PortfolioRankView,
    PortfolioResolveView,
    SignatureFileView,
    UserProfileView,
)

urlpatterns = [
    path("profile/", UserProfileView.as_view(), name="user-profile"),
    path("profile/signature/", SignatureFileView.as_view(), name="profile-signature"),
    path("personality/", PersonalityProfileView.as_view(), name="personality-profile"),
    path(
        "personality/rebuild/",
        PersonalityDossierRebuildView.as_view(),
        name="personality-rebuild",
    ),
    path("account/", AccountDeleteView.as_view(), name="account-delete"),
    path(
        "personality/questions/",
        PersonalityQuestionListCreateView.as_view(),
        name="personality-questions",
    ),
    path(
        "personality/questions/<int:pk>/",
        PersonalityQuestionDetailView.as_view(),
        name="personality-question-detail",
    ),
    path(
        "portfolio/manage/blocks/",
        PortfolioBlockListCreateView.as_view(),
        name="portfolio-blocks",
    ),
    path(
        "portfolio/manage/blocks/<int:pk>/",
        PortfolioBlockDetailView.as_view(),
        name="portfolio-block-detail",
    ),
    path(
        "portfolio/manage/links/",
        PortfolioLinkListCreateView.as_view(),
        name="portfolio-links",
    ),
    path(
        "portfolio/manage/links/<int:pk>/",
        PortfolioLinkDetailView.as_view(),
        name="portfolio-link-detail",
    ),
    path(
        "portfolio/manage/links/<int:pk>/revoke/",
        PortfolioLinkRevokeView.as_view(),
        name="portfolio-link-revoke",
    ),
    # Public: anonymous, the owner named by the handle in the path (never the Host header).
    path(
        "public/intro/<uuid:pk>/",
        PortfolioIntroStatusView.as_view(),
        name="portfolio-intro-status",
    ),
    path(
        "public/<slug:handle>/links/<slug:slug>/",
        PortfolioResolveView.as_view(),
        name="portfolio-resolve",
    ),
    path(
        "public/<slug:handle>/native/",
        PortfolioNativeView.as_view(),
        name="portfolio-native",
    ),
    path(
        "public/<slug:handle>/native/rank/",
        PortfolioRankView.as_view(),
        name="portfolio-rank",
    ),
    path(
        "public/<slug:handle>/native/meta/",
        PortfolioMetaView.as_view(),
        name="portfolio-meta",
    ),
    path(
        "public/<slug:handle>/native/prewarm/",
        PortfolioPrewarmView.as_view(),
        name="portfolio-prewarm",
    ),
    path(
        "public/<slug:handle>/native/intro/",
        PortfolioIntroView.as_view(),
        name="portfolio-intro",
    ),
]
