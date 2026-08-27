from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from . import viewsets

urlpatterns = [
    path('login/', viewsets.LoginView.as_view(), name='login'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path('register/', viewsets.RegisterView.as_view(), name='register'),
    path('profile/', viewsets.ProfileView.as_view(), name='profile'),
    path(
        'admin/churches/',
        viewsets.AdminChurchesView.as_view(),
        name='admin-churches',
    ),
    path(
        'admin/pending-churches/',
        viewsets.AdminPendingChurchesView.as_view(),
        name='admin-pending-churches',
    ),
    path(
        'admin/churches/<int:pk>/approve/',
        viewsets.AdminApproveChurchView.as_view(),
        name='admin-approve-church',
    ),
    path(
        'admin/churches/<int:pk>/reject/',
        viewsets.AdminRejectChurchView.as_view(),
        name='admin-reject-church',
    ),
]
