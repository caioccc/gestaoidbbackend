from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register('bands', views.BandViewSet, basename='band')
router.register('setlists', views.BandSetlistViewSet, basename='band-setlist')
router.register('ministries', views.MinistryViewSet, basename='ministry')
router.register('rosters', views.VolunteerRosterViewSet, basename='roster')
router.register('assignments', views.RosterAssignmentViewSet, basename='roster-assignment')
router.register('songs', views.SongViewSet, basename='song')

urlpatterns = [
    path('youtube-search/', views.YouTubeSearchView.as_view(), name='youtube-search'),
    path('chordify/', views.ChordifyView.as_view(), name='chordify'),
    path('', include(router.urls)),
]