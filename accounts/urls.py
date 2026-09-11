from django.urls import path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from . import viewsets

router = DefaultRouter()
router.register('churches', viewsets.ChurchViewSet, basename='church')
router.register('members', viewsets.MemberSelfViewSet, basename='member-self')
router.register('ministry-areas', viewsets.MinistryAreaViewSet, basename='ministry-area')
router.register('storage-locations', viewsets.StorageLocationViewSet, basename='storage-location')
router.register('materials', viewsets.MaterialItemViewSet, basename='material')
router.register('loans', viewsets.LoanViewSet, basename='loan')
router.register('cultos', viewsets.WorshipServiceViewSet, basename='worship')
router.register('minutes', viewsets.ChurchMinutesViewSet, basename='minutes')
router.register('church-links', viewsets.ChurchPublicLinkViewSet, basename='church-link')

urlpatterns = [
    path('login/', viewsets.LoginView.as_view(), name='login'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path('register/', viewsets.RegisterView.as_view(), name='register'),
    path('profile/', viewsets.ProfileView.as_view(), name='profile'),
    path('me/', viewsets.MeView.as_view(), name='me'),
    path('switch-church/', viewsets.SwitchChurchView.as_view(), name='switch-church'),
    path(
        'churches/search-parents/',
        viewsets.SearchParentChurchesView.as_view(),
        name='search-parent-churches',
    ),
    path(
        'churches/<int:church_pk>/users/',
        viewsets.ChurchUsersView.as_view(),
        name='church-users',
    ),
    path(
        'churches/<int:church_pk>/users/<int:membership_pk>/',
        viewsets.ChurchUserDetailView.as_view(),
        name='church-user-detail',
    ),
    path(
        'churches/<int:church_pk>/members/',
        viewsets.ChurchMembersViewSet.as_view(
            {'get': 'list', 'post': 'create'}
        ),
        name='church-members-list',
    ),
    path(
        'churches/<int:church_pk>/members/<int:pk>/',
        viewsets.ChurchMembersViewSet.as_view(
            {'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}
        ),
        name='church-members-detail',
    ),
    path(
        'churches/<int:church_pk>/ministry-areas/',
        viewsets.ChurchMinistryAreasViewSet.as_view(
            {'get': 'list', 'post': 'create'}
        ),
        name='church-ministry-areas-list',
    ),
    path(
        'churches/<int:church_pk>/ministry-areas/<int:pk>/',
        viewsets.ChurchMinistryAreasViewSet.as_view(
            {'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}
        ),
        name='church-ministry-areas-detail',
    ),
    path(
        'churches/<int:pk>/profile/',
        viewsets.ChurchProfileManageView.as_view(),
        name='church-manage-profile',
    ),
    path(
        'pending-congregations/',
        viewsets.PendingCongregationsView.as_view(),
        name='pending-congregations',
    ),
    path(
        'pending-congregations/<int:pk>/approve/',
        viewsets.ApproveCongregationView.as_view(),
        name='approve-congregation',
    ),
    path(
        'pending-congregations/<int:pk>/reject/',
        viewsets.RejectCongregationView.as_view(),
        name='reject-congregation',
    ),
    path(
        'accounting-categories/',
        viewsets.AccountingCategoriesView.as_view(),
        name='accounting-categories',
    ),
    path(
        'profile/reset-password/',
        viewsets.ResetOwnPasswordView.as_view(),
        name='reset-own-password',
    ),
    path(
        'members/import-inspect/',
        viewsets.MemberImportInspectView.as_view(),
        name='member-import-inspect',
    ),
    path(
        'members/import/',
        viewsets.MemberImportView.as_view(),
        name='member-import',
    ),
    path(
        'members/<int:pk>/declaration/',
        viewsets.MemberDeclarationView.as_view(),
        name='member-declaration',
    ),
    path(
        'members/<int:pk>/documents/',
        viewsets.MemberDocumentsView.as_view(),
        name='member-documents',
    ),
    path(
        'documents/<int:pk>/',
        viewsets.MemberDocumentDetailView.as_view(),
        name='member-document-detail',
    ),
    path(
        'documents/<int:pk>/download/',
        viewsets.MemberDocumentDownloadView.as_view(),
        name='member-document-download',
    ),
    path(
        'members/report/',
        viewsets.MemberReportPdfView.as_view(),
        name='member-report',
    ),
    path(
        'birthdays/',
        viewsets.BirthdayMembersView.as_view(),
        name='birthday-members',
    ),
    path(
        'transfers/',
        viewsets.MemberTransferView.as_view(),
        name='member-transfers',
    ),
    path(
        'transfers/incoming/',
        viewsets.IncomingMemberTransfersView.as_view(),
        name='member-transfers-incoming',
    ),
    path(
        'transfers/<int:pk>/receive/',
        viewsets.ReceiveMemberTransferView.as_view(),
        name='member-transfer-receive',
    ),
    path(
        'transfers/<int:pk>/cancel/',
        viewsets.CancelMemberTransferView.as_view(),
        name='member-transfer-cancel',
    ),
    path(
        'churches/search-transfers/',
        viewsets.TransferTargetChurchesView.as_view(),
        name='transfer-target-churches',
    ),
    path(
        'alerts/',
        viewsets.AlertsView.as_view(),
        name='alerts',
    ),
    path(
        'public/card/<str:hash>/',
        viewsets.PublicMemberCardView.as_view(),
        name='public-member-card',
    ),
    path(
        'public/forms/<str:hash>/',
        viewsets.PublicMemberFormView.as_view(),
        name='public-member-form',
    ),
    path(
        'members/form/public-link/',
        viewsets.ChurchMemberFormLinkView.as_view(),
        name='member-form-public-link',
    ),
    path(
        'member-submissions/',
        viewsets.MemberSubmissionsView.as_view(),
        name='member-submissions',
    ),
    path(
        'member-submissions/<int:pk>/review/',
        viewsets.MemberSubmissionReviewView.as_view(),
        name='member-submission-review',
    ),
    path(
        'public/minutes/<str:hash>/',
        viewsets.PublicMinutesView.as_view(),
        name='public-minutes',
    ),
    path(
        'public/minutes/<str:hash>/pdf/',
        viewsets.PublicMinutesPdfView.as_view(),
        name='public-minutes-pdf',
    ),
    path(
        'public/churches/<str:slug>/links/',
        viewsets.PublicChurchLinksView.as_view(),
        name='public-church-links',
    ),
    path(
        'public/links/<int:pk>/click/',
        viewsets.PublicChurchLinkClickView.as_view(),
        name='public-church-link-click',
    ),
    path(
        'calendar/public-link/',
        viewsets.CalendarPublicLinkView.as_view(),
        name='calendar-public-link',
    ),
    path(
        'calendar/public-link/regenerate/',
        viewsets.CalendarPublicLinkRegenerateView.as_view(),
        name='calendar-public-link-regenerate',
    ),
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
    path(
        'admin/churches/<int:pk>/profile/',
        viewsets.AdminChurchProfileView.as_view(),
        name='admin-church-profile',
    ),
    path(
        'admin/churches/<int:pk>/reset-password/',
        viewsets.AdminChurchResetPasswordView.as_view(),
        name='admin-church-reset-password',
    ),
    path(
        'admin/churches/<int:pk>/clear-data/',
        viewsets.AdminChurchClearDataView.as_view(),
        name='admin-church-clear-data',
    ),
] + router.urls
