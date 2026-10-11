from django.urls import path
from rest_framework.routers import DefaultRouter

from . import viewsets

router = DefaultRouter()
router.register('folders', viewsets.FolderCultoViewSet, basename='folder')

urlpatterns = [
    path(
        'public/folders/<str:hash>/',
        viewsets.PublicFolderView.as_view(),
        name='public-folder',
    ),
] + router.urls