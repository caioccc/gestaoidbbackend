from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views
from . import viewsets

router = DefaultRouter()
router.register(r'entries', viewsets.FinancialEntryViewSet, basename='entry')
router.register(r'exits', viewsets.FinancialExitViewSet, basename='exit')
router.register(r'tithers', viewsets.TitherViewSet, basename='tither')
router.register(r'calendar/events', viewsets.CalendarEventViewSet, basename='calendar-event')

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
admin_router.register(
    r'admin/churches/(?P<church_pk>[0-9]+)/tithers',
    viewsets.AdminChurchTithersViewSet,
    basename='admin-church-tithers',
)
admin_router.register(
    r'admin/churches/(?P<church_pk>[0-9]+)/calendar/events',
    viewsets.AdminChurchCalendarEventsViewSet,
    basename='admin-church-calendar-events',
)

urlpatterns = [
    path('public/calendar/<str:hash>/', viewsets.PublicCalendarEventsView.as_view(), name='public-calendar'),
    path('dashboard/summary/', viewsets.DashboardSummaryView.as_view(), name='dashboard-summary'),
    path('dre/summary/', viewsets.DreSummaryView.as_view(), name='dre-summary'),
    path('import-spreadsheet/', viewsets.ImportSpreadsheetView.as_view(), name='import-spreadsheet'),
    path('import-inspect-spreadsheet/', viewsets.InspectSpreadsheetView.as_view(), name='import-inspect-spreadsheet'),
    path('import-availability/', viewsets.ExistingMonthDataView.as_view(), name='import-availability'),
    path('tithers/reconciliation/', viewsets.TithersReconciliationView.as_view(), name='tithers-reconciliation'),
    path('tithers/matrix/', viewsets.TithersMatrixView.as_view(), name='tithers-matrix'),
    path('tithers/repeat-audit/', viewsets.TitherRepeatAuditView.as_view(), name='tither-repeat-audit'),
    path('validation/', viewsets.MonthlyValidationView.as_view(), name='monthly-validation'),
    path('tithers/<int:tither_pk>/tithe-records/', viewsets.TitherTitheRecordsView.as_view(), name='tither-tithe-records'),
    path('monthly-closings/', viewsets.MonthlyClosingsView.as_view(), name='monthly-closings'),
    path('reports/regional/', viewsets.RegionalReportView.as_view(), name='reports-regional'),
    path('reports/regional/pdf/', viewsets.RegionalReportPdfView.as_view(), name='reports-regional-pdf'),
    path('reports/regional/xls/', views.RegionalReportXlsView.as_view(), name='reports-regional-xls'),
    path('reports/national/xlsx/', views.NationalReportXlsxView.as_view(), name='reports-national-xlsx'),
    path('reports/caixa/download/', views.CaixaDownloadView.as_view(), name='reports-caixa-download'),
    path('templates/<str:template_key>/download/', views.TemplateDownloadView.as_view(), name='template-download'),
    path('categories/', viewsets.CategoriesView.as_view(), name='categories'),
    path('admin/churches/<int:church_pk>/dashboard/summary/', viewsets.AdminChurchDashboardView.as_view(), name='admin-church-dashboard'),
    path('admin/churches/<int:church_pk>/dre/summary/', viewsets.AdminChurchDreSummaryView.as_view(), name='admin-church-dre-summary'),
    path('admin/churches/<int:church_pk>/import-spreadsheet/', viewsets.AdminChurchImportView.as_view(), name='admin-church-import'),
    path('admin/churches/<int:church_pk>/import-inspect-spreadsheet/', viewsets.AdminChurchInspectSpreadsheetView.as_view(), name='admin-church-import-inspect-spreadsheet'),
    path('admin/churches/<int:church_pk>/import-availability/', viewsets.AdminChurchExistingMonthDataView.as_view(), name='admin-church-import-availability'),
    path('admin/churches/<int:church_pk>/tithers/reconciliation/', viewsets.AdminChurchTithersReconciliationView.as_view(), name='admin-church-tithers-reconciliation'),
    path('admin/churches/<int:church_pk>/tithers/matrix/', viewsets.AdminChurchTithersMatrixView.as_view(), name='admin-church-tithers-matrix'),
    path('admin/churches/<int:church_pk>/tithers/repeat-audit/', viewsets.AdminChurchTitherRepeatAuditView.as_view(), name='admin-church-tither-repeat-audit'),
    path('admin/churches/<int:church_pk>/validation/', viewsets.AdminChurchMonthlyValidationView.as_view(), name='admin-church-monthly-validation'),
    path('admin/churches/<int:church_pk>/tithers/<int:tither_pk>/tithe-records/', viewsets.AdminChurchTitherTitheRecordsView.as_view(), name='admin-church-tither-tithe-records'),
    path('admin/churches/<int:church_pk>/monthly-closings/', viewsets.AdminChurchMonthlyClosingsView.as_view(), name='admin-church-monthly-closings'),
    path('admin/churches/<int:church_pk>/reports/regional/', viewsets.AdminChurchRegionalReportView.as_view(), name='admin-church-reports-regional'),
    path('admin/churches/<int:church_pk>/reports/regional/pdf/', viewsets.AdminChurchRegionalReportPdfView.as_view(), name='admin-church-reports-regional-pdf'),
    path('admin/churches/<int:church_pk>/reports/national/xlsx/', views.AdminChurchNationalReportXlsxView.as_view(), name='admin-church-reports-national-xlsx'),
    path('admin/churches/<int:church_pk>/reports/caixa/download/', viewsets.AdminChurchCaixaDownloadView.as_view(), name='admin-church-reports-caixa-download'),
    path('admin/churches/<int:church_pk>/categories/', viewsets.AdminChurchCategoriesView.as_view(), name='admin-church-categories'),
    path('export/entries/', viewsets.ExportEntriesView.as_view(), name='export-entries'),
    path('export/exits/', viewsets.ExportExitsView.as_view(), name='export-exits'),
    path('export/closings/', viewsets.ExportClosingsView.as_view(), name='export-closings'),
    path('admin/churches/<int:church_pk>/export/entries/', viewsets.AdminChurchExportEntriesView.as_view(), name='admin-church-export-entries'),
    path('admin/churches/<int:church_pk>/export/exits/', viewsets.AdminChurchExportExitsView.as_view(), name='admin-church-export-exits'),
    path('admin/churches/<int:church_pk>/export/closings/', viewsets.AdminChurchExportClosingsView.as_view(), name='admin-church-export-closings'),
    path('', include(router.urls)),
    path('', include(admin_router.urls)),
]
