from django.urls import path
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter

from .views import (
    CoverageView,
    DeleteCVView,
    DiscoveryProbeView,
    EmployerCandidateViewSet,
    FilterOptionsView,
    JobSourceViewSet,
    JobViewSet,
    ParseCVView,
    PreviewRedactedView,
    SearchView,
    SourceStatusView,
    StatsSummaryView,
    UploadCVView,
    UserJobViewSet,
    UserPreferenceView,
    UserProfileView,
)

router = DefaultRouter()
router.register(r'jobs', JobViewSet)
router.register(r'sources', JobSourceViewSet)
router.register(r'user-jobs', UserJobViewSet, basename='userjob')
router.register(r'employer-candidates', EmployerCandidateViewSet, basename='employercandidate')

urlpatterns = [
    path('auth/token/', obtain_auth_token, name='api-token-auth'),
    path('preferences/', UserPreferenceView.as_view(), name='preferences'),
    path('profile/', UserProfileView.as_view(), name='profile'),
    path('profile/upload/', UploadCVView.as_view(), name='profile-upload'),
    path('profile/cv/', DeleteCVView.as_view(), name='profile-delete-cv'),
    path('profile/preview/', PreviewRedactedView.as_view(), name='profile-preview'),
    path('profile/parse/', ParseCVView.as_view(), name='profile-parse'),
    path('search/', SearchView.as_view(), name='search'),
    path('search/coverage/', CoverageView.as_view(), name='search-coverage'),
    path('stats/summary/', StatsSummaryView.as_view(), name='stats-summary'),
    path('stats/filters/', FilterOptionsView.as_view(), name='stats-filters'),
    path('sources/status/', SourceStatusView.as_view(), name='sources-status'),
    path('discover/probe/', DiscoveryProbeView.as_view(), name='discover-probe'),
] + router.urls
