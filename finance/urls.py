from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import viewsets

router = DefaultRouter()
router.register(r'entries', viewsets.FinancialEntryViewSet, basename='entry')
router.register(r'exits', viewsets.FinancialExitViewSet, basename='exit')

admin_router = DefaultRouter()
admin_router.register(
    r'admin/churches/(?P<church_pk>[0-9]+)/entries',
    viewsets.AdminChurchEntriesViewSet,
    basename='admin-church-entries',
)
admin_router.register(
    r'admin/churches/(?P<church_pk>[0-9]+)/exits',
    viewsets.AdminChurchExitsViewSet,
    basename='admin-church-exits',
)

urlpatterns = [
    path('dashboard/summary/', viewsets.DashboardSummaryView.as_view(), name='dashboard-summary'),
    path('import-spreadsheet/', viewsets.ImportSpreadsheetView.as_view(), name='import-spreadsheet'),
    path('tithers/reconciliation/', viewsets.TithersReconciliationView.as_view(), name='tithers-reconciliation'),
    path('tithers/matrix/', viewsets.TithersMatrixView.as_view(), name='tithers-matrix'),
    path('monthly-closings/', viewsets.MonthlyClosingsView.as_view(), name='monthly-closings'),
    path('reports/regional/', viewsets.RegionalReportView.as_view(), name='reports-regional'),
    path('reports/regional/pdf/', viewsets.RegionalReportPdfView.as_view(), name='reports-regional-pdf'),
    path('categories/', viewsets.CategoriesView.as_view(), name='categories'),
    path('admin/churches/<int:church_pk>/dashboard/summary/', viewsets.AdminChurchDashboardView.as_view(), name='admin-church-dashboard'),
    path('admin/churches/<int:church_pk>/import-spreadsheet/', viewsets.AdminChurchImportView.as_view(), name='admin-church-import'),
    path('admin/churches/<int:church_pk>/tithers/reconciliation/', viewsets.AdminChurchTithersReconciliationView.as_view(), name='admin-church-tithers-reconciliation'),
    path('admin/churches/<int:church_pk>/tithers/matrix/', viewsets.AdminChurchTithersMatrixView.as_view(), name='admin-church-tithers-matrix'),
    path('admin/churches/<int:church_pk>/monthly-closings/', viewsets.AdminChurchMonthlyClosingsView.as_view(), name='admin-church-monthly-closings'),
    path('admin/churches/<int:church_pk>/reports/regional/', viewsets.AdminChurchRegionalReportView.as_view(), name='admin-church-reports-regional'),
    path('admin/churches/<int:church_pk>/reports/regional/pdf/', viewsets.AdminChurchRegionalReportPdfView.as_view(), name='admin-church-reports-regional-pdf'),
    path('admin/churches/<int:church_pk>/categories/', viewsets.AdminChurchCategoriesView.as_view(), name='admin-church-categories'),
    path('', include(router.urls)),
    path('', include(admin_router.urls)),
]
