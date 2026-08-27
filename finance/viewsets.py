"""Views do mÃ³dulo financeiro (EclÃ©sia IDB)."""
from datetime import datetime
from decimal import Decimal

from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Church
from accounts.viewsets import IsStaffPermission

from .models import FinancialEntry, FinancialExit, Tither, TitheRecord
from .serializers import (
    CategorySerializer,
    FinancialEntrySerializer,
    FinancialExitSerializer,
)
from . import services


def _int_param(request, name, default):
    try:
        return int(request.query_params.get(name, default))
    except (TypeError, ValueError):
        return default


class FinancialEntryViewSet(viewsets.ModelViewSet):
    """CRUD de entradas, com filtros por perÃ­odo e ordenaÃ§Ã£o por data desc."""

    serializer_class = FinancialEntrySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = FinancialEntry.objects.filter(
            church=self.request.user.church,
       ).order_by('-date', '-created_at')

        start = self.request.query_params.get('start_date')
        end = self.request.query_params.get('end_date')
        if start:
            qs = qs.filter(date__gte=start)
        if end:
            qs = qs.filter(date__lte=end)
        return qs

    def perform_create(self, serializer):
        serializer.save(church=self.request.user.church)


class FinancialExitViewSet(viewsets.ModelViewSet):
    """CRUD de saÃ­das, com filtros por perÃ­odo."""

    serializer_class = FinancialExitSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        qs = FinancialExit.objects.filter(
            church=self.request.user.church,
        ).order_by('-date', '-created_at')

        start = self.request.query_params.get('start_date')
        end = self.request.query_params.get('end_date')
        if start:
            qs = qs.filter(date__gte=start)
        if end:
            qs = qs.filter(date__lte=end)
        return qs

    def perform_create(self, serializer):
        serializer.save(church=self.request.user.church)


