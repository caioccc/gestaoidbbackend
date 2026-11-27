from django.urls import path
from rest_framework.routers import DefaultRouter

from . import viewsets

router = DefaultRouter()
router.register(
    r'events',
    viewsets.EventViewSet,
    basename='event',
)
router.register(
    r'registrations',
    viewsets.EventRegistrationViewSet,
    basename='event-registration',
)
router.register(
    r'checkin-configs',
    viewsets.EventCheckinConfigViewSet,
    basename='event-checkin-config',
)
router.register(
    r'checkin-sessions',
    viewsets.EventSessionViewSet,
    basename='event-checkin-session',
)
router.register(
    r'checkin-team',
    viewsets.EventCheckinTeamMemberViewSet,
    basename='event-checkin-team',
)
router.register(
    r'checkins',
    viewsets.EventCheckinViewSet,
    basename='event-checkin',
)

admin_router = DefaultRouter()
admin_router.register(
    r'admin/churches/(?P<church_pk>[0-9]+)/events',
    viewsets.EventViewSet,
    basename='admin-church-event',
)
admin_router.register(
    r'admin/churches/(?P<church_pk>[0-9]+)/registrations',
    viewsets.AdminChurchEventRegistrationViewSet,
    basename='admin-church-event-registration',
)
admin_router.register(
    r'admin/churches/(?P<church_pk>[0-9]+)/checkin-configs',
    viewsets.AdminChurchEventCheckinConfigViewSet,
    basename='admin-church-event-checkin-config',
)
admin_router.register(
    r'admin/churches/(?P<church_pk>[0-9]+)/checkin-sessions',
    viewsets.AdminChurchEventSessionViewSet,
    basename='admin-church-event-checkin-session',
)
admin_router.register(
    r'admin/churches/(?P<church_pk>[0-9]+)/checkin-team',
    viewsets.AdminChurchEventCheckinTeamMemberViewSet,
    basename='admin-church-event-checkin-team',
)
admin_router.register(
    r'admin/churches/(?P<church_pk>[0-9]+)/checkins',
    viewsets.AdminChurchEventCheckinViewSet,
    basename='admin-church-event-checkin',
)

urlpatterns = [
    path(
        'public/checkin/<str:token>/',
        viewsets.PublicEventCheckinView.as_view(),
        name='public-event-checkin',
    ),
    path(
        'public/registration/<str:token>/',
        viewsets.PublicEventRegistrationView.as_view(),
        name='public-event-registration',
    ),
] + router.urls + admin_router.urls