class DashboardSummaryView(APIView):
    """Resumo anual com totais e sÃ©rie temporal dos 12 meses."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        year = _int_param(request, 'year', datetime.now().year)
        data = services.dashboard_summary(request.user.church, year)
        return Response(data)


class ImportSpreadsheetView(APIView):
    """Endpoint multipart para upload e processamento em lote das planilhas."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        files = {}
        for key in ('entries', 'exits', 'tithers'):
            if key in request.FILES:
                files[key] = request.FILES[key]

        if not files:
            return Response(
                {'detail': 'Envie ao menos uma planilha (entries, exits ou tithers).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = services.import_spreadsheets(request.user.church, files)
        return Response(result, status=status.HTTP_200_OK)


class TithersReconciliationView(APIView):
    """Status da conciliaÃ§Ã£o entre dizimistas e entradas do mÃªs."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        year = _int_param(request, 'year', datetime.now().year)
        month = _int_param(request, 'month', datetime.now().month)
        if not (1 <= month <= 12):
            return Response(
                {'detail': 'month deve estar entre 1 e 12.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = services.reconcile_tithers(request.user.church, year, month)
        return Response(data)


class TithersMatrixView(APIView):
    """Matriz anual de dizimistas: membros x valores mensais (Jan a Dez)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        year = _int_param(request, 'year', datetime.now().year)
        church = request.user.church

        tithers = (
            Tither.objects.filter(church=church)
            .order_by('name')
            .prefetch_related('tithe_records')
        )

        records = (
            TitheRecord.objects.filter(tither__church=church, year=year)
            .values('tither_id', 'month')
            .annotate(total=Sum('amount'))
        )
        by_tither: dict = {}
        for row in records:
            by_tither.setdefault(row['tither_id'], {})[row['month']] = (
                str(row['total'])
            )

        month_totals = [Decimal('0.00') for _ in range(12)]
        members = []
        grand_total = Decimal('0.00')

        for tither in tithers:
            months = [None] * 12
            member_total = Decimal('0.00')
            for month, amount in (by_tither.get(tither.id) or {}).items():
                months[month - 1] = amount
                d = Decimal(amount)
                member_total += d
                month_totals[month - 1] += d
            grand_total += member_total
            members.append(
                {
                    'id': tither.id,
                    'name': 'Dizimista Anônimo' if tither.is_anonymous else tither.name,
                    'is_anonymous': tither.is_anonymous,
                    'months': months,
                    'total': str(member_total),
                }
            )

        return Response(
            {
                'year': year,
                'grand_total': str(grand_total),
                'month_totals': [str(v) for v in month_totals],
                'members': members,
            }
        )


class MonthlyClosingsView(APIView):
    """Resumo mensal do caixa (12 meses) para o Fechamento Mensal."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        year = _int_param(request, 'year', datetime.now().year)
        church = request.user.church

        months = []
        grand = {
            'previous_balance': Decimal('0.00'),
            'total_entries': Decimal('0.00'),
            'total_exits': Decimal('0.00'),
            'final_balance': Decimal('0.00'),
        }
        for month in range(1, 13):
            closing, _ = services.get_or_create_monthly_closing(
                church, year, month
            )
            months.append(
                {
                    'year': year,
                    'month': month,
                    'is_closed': closing.is_closed,
                    'previous_balance': str(closing.previous_balance),
                    'total_entries': str(closing.total_entries),
                    'total_exits': str(closing.total_exits),
                    'final_balance': str(closing.final_balance),
                }
            )
            grand['total_entries'] += closing.total_entries
            grand['total_exits'] += closing.total_exits
            grand['final_balance'] += closing.final_balance

        return Response(
            {
                'year': year,
                'grand_total': {k: str(v) for k, v in grand.items()},
                'months': months,
            }
        )


class RegionalReportView(APIView):
    """Remessa regional e balancete do mÃªs."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        year = _int_param(request, 'year', datetime.now().year)
        month = _int_param(request, 'month', datetime.now().month)
        if not (1 <= month <= 12):
            return Response(
                {'detail': 'month deve estar entre 1 e 12.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        remittance = services.calc_regional_remittance(
            request.user.church, year, month,
        )
        balance = services.build_monthly_balance(
            request.user.church, year, month,
        )
        closing, _ = services.get_or_create_monthly_closing(
            request.user.church, year, month,
        )
        return Response({
            'remittance': remittance,
            'monthly_balance': balance,
            'monthly_closing': {
                'id': closing.id,
                'previous_balance': closing.previous_balance,
                'total_entries': closing.total_entries,
                'total_exits': closing.total_exits,
                'final_balance': closing.final_balance,
                'is_closed': closing.is_closed,
            },
        })


class RegionalReportPdfView(APIView):
    """GeraÃ§Ã£o de PDF oficial do relatÃ³rio regional via xhtml2pdf."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        year = _int_param(request, 'year', datetime.now().year)
        month = _int_param(request, 'month', datetime.now().month)
        if not (1 <= month <= 12):
            return Response(
                {'detail': 'month deve estar entre 1 e 12.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        church = request.user.church
        if church is None:
            return Response(
                {'detail': 'UsuÃ¡rio sem congregaÃ§Ã£o vinculada.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        remittance = services.calc_regional_remittance(church, year, month)
        balance = services.build_monthly_balance(church, year, month)

        pdf = build_regional_pdf(church, year, month, remittance, balance)

        response = HttpResponse(
            pdf, content_type='application/pdf',
        )
        response['Content-Disposition'] = (
            f'attachment; filename="remessa-regional-{year}-{month:02d}.pdf"'
        )
        return response


def build_regional_pdf(church, year, month, remittance, balance) -> bytes:
    """Renderiza o relatÃ³rio regional em PDF usando xhtml2pdf."""
    from io import BytesIO
    from django.template.loader import render_to_string
    from django.utils import formats
    from xhtml2pdf import pisa

    dizimos = remittance['dizimos']
    offers = sorted(
        remittance['ofertas_departamentos'].values(),
        key=lambda o: o['categoria'],
    )

    context = {
        'church': church,
        'year': year,
        'month': month,
        'dizimos': dizimos,
        'offers': offers,
        'total_remittance': remittance['total_remittance'],
        'balance': balance,
        'month_name': formats.date_format(datetime(year, month, 1), 'F'),
    }

    html = render_to_string('finance/regional_report.html', context)
    result = BytesIO()
    pisa_status = pisa.CreatePDF(html, dest=result, encoding='utf-8')
    if pisa_status.err:
        raise RuntimeError('Erro ao gerar o PDF do relatÃ³rio regional.')
    return result.getvalue()


class CategoriesView(APIView):
    """Lista as categorias disponÃ­veis (para selects no frontend)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(CategorySerializer.many_categories())


def _get_admin_church(church_pk):
    """Resolve a igreja alvo para os endpoints admin (404 se nÃ£o existir)."""
    return get_object_or_404(Church, pk=church_pk)


class AdminChurchEntriesViewSet(viewsets.ModelViewSet):
    """CRUD de entradas para a igreja indicada na rota (apenas staff)."""

    serializer_class = FinancialEntrySerializer
    permission_classes = [IsStaffPermission]

    def _church(self):
        return _get_admin_church(self.kwargs['church_pk'])

    def get_queryset(self):
        qs = FinancialEntry.objects.filter(
            church=self._church(),
        ).order_by('-date', '-created_at')

        start = self.request.query_params.get('start_date')
        end = self.request.query_params.get('end_date')
        if start:
            qs = qs.filter(date__gte=start)
        if end:
            qs = qs.filter(date__lte=end)
        return qs

    def perform_create(self, serializer):
        serializer.save(church=self._church())


class AdminChurchExitsViewSet(viewsets.ModelViewSet):
    """CRUD de saÃ­das para a igreja indicada na rota (apenas staff)."""

    serializer_class = FinancialExitSerializer
    permission_classes = [IsStaffPermission]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def _church(self):
        return _get_admin_church(self.kwargs['church_pk'])

    def get_queryset(self):
        qs = FinancialExit.objects.filter(
            church=self._church(),
        ).order_by('-date', '-created_at')

        start = self.request.query_params.get('start_date')
        end = self.request.query_params.get('end_date')
        if start:
            qs = qs.filter(date__gte=start)
        if end:
            qs = qs.filter(date__lte=end)
        return qs

    def perform_create(self, serializer):
        serializer.save(church=self._church())


class AdminChurchDashboardView(APIView):
    permission_classes = [IsStaffPermission]

    def get(self, request, church_pk):
        year = _int_param(request, 'year', datetime.now().year)
        data = services.dashboard_summary(_get_admin_church(church_pk), year)
        return Response(data)


class AdminChurchImportView(APIView):
    permission_classes = [IsStaffPermission]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, church_pk):
        files = {}
        for key in ('entries', 'exits', 'tithers'):
            if key in request.FILES:
                files[key] = request.FILES[key]

        if not files:
            return Response(
                {'detail': 'Envie ao menos uma planilha (entries, exits ou tithers).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = services.import_spreadsheets(
            _get_admin_church(church_pk), files,
        )
        return Response(result, status=status.HTTP_200_OK)


class AdminChurchTithersReconciliationView(APIView):
    permission_classes = [IsStaffPermission]

    def get(self, request, church_pk):
        year = _int_param(request, 'year', datetime.now().year)
        month = _int_param(request, 'month', datetime.now().month)
        if not (1 <= month <= 12):
            return Response(
                {'detail': 'month deve estar entre 1 e 12.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = services.reconcile_tithers(_get_admin_church(church_pk), year, month)
        return Response(data)


class AdminChurchTithersMatrixView(APIView):
    permission_classes = [IsStaffPermission]

    def get(self, request, church_pk):
        year = _int_param(request, 'year', datetime.now().year)
        church = _get_admin_church(church_pk)

        tithers = (
            Tither.objects.filter(church=church)
            .order_by('name')
            .prefetch_related('tithe_records')
        )

        records = (
            TitheRecord.objects.filter(tither__church=church, year=year)
            .values('tither_id', 'month')
            .annotate(total=Sum('amount'))
        )
        by_tither: dict = {}
        for row in records:
            by_tither.setdefault(row['tither_id'], {})[row['month']] = (
                str(row['total'])
            )

        month_totals = [Decimal('0.00') for _ in range(12)]
        members = []
        grand_total = Decimal('0.00')

        for tither in tithers:
            months = [None] * 12
            member_total = Decimal('0.00')
            for month, amount in (by_tither.get(tither.id) or {}).items():
                months[month - 1] = amount
                d = Decimal(amount)
                member_total += d
                month_totals[month - 1] += d
            grand_total += member_total
            members.append(
                {
                    'id': tither.id,
                    'name': 'Dizimista AnÃ´nimo' if tither.is_anonymous else tither.name,
                    'is_anonymous': tither.is_anonymous,
                    'months': months,
                    'total': str(member_total),
                }
            )

        return Response(
            {
                'year': year,
                'grand_total': str(grand_total),
                'month_totals': [str(v) for v in month_totals],
                'members': members,
            }
        )


class AdminChurchMonthlyClosingsView(APIView):
    permission_classes = [IsStaffPermission]

    def get(self, request, church_pk):
        year = _int_param(request, 'year', datetime.now().year)
        church = _get_admin_church(church_pk)

        months = []
        grand = {
            'previous_balance': Decimal('0.00'),
            'total_entries': Decimal('0.00'),
            'total_exits': Decimal('0.00'),
            'final_balance': Decimal('0.00'),
        }
        for month in range(1, 13):
            closing, _ = services.get_or_create_monthly_closing(
                church, year, month
            )
            months.append(
                {
                    'year': year,
                    'month': month,
                    'is_closed': closing.is_closed,
                    'previous_balance': str(closing.previous_balance),
                    'total_entries': str(closing.total_entries),
                    'total_exits': str(closing.total_exits),
                    'final_balance': str(closing.final_balance),
                }
            )
            grand['total_entries'] += closing.total_entries
            grand['total_exits'] += closing.total_exits
            grand['final_balance'] += closing.final_balance

        return Response(
            {
                'year': year,
                'grand_total': {k: str(v) for k, v in grand.items()},
                'months': months,
            }
        )


class AdminChurchRegionalReportView(APIView):
    permission_classes = [IsStaffPermission]

    def get(self, request, church_pk):
        year = _int_param(request, 'year', datetime.now().year)
        month = _int_param(request, 'month', datetime.now().month)
        if not (1 <= month <= 12):
            return Response(
                {'detail': 'month deve estar entre 1 e 12.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        church = _get_admin_church(church_pk)
        remittance = services.calc_regional_remittance(church, year, month)
        balance = services.build_monthly_balance(church, year, month)
        closing, _ = services.get_or_create_monthly_closing(church, year, month)
        return Response({
            'remittance': remittance,
            'monthly_balance': balance,
            'monthly_closing': {
                'id': closing.id,
                'previous_balance': closing.previous_balance,
                'total_entries': closing.total_entries,
                'total_exits': closing.total_exits,
                'final_balance': closing.final_balance,
                'is_closed': closing.is_closed,
            },
        })


class AdminChurchRegionalReportPdfView(APIView):
    permission_classes = [IsStaffPermission]

    def get(self, request, church_pk):
        year = _int_param(request, 'year', datetime.now().year)
        month = _int_param(request, 'month', datetime.now().month)
        if not (1 <= month <= 12):
            return Response(
                {'detail': 'month deve estar entre 1 e 12.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        church = _get_admin_church(church_pk)
        remittance = services.calc_regional_remittance(church, year, month)
        balance = services.build_monthly_balance(church, year, month)

        pdf = build_regional_pdf(church, year, month, remittance, balance)

        response = HttpResponse(
            pdf, content_type='application/pdf',
        )
        response['Content-Disposition'] = (
            f'attachment; filename="remessa-regional-{year}-{month:02d}.pdf"'
        )
        return response


class AdminChurchCategoriesView(APIView):
    """Lista as categorias disponÃ­veis para selects (apenas staff)."""

    permission_classes = [IsStaffPermission]

    def get(self, request, church_pk):
        return Response(CategorySerializer.many_categories())
