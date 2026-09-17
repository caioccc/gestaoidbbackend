"""Views do mÃ³dulo financeiro (EclÃ©sia IDB)."""
import base64
import os
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from typing import Optional
from django.core.files.uploadedfile import SimpleUploadedFile

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Church, Member
from accounts.permissions import CanAccessTargetChurch, is_admin

from .models import (
    CalendarEvent,
    DepartmentCategory,
    FinancialEntry,
    FinancialExit,
    FinancialReceipt,
    MonthlyValidation,
    Tither,
    TitheRecord,
)
from .serializers import (
    CalendarEventSerializer,
    CategorySerializer,
    FinancialEntrySerializer,
    FinancialExitSerializer,
    FinancialReceiptSerializer,
    PublicCalendarEventSerializer,
    TitherSerializer,
)
from . import services


def _int_param(request, name, default):
    try:
        return int(request.query_params.get(name, default))
    except (TypeError, ValueError):
        return default


def _int_from_post(request, name, default):
    """Lê int de post-data (multipart) ou query-string, com fallback."""
    raw = None
    if hasattr(request, 'data') and name in request.data:
        raw = request.data.get(name)
    if raw is None and request.query_params.get(name) is not None:
        raw = request.query_params.get(name)
    try:
        return int(raw or default)
    except (TypeError, ValueError):
        return default


def _parse_competence(request):
    """Valida a competência (year/month) obrigatória da importação.

    Retorna (year, month) ou uma Response de erro (400).
    """
    year_raw = request.data.get('year')
    month_raw = request.data.get('month')
    if year_raw is None or year_raw == '' or month_raw is None or month_raw == '':
        return Response(
            {'detail': 'year e month (competência) são obrigatórios.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        year = int(year_raw)
        month = int(month_raw)
    except (TypeError, ValueError):
        return Response(
            {'detail': 'year e month devem ser números inteiros.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if month < 1 or month > 12 or year not in range(2000, 2101):
        return Response(
            {'detail': 'Competência inválida: ano (2000..2100) e mês (1..12).'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return year, month


def _validate_month(request) -> Response | None:
    """Valida o parâmetro month (1..12). Retorna None quando válido."""
    month = _int_param(request, 'month', 0)
    if not (1 <= month <= 12):
        return Response(
            {'detail': 'month deve estar entre 1 e 12.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


def _parse_mapping(request):
    """Lê o mapeamento de colunas (JSON string ou dict) do request."""
    raw = request.data.get('mapping')
    if raw in (None, ''):
        return None
    if isinstance(raw, str):
        try:
            import json  # noqa: PLC0415
            return json.loads(raw)
        except (ValueError, TypeError):
            return None
    return raw


def _cloudinary_url(field):
    """Resolve a URL acessível de um CloudinaryField (ou None)."""
    if not field:
        return None
    try:
        return getattr(field, 'url', None) or (field.name if field.name else None)
    except Exception:
        return field.name if getattr(field, 'name', None) else None


def _data_url_to_file(data_url, name):
    """Converte um data URL (ex.: data:image/png;base64,...) em Um UploadedFile.

    Utiliza SimpleUploadedFile (subclasse de UploadedFile) para que o
    CloudinaryField.pre_save reconheça o valor e envie a imagem ao Cloudinary
    durante o save() do modelo.
    """
    if not data_url or ',' not in data_url:
        return None
    try:
        header, b64 = data_url.split(',', 1)
        content = base64.b64decode(b64)
    except (ValueError, TypeError, base64.binascii.Error):
        return None
    if not content:
        return None
    content_type = header.split(';')[0].replace('data:', '') if 'data:' in header else 'image/png'
    return SimpleUploadedFile(name, content, content_type=content_type)


def _read_img(request, key, name):
    """Lê uma imagem (data URL base64) do request.data e devolve um UploadedFile."""
    return _data_url_to_file(request.data.get(key), name)


def _serialize_validation(record: Optional[MonthlyValidation]):
    """Payload serializado de uma validação mensal (ou None)."""
    if record is None:
        return None
    record.recompute_status()
    return {
        'id': record.id,
        'church': record.church_id,
        'year': record.year,
        'month': record.month,
        'status': record.status,
        'status_display': record.get_status_display(),
        'note': record.note,
        'approved_by_treasury': record.approved_by_treasury_id,
        'treasury_approved_at': record.treasury_approved_at,
        'rejected_by_treasury': record.rejected_by_treasury_id,
        'treasury_rejected_at': record.treasury_rejected_at,
        'approved_by_leadership': record.approved_by_leadership_id,
        'leadership_approved_at': record.leadership_approved_at,
        'rejected_by_leadership': record.rejected_by_leadership_id,
        'leadership_rejected_at': record.leadership_rejected_at,
        'treasury_photo_url': _cloudinary_url(record.treasury_photo_url),
        'treasury_signature_url': _cloudinary_url(record.treasury_signature_url),
        'signature_hash': record.signature_hash or None,
        'updated_at': record.updated_at,
    }


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


class TitherViewSet(viewsets.ModelViewSet):
    """CRUD de membros dizimistas do usuário (igreja vinculada)."""

    serializer_class = TitherSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Tither.objects.filter(
            church=self.request.user.church,
        ).order_by('name')

    def perform_create(self, serializer):
        serializer.save(church=self.request.user.church)


def _update_tither_tithe_records(tither, year, months):
    """Grava os 12 valores mensais de dízimo de um membro (update_or_create).

    Valor vazio/None deleta o registro do mês; caso contrário cria/atualiza.
    Retorna os 12 meses recalculados a partir do banco (autoritativo).
    """
    YEAR_MIN = 2000
    YEAR_MAX = 2100
    if not (YEAR_MIN <= int(year) <= YEAR_MAX):
        raise ValueError('year inválido.')
    if not isinstance(months, (list, tuple)) or len(months) != 12:
        raise ValueError('months deve ter exatamente 12 itens.')

    from decimal import Decimal

    result = []
    for i, raw in enumerate(months, start=1):
        if raw is None or raw == '':
            TitheRecord.objects.filter(
                tither=tither, year=year, month=i,
            ).delete()
            result.append(None)
            continue
        amount = Decimal(str(raw))
        if amount <= 0:
            raise ValueError(f'O valor do mês {i} deve ser maior que 0.')
        TitheRecord.objects.update_or_create(
            tither=tither,
            year=year,
            month=i,
            defaults={'amount': amount},
        )
        result.append(str(amount))
    return {'member_id': tither.id, 'year': year, 'months': result}


class TitherTitheRecordsView(APIView):
    """Grava os 12 valores mensais de dízimo de um membro do usuário."""

    permission_classes = [IsAuthenticated]

    def patch(self, request, tither_pk):
        tither = get_object_or_404(
            Tither, pk=tither_pk, church=request.user.church,
        )
        year = _int_from_post(request, 'year', datetime.now().year)
        months = request.data.get('months')
        try:
            result = _update_tither_tithe_records(tither, year, months)
        except ValueError as exc:
            return Response(
                {'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(result)


class DashboardSummaryView(APIView):
    """Resumo anual com totais e sÃ©rie temporal dos 12 meses."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        year = _int_param(request, 'year', datetime.now().year)
        data = services.dashboard_summary(request.user.church, year)
        return Response(data)


class ExistingMonthDataView(APIView):
    """Retorna quantos lançamentos já existem na competência (ano/mês).

    Usado na tela de importação para alertar o usuário/adm que, ao reimportar,
    os dados atuais do período serão SUBSTITUÍDOS pelos novos arquivos.
    GET ?year=2026&month=7
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        competence = _parse_competence(request)
        if isinstance(competence, Response):
            return competence
        year, month = competence
        counts = services.month_data_counts(request.user.church, year, month)
        return Response(counts, status=status.HTTP_200_OK)


class AdminChurchExistingMonthDataView(APIView):
    """Idem ExistingMonthDataView, porém para staff sobre a igreja da URL."""

    permission_classes = [CanAccessTargetChurch]

    def get(self, request, church_pk):
        competence = _parse_competence(request)
        if isinstance(competence, Response):
            return competence
        year, month = competence
        counts = services.month_data_counts(
            _get_admin_church(church_pk), year, month
        )
        return Response(counts, status=status.HTTP_200_OK)


class ImportSpreadsheetView(APIView):
    """Endpoint multipart para upload e processamento em lote das planilhas.

    Ano/Mês da competência são obrigatórios (`year`/`month`). Envie `dry_run=1`
    para apenas validar (prévia, sem gravar). Na gravação, havendo qualquer erro
    de validação nada é persistido (tudo-ou-nada) e a resposta vem como 400
    com os erros agrupados por arquivo.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        files = {}
        for key in ('entries', 'exits', 'tithers', 'caixa', 'relatorio'):
            if key in request.FILES:
                files[key] = request.FILES[key]

        if not any(k in files for k in ('entries', 'exits', 'tithers')):
            return Response(
                {'detail': 'Envie ao menos uma planilha (entries, exits ou tithers).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        competence = _parse_competence(request)
        if isinstance(competence, Response):
            return competence

        year, month = competence
        dry_run = str(request.data.get('dry_run', '')).lower() in ('1', 'true', 'yes')
        try:
            result = services.import_spreadsheets(
                request.user.church, files, year=year, month=month,
                dry_run=dry_run, mapping=_parse_mapping(request),
            )
        except services.ImportValidationError as exc:
            return Response(exc.result, status=status.HTTP_400_BAD_REQUEST)
        except (TypeError, ValueError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
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

        # Saldo acumulado no rodapé = Σ Entradas − Σ Saídas (não somar os
        # saldos mês a mês, pois o saldo anterior já é carregado entre meses).
        grand['final_balance'] = (
            grand['total_entries'] - grand['total_exits']
        )

        return Response(
            {
                'year': year,
                'grand_total': {k: str(v) for k, v in grand.items()},
                'months': months,
            }
        )

    def post(self, request):
        """Fechar ou reabrir a competência de um mês (Tesouraria)."""
        year = _int_param(request, 'year', datetime.now().year)
        raw_month = request.query_params.get('month')
        month = int(raw_month) if raw_month else None
        if month is None or not (1 <= month <= 12):
            return Response(
                {'detail': 'month deve estar entre 1 e 12.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        closed = request.data.get('closed')
        if not isinstance(closed, bool):
            return Response(
                {'detail': 'closed deve ser true ou false.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        church = request.user.church
        closing, _ = services.get_or_create_monthly_closing(church, year, month)
        closing.is_closed = closed
        closing.save(update_fields=['is_closed'])
        return Response(
            {
                'year': year,
                'month': month,
                'is_closed': closing.is_closed,
            }
        )


def _reject_if_congregation(church) -> Response | None:
    """Relatório Regional existe apenas para Sedes (guarda compartilhado)."""
    if church is None:
        return Response(
            {'detail': 'Usuário sem igreja vinculada.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not church.is_sede():
        return Response(
            {'detail': 'Congregações não possuem Relatório Regional.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


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
        church = request.user.church
        rejected = _reject_if_congregation(church)
        if rejected is not None:
            return rejected
        remittance = services.calc_regional_remittance(
            church, year, month,
        )
        balance = services.build_monthly_balance(
            request.user.church, year, month,
        )
        closing, _ = services.get_or_create_monthly_closing(
            request.user.church, year, month,
        )
        church = request.user.church
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
        rejected = _reject_if_congregation(church)
        if rejected is not None:
            return rejected

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
    permission_classes = [CanAccessTargetChurch]

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
    permission_classes = [CanAccessTargetChurch]
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


class AdminChurchTithersViewSet(viewsets.ModelViewSet):
    """CRUD de membros dizimistas para a igreja indicada na rota (staff)."""

    serializer_class = TitherSerializer
    permission_classes = [CanAccessTargetChurch]

    def _church(self):
        return _get_admin_church(self.kwargs['church_pk'])

    def get_queryset(self):
        return Tither.objects.filter(
            church=self._church(),
        ).order_by('name')

    def perform_create(self, serializer):
        serializer.save(church=self._church())


class AdminChurchTitherTitheRecordsView(APIView):
    """Grava os 12 valores mensais de dízimo de um membro (staff)."""

    permission_classes = [CanAccessTargetChurch]

    def patch(self, request, church_pk, tither_pk):
        church = _get_admin_church(church_pk)
        tither = get_object_or_404(Tither, pk=tither_pk, church=church)
        year = _int_from_post(request, 'year', datetime.now().year)
        months = request.data.get('months')
        try:
            result = _update_tither_tithe_records(tither, year, months)
        except ValueError as exc:
            return Response(
                {'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(result)


class AdminChurchDashboardView(APIView):
    permission_classes = [CanAccessTargetChurch]

    def get(self, request, church_pk):
        year = _int_param(request, 'year', datetime.now().year)
        data = services.dashboard_summary(_get_admin_church(church_pk), year)
        return Response(data)


class AdminChurchImportView(APIView):
    """Importação de planilhas (staff) — competência obrigatória, dry_run/commit."""

    permission_classes = [CanAccessTargetChurch]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, church_pk):
        files = {}
        for key in ('entries', 'exits', 'tithers', 'caixa', 'relatorio'):
            if key in request.FILES:
                files[key] = request.FILES[key]

        if not any(k in files for k in ('entries', 'exits', 'tithers')):
            return Response(
                {'detail': 'Envie ao menos uma planilha (entries, exits ou tithers).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        competence = _parse_competence(request)
        if isinstance(competence, Response):
            return competence

        year, month = competence
        dry_run = str(request.data.get('dry_run', '')).lower() in ('1', 'true', 'yes')
        try:
            result = services.import_spreadsheets(
                _get_admin_church(church_pk), files,
                year=year, month=month, dry_run=dry_run,
                mapping=_parse_mapping(request),
            )
        except services.ImportValidationError as exc:
            return Response(exc.result, status=status.HTTP_400_BAD_REQUEST)
        except (TypeError, ValueError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result, status=status.HTTP_200_OK)


class InspectSpreadsheetView(APIView):
    """Inspeciona uma planilha e devolve cabeçalhos + sugestão de mapeamento."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        kind = request.data.get('kind')
        if kind not in ('entries', 'exits', 'tithers'):
            return Response(
                {'detail': 'kind deve ser entries, exits ou tithers.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        file = request.FILES.get('file')
        if file is None:
            return Response(
                {'detail': 'Envie o arquivo (file).'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(services.inspect_spreadsheet(file, kind))


class AdminChurchInspectSpreadsheetView(APIView):
    """Inspeção (staff) de uma planilha para mapeamento de colunas."""

    permission_classes = [CanAccessTargetChurch]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, church_pk):
        _get_admin_church(church_pk)
        kind = request.data.get('kind')
        if kind not in ('entries', 'exits', 'tithers'):
            return Response(
                {'detail': 'kind deve ser entries, exits ou tithers.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        file = request.FILES.get('file')
        if file is None:
            return Response(
                {'detail': 'Envie o arquivo (file).'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(services.inspect_spreadsheet(file, kind))


class DreSummaryView(APIView):
    """DRE: receitas por departamento e despesas por natureza (mensal/anual)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        year = _int_param(request, 'year', datetime.now().year)
        raw_month = request.query_params.get('month')
        month = int(raw_month) if raw_month else None
        if month is not None and not (1 <= month <= 12):
            return Response(
                {'detail': 'month deve estar entre 1 e 12.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = services.build_dre_summary(request.user.church, year, month)
        return Response(data)


class TitherRepeatAuditView(APIView):
    """Auditoria de repetição de dizimistas (~90%) entre o mês e o anterior."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        year = _int_param(request, 'year', datetime.now().year)
        month = _int_param(request, 'month', datetime.now().month)
        invalid = _validate_month(request)
        if invalid:
            return invalid
        return Response(services.audit_tither_repeat(request.user.church, year, month))


class MonthlyValidationView(APIView):
    """Validação mensal da Rotina Contábil IDB — papel Tesouraria (igreja).

    GET retorna o checklist calculado ao vivo + o registro de validação.
    POST: action='approve' (exige competência fechada) ou 'reject'.
    A aprovação da Tesouraria é independente da Liderança.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        year = _int_param(request, 'year', datetime.now().year)
        month = _int_param(request, 'month', datetime.now().month)
        invalid = _validate_month(request)
        if invalid:
            return invalid
        church = request.user.church
        record = MonthlyValidation.objects.filter(
            church=church, year=year, month=month,
        ).first()
        return Response({
            'year': year,
            'month': month,
            'checks': services.build_validation_checks(church, year, month),
            'validation': _serialize_validation(record),
        })

    def post(self, request):
        year = _int_param(request, 'year', datetime.now().year)
        month = _int_param(request, 'month', datetime.now().month)
        invalid = _validate_month(request)
        if invalid:
            return invalid
        church = request.user.church
        action = request.data.get('action')
        note = str(request.data.get('note') or '').strip()
        checks = services.build_validation_checks(church, year, month)
        record, _ = MonthlyValidation.objects.get_or_create(
            church=church, year=year, month=month,
        )

        photo = _read_img(request, 'photo', 'treasury_photo.png')
        signature = _read_img(request, 'signature', 'treasury_signature.png')

        if action == 'approve' or action == 'submit_treasury':
            if not checks['closing']['is_closed']:
                return Response(
                    {'detail': 'A competência precisa estar fechada (Caixa IDB) antes da validação.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            record.checks = checks
            record.note = note
            record.approved_by_treasury = request.user
            record.treasury_approved_at = datetime.now()
            record.rejected_by_treasury = None
            record.treasury_rejected_at = None
            if photo:
                record.treasury_photo_url = photo
            if signature:
                record.treasury_signature_url = signature
            record.recompute_status()
            record.save()
            return Response(_serialize_validation(record))
        if action == 'reject':
            record.checks = checks
            record.note = note
            record.rejected_by_treasury = request.user
            record.treasury_rejected_at = datetime.now()
            record.approved_by_treasury = None
            record.treasury_approved_at = None
            if photo:
                record.treasury_photo_url = photo
            if signature:
                record.treasury_signature_url = signature
            record.recompute_status()
            record.save()
            return Response(_serialize_validation(record))
        return Response(
            {'detail': "action deve ser 'approve' ou 'reject'."},
            status=status.HTTP_400_BAD_REQUEST,
        )


class AdminChurchDreSummaryView(APIView):
    """DRE por natureza — papel Liderança (staff)."""

    permission_classes = [CanAccessTargetChurch]

    def get(self, request, church_pk):
        year = _int_param(request, 'year', datetime.now().year)
        raw_month = request.query_params.get('month')
        month = int(raw_month) if raw_month else None
        if month is not None and not (1 <= month <= 12):
            return Response(
                {'detail': 'month deve estar entre 1 e 12.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(services.build_dre_summary(
            _get_admin_church(church_pk), year, month,
        ))


class AdminChurchTitherRepeatAuditView(APIView):
    """Repetição de dizimistas (~90%) — papel Liderança (staff)."""

    permission_classes = [CanAccessTargetChurch]

    def get(self, request, church_pk):
        year = _int_param(request, 'year', datetime.now().year)
        month = _int_param(request, 'month', datetime.now().month)
        invalid = _validate_month(request)
        if invalid:
            return invalid
        return Response(services.audit_tither_repeat(
            _get_admin_church(church_pk), year, month,
        ))


class AdminChurchMonthlyValidationView(APIView):
    """Validação mensal — papel Liderança (staff).

    GET: checklist + registro. POST: action='approve' (exige competência
    fechada) ou 'reject'. A aprovação da Liderança é independente da
    Tesouraria.
    """

    permission_classes = [CanAccessTargetChurch]

    def get(self, request, church_pk):
        year = _int_param(request, 'year', datetime.now().year)
        month = _int_param(request, 'month', datetime.now().month)
        invalid = _validate_month(request)
        if invalid:
            return invalid
        church = _get_admin_church(church_pk)
        record = MonthlyValidation.objects.filter(
            church=church, year=year, month=month,
        ).first()
        return Response({
            'year': year,
            'month': month,
            'checks': services.build_validation_checks(church, year, month),
            'validation': _serialize_validation(record),
        })

    def post(self, request, church_pk):
        year = _int_param(request, 'year', datetime.now().year)
        month = _int_param(request, 'month', datetime.now().month)
        invalid = _validate_month(request)
        if invalid:
            return invalid
        church = _get_admin_church(church_pk)
        action = request.data.get('action')
        note = str(request.data.get('note') or '').strip()
        checks = services.build_validation_checks(church, year, month)
        record = MonthlyValidation.objects.filter(
            church=church, year=year, month=month,
        ).first()

        photo = _read_img(request, 'photo', 'treasury_photo.png')
        signature = _read_img(request, 'signature', 'treasury_signature.png')

        if action == 'approve' or action == 'submit_leadership':
            if not checks['closing']['is_closed']:
                return Response(
                    {'detail': 'A competência precisa estar fechada (Caixa IDB) antes da validação.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if record is None:
                record = MonthlyValidation.objects.create(
                    church=church, year=year, month=month,
                )
            record.checks = checks
            record.note = note
            record.approved_by_treasury = request.user
            record.treasury_approved_at = datetime.now()
            record.approved_by_leadership = request.user
            record.leadership_approved_at = datetime.now()
            record.rejected_by_treasury = None
            record.treasury_rejected_at = None
            record.rejected_by_leadership = None
            record.leadership_rejected_at = None
            if photo:
                record.treasury_photo_url = photo
            if signature:
                record.treasury_signature_url = signature
            record.recompute_status()
            record.save()
            return Response(_serialize_validation(record))
        if action == 'reject':
            if record is None:
                record = MonthlyValidation.objects.create(
                    church=church, year=year, month=month,
                )
            record.checks = checks
            record.note = note
            record.rejected_by_treasury = request.user
            record.treasury_rejected_at = datetime.now()
            record.rejected_by_leadership = request.user
            record.leadership_rejected_at = datetime.now()
            record.approved_by_treasury = None
            record.treasury_approved_at = None
            record.approved_by_leadership = None
            record.leadership_approved_at = None
            if photo:
                record.treasury_photo_url = photo
            if signature:
                record.treasury_signature_url = signature
            record.recompute_status()
            record.save()
            return Response(_serialize_validation(record))
        return Response(
            {'detail': "action deve ser 'approve' ou 'reject'."},
            status=status.HTTP_400_BAD_REQUEST,
        )


class AdminChurchTithersReconciliationView(APIView):
    permission_classes = [CanAccessTargetChurch]

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
    permission_classes = [CanAccessTargetChurch]

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
    permission_classes = [CanAccessTargetChurch]

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

        # Saldo acumulado no rodapé = Σ Entradas − Σ Saídas.
        grand['final_balance'] = (
            grand['total_entries'] - grand['total_exits']
        )

        return Response(
            {
                'year': year,
                'grand_total': {k: str(v) for k, v in grand.items()},
                'months': months,
            }
        )

    def post(self, request, church_pk):
        """Fechar ou reabrir a competência de um mês (Liderança)."""
        year = _int_param(request, 'year', datetime.now().year)
        raw_month = request.query_params.get('month')
        month = int(raw_month) if raw_month else None
        if month is None or not (1 <= month <= 12):
            return Response(
                {'detail': 'month deve estar entre 1 e 12.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        closed = request.data.get('closed')
        if not isinstance(closed, bool):
            return Response(
                {'detail': 'closed deve ser true ou false.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        church = _get_admin_church(church_pk)
        closing, _ = services.get_or_create_monthly_closing(church, year, month)
        closing.is_closed = closed
        closing.save(update_fields=['is_closed'])
        return Response(
            {
                'year': year,
                'month': month,
                'is_closed': closing.is_closed,
            }
        )


class AdminChurchRegionalReportView(APIView):
    permission_classes = [CanAccessTargetChurch]

    def get(self, request, church_pk):
        year = _int_param(request, 'year', datetime.now().year)
        month = _int_param(request, 'month', datetime.now().month)
        if not (1 <= month <= 12):
            return Response(
                {'detail': 'month deve estar entre 1 e 12.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        church = _get_admin_church(church_pk)
        rejected = _reject_if_congregation(church)
        if rejected is not None:
            return rejected
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
    permission_classes = [CanAccessTargetChurch]

    def get(self, request, church_pk):
        year = _int_param(request, 'year', datetime.now().year)
        month = _int_param(request, 'month', datetime.now().month)
        if not (1 <= month <= 12):
            return Response(
                {'detail': 'month deve estar entre 1 e 12.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        church = _get_admin_church(church_pk)
        rejected = _reject_if_congregation(church)
        if rejected is not None:
            return rejected
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

    permission_classes = [CanAccessTargetChurch]

    def get(self, request, church_pk):
        return Response(CategorySerializer.many_categories())


def _role(user) -> Optional[str]:
    """Papel do usuário na igreja ativa (None se admin ou sem igreja)."""
    if not (user and user.is_authenticated) or user.church is None:
        return None
    return user.get_role_for(user.church)


def _can_manage_finance(user) -> bool:
    """TESOUREIRO, PASTOR ou ADMIN controlam eventos FINANCE."""
    if is_admin(user):
        return True
    return _role(user) in ('TESOUREIRO', 'PASTOR')


def _can_manage_general(user) -> bool:
    """SECRETARIA, PASTOR ou ADMIN controlam eventos GENERAL (agenda)."""
    if is_admin(user):
        return True
    return _role(user) in ('SECRETARIA', 'PASTOR')


def _can_edit_event(user, event) -> bool:
    """Regras de edição/exclusão por audiência do evento."""
    if event.audience == CalendarEvent.Audience.FINANCE:
        return _can_manage_finance(user)
    return _can_manage_general(user)


def _default_audience_for(user) -> str:
    """Audiência imposta na criação conforme o papel do criador."""
    if is_admin(user):
        return ''  # ADMIN escolhe
    role = _role(user)
    if role == 'TESOUREIRO':
        return CalendarEvent.Audience.FINANCE
    if role == 'SECRETARIA':
        return CalendarEvent.Audience.GENERAL
    return ''  # PASTOR escolhe


class CalendarEventViewSet(viewsets.ModelViewSet):
    """Calendário geral da igreja ativa do usuário.

    TESOUREIRO/PASTOR/ADMIN enxergam todos os eventos (financeiros e gerais) e
    controlam os financeiros. SECRETARIA enxerga apenas os gerais (agenda da
    igreja) e os controla. O tesoureiro lê a agenda, mas não a edita.
    """

    serializer_class = CalendarEventSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        user = self.request.user
        qs = CalendarEvent.objects.filter(church=user.church)
        if _can_manage_finance(user):
            return qs.select_related('created_by').order_by('title')
        return qs.filter(
            audience=CalendarEvent.Audience.GENERAL
        ).select_related('created_by').order_by('title')

    def _check_can_edit(self, event):
        if not _can_edit_event(self.request.user, event):
            raise PermissionDenied(
                'Você não pode editar/excluir este evento.'
            )

    def perform_create(self, serializer):
        user = self.request.user
        if user.church is None:
            raise ValidationError({
                'detail': 'Você não possui uma igreja ativa. Selecione uma igreja '
                          'no perfil antes de criar eventos.'
            })
        audience = serializer.validated_data.get('audience', '')
        forced = _default_audience_for(user)
        if forced:
            audience = forced
        if audience not in CalendarEvent.Audience.values:
            audience = CalendarEvent.Audience.GENERAL
        member_ids = serializer.validated_data.pop('members', None)
        self._validate_members(user.church, member_ids)
        event = serializer.save(
            church=user.church,
            created_by=user,
            audience=audience,
        )
        if member_ids:
            event.members.set(member_ids)

    def perform_update(self, serializer):
        user = self.request.user
        event = self.get_object()
        self._check_can_edit(event)
        serializer.validated_data.pop('audience', None)
        member_ids = serializer.validated_data.pop('members', None)
        if member_ids is not None:
            self._validate_members(user.church, member_ids)
        updated = serializer.save(audience=event.audience)
        if member_ids is not None:
            updated.members.set(member_ids)

    def perform_destroy(self, instance):
        self._check_can_edit(instance)
        instance.delete()

    @staticmethod
    def _validate_members(church, members):
        ids = []
        for m in (members or []):
            ids.append(m.id if hasattr(m, 'id') else int(m))
        if not ids:
            return
        if Member.objects.filter(pk__in=ids, church=church).count() != len(set(ids)):
            raise ValidationError({
                'members': 'Membros inválidos para esta igreja.',
            })


class AdminChurchCalendarEventsViewSet(viewsets.ModelViewSet):
    """CRUD de eventos do calendário de uma igreja específica (apenas staff)."""

    serializer_class = CalendarEventSerializer
    permission_classes = [CanAccessTargetChurch]
    pagination_class = None

    def _church(self):
        return _get_admin_church(self.kwargs['church_pk'])

    def get_queryset(self):
        return CalendarEvent.objects.filter(
            church=self._church()
        ).select_related('created_by').order_by('title')

    def perform_create(self, serializer):
        serializer.save(church=self._church(), created_by=self.request.user)


class PublicCalendarEventsView(APIView):
    """Eventos gerais (agenda) da igreja via hash público — sem autenticação.

    Nunca expõe dados de membros nem autores. Acesso pela URL pública
    /calendar/<hash> da igreja.
    """

    permission_classes = [AllowAny]

    def get(self, request, hash):
        church = Church.objects.filter(calendar_public_hash=hash).first()
        if church is None:
            return Response(
                {'detail': 'Calendário não encontrado.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        qs = CalendarEvent.objects.filter(
            church=church,
            audience=CalendarEvent.Audience.GENERAL,
        )
        return Response({
            'church': {
                'id': church.id,
                'name': church.name,
            },
            'events': PublicCalendarEventSerializer(qs, many=True).data,
        })


# --------------------------------------------------------------------------- #
# Export de planilhas preenchidas no modelo (Entradas/Saídas) e Fechamento
# --------------------------------------------------------------------------- #
XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _xlsx_file_response(data: bytes, filename: str) -> FileResponse:
    """Empacota bytes .xlsx como resposta de download anexa."""
    response = FileResponse(
        BytesIO(data), content_type=XLSX_MIME, as_attachment=True,
        filename=filename,
    )
    return response


def _export_competence(request, require_month: bool = True):
    """Lê year/month da query; valida a competência."""
    year = _int_param(request, 'year', datetime.now().year)
    raw_month = request.query_params.get('month')
    month = int(raw_month) if raw_month else None
    if require_month and (month is None or not (1 <= month <= 12)):
        return None, None, Response(
            {'detail': 'month deve estar entre 1 e 12.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return year, month, None


class ExportEntriesView(APIView):
    """Baixa o modelo de Entradas preenchido com o mês da competência."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        year, month, err = _export_competence(request)
        if err:
            return err
        data = services.generate_filled_entries(request.user.church, year, month)
        return _xlsx_file_response(
            data, f'entradas-{year}-{month:02d}.xlsx'
        )


class ExportExitsView(APIView):
    """Baixa o modelo de Saídas preenchido com o mês da competência."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        year, month, err = _export_competence(request)
        if err:
            return err
        data = services.generate_filled_exits(request.user.church, year, month)
        return _xlsx_file_response(
            data, f'saidas-{year}-{month:02d}.xlsx'
        )


class ExportClosingsView(APIView):
    """Baixa o export do Fechamento Mensal (anual ou por competência)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        mode = request.query_params.get('mode', 'annual')
        year = _int_param(request, 'year', datetime.now().year)
        month = None
        if mode == 'period':
            year, month, err = _export_competence(request, require_month=True)
            if err:
                return err
        data = services.generate_closings_export(
            request.user.church, year, mode, month=month
        )
        filename = (
            f'fechamento-{year}.xlsx'
            if mode == 'annual'
            else f'fechamento-{year}-{month:02d}.xlsx'
        )
        return _xlsx_file_response(data, filename)


class AdminChurchExportEntriesView(APIView):
    """Variante admin do export de Entradas para uma igreja específica."""

    permission_classes = [CanAccessTargetChurch]

    def get(self, request, church_pk):
        church = _get_admin_church(church_pk)
        year, month, err = _export_competence(request)
        if err:
            return err
        data = services.generate_filled_entries(church, year, month)
        return _xlsx_file_response(
            data, f'entradas-{year}-{month:02d}.xlsx'
        )


class AdminChurchExportExitsView(APIView):
    """Variante admin do export de Saídas para uma igreja específica."""

    permission_classes = [CanAccessTargetChurch]

    def get(self, request, church_pk):
        church = _get_admin_church(church_pk)
        year, month, err = _export_competence(request)
        if err:
            return err
        data = services.generate_filled_exits(church, year, month)
        return _xlsx_file_response(
            data, f'saidas-{year}-{month:02d}.xlsx'
        )


class AdminChurchExportClosingsView(APIView):
    """Variante admin do export do Fechamento Mensal."""

    permission_classes = [CanAccessTargetChurch]

    def get(self, request, church_pk):
        church = _get_admin_church(church_pk)
        mode = request.query_params.get('mode', 'annual')
        year = _int_param(request, 'year', datetime.now().year)
        month = None
        if mode == 'period':
            year, month, err = _export_competence(request, require_month=True)
            if err:
                return err
        data = services.generate_closings_export(church, year, mode, month=month)
        filename = (
            f'fechamento-{year}.xlsx'
            if mode == 'annual'
            else f'fechamento-{year}-{month:02d}.xlsx'
        )
        return _xlsx_file_response(data, filename)


XLS_MIME = 'application/vnd.ms-excel'


def _xls_file_response(data: bytes, filename: str) -> HttpResponse:
    """Empacota bytes .xls (caixa IDB) como resposta de download anexa."""
    response = HttpResponse(data, content_type=XLS_MIME)
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


class AdminChurchCaixaDownloadView(APIView):
    """Baixa o Caixa IDB (Balanço Local) preenchido (.xls) para uma igreja."""

    permission_classes = [CanAccessTargetChurch]

    def get(self, request, church_pk):
        church = _get_admin_church(church_pk)
        year, month, err = _export_competence(request)
        if err:
            return err
        data = services.generate_filled_caixa(church, year, month)
        return _xls_file_response(data, f'caixa-idb-{year}-{month:02d}.xls')


# --------------------------------------------------------------------------- #
# Recibos Financeiros (saída/pagamento e entrada/doação) — PDF A4 em 2 vias.
# --------------------------------------------------------------------------- #
def _brl(value: Decimal) -> str:
    """Formata um Decimal como moeda brasileira: R$ 1.850,00."""
    formatted = f'{value:,.2f}'
    return f'R$ {formatted.replace(",", "@").replace(".", ",").replace("@", ".")}'


def next_receipt_number(church, year: int) -> int:
    """Próximo número sequencial do ano (recalculado do máximo existente).

    Como a numeração é recalculada a partir do máximo já gravado e há a
    constraint única (church, year, number), saltos e duplicidades são
    evitados mesmo sob concorrência (transação atômica no create).
    """
    last = (
        FinancialReceipt.objects.filter(church=church, year=year)
        .order_by('-number')
        .first()
    )
    return (last.number + 1) if last else 1


def build_receipt_pdf(receipt) -> bytes:
    """Renderiza o recibo em PDF A4 (2 vias) via xhtml2pdf."""
    from django.template.loader import render_to_string
    from xhtml2pdf import pisa

    from .amount_extenso import data_por_extenso, valor_por_extenso

    church = receipt.church
    amount_brl = _brl(receipt.amount)
    amount_extenso = valor_por_extenso(receipt.amount)

    # Cabeçalho institucional (endereço da igreja).
    endereco = []
    if church.street:
        street = church.street
        if church.number:
            street += f', {church.number}'
        endereco.append(street)
    if church.neighborhood:
        endereco.append(church.neighborhood)
    localidade = ''
    if church.city:
        localidade = f'{church.city} - {church.state}'
        if church.cep:
            localidade += f' - CEP {church.cep}'
    church_address = ', '.join(e for e in [', '.join(endereco), localidade] if e)

    # Cláusulas de identificação do favorecido/doador.
    document_clause = ''
    if receipt.favored_document:
        document_clause = f', {receipt.favored_document}'
    if receipt.favored_rg:
        document_clause += f', RG {receipt.favored_rg}'
    location_clause = ''
    if receipt.favored_city:
        location = receipt.favored_city
        if receipt.favored_state:
            location += f'/{receipt.favored_state}'
        location_clause = f', residente em {location}'

    if receipt.receipt_type == FinancialReceipt.Type.SAIDA:
        title_text = 'RECIBO DE PAGAMENTO / SAÍDA'
        body_line_1 = (
            f'Recebi de {receipt.favored_name}{document_clause}{location_clause}, '
            f'a importância de {amount_brl} ({amount_extenso}).'
        )
        signature_label_left = 'FAVORECIDO(A) / PRESTADOR(A)'
    else:
        title_text = 'RECIBO DE DOAÇÃO / ENTRADA'
        church_name = church.name or 'a igreja'
        body_line_1 = (
            f'{church_name} recebeu de {receipt.favored_name}'
            f'{document_clause}{location_clause}, '
            f'a importância de {amount_brl} ({amount_extenso}).'
        )
        signature_label_left = 'DOADOR(A) / COLABORADOR(A)'

    body_line_2 = 'Referente a: {}.'.format(receipt.description or '-')

    date_extenso = data_por_extenso(receipt.date)
    city_date = f'{church.city}, {date_extenso}' if church.city else date_extenso

    context = {
        'church': church,
        'receipt': receipt,
        'receipt_number': receipt.full_number,
        'title_text': title_text,
        'amount': amount_brl,
        'amount_extenso': amount_extenso,
        'body_line_1': body_line_1,
        'body_line_2': body_line_2,
        'city_date': city_date,
        'church_address': church_address.upper(),
        'pastor_name': church.pastor_name or '-',
        'treasurer_name': church.treasurer_name or '-',
        'signature_label_left': signature_label_left,
        'signature_label_right': 'TESOUREIRO(A) / PASTOR(A)',
    }

    html = render_to_string('finance/receipt_pdf.html', context)
    result = BytesIO()
    pisa_status = pisa.CreatePDF(html, dest=result, encoding='utf-8')
    if pisa_status.err:
        raise RuntimeError('Erro ao gerar o PDF do recibo financeiro.')
    return result.getvalue()


class BaseFinancialReceiptViewSet(viewsets.ModelViewSet):
    """CRUD de recibos financeiros com numeração anual sequencial por igreja.

    Apenas TESOUREIRO/PASTOR/ADMIN operam recibos (SECRETARIA não enxerga).
    Meses fechados no Caixa IDB tornam o recibo somente leitura (exceto
    admin/staff) — reimpressão/download permanece disponível.
    """

    serializer_class = FinancialReceiptSerializer
    permission_classes = [IsAuthenticated]

    def _church(self):
        raise NotImplementedError

    def get_queryset(self):
        user = self.request.user
        if not _can_manage_finance(user):
            raise PermissionDenied('Acesso restrito à Tesouraria/Pastorado.')
        qs = FinancialReceipt.objects.filter(church=self._church())
        params = self.request.query_params
        year = params.get('year')
        if year and str(year).isdigit():
            qs = qs.filter(year=int(str(year)))
        rtype = params.get('receipt_type')
        if rtype in FinancialReceipt.Type.values:
            qs = qs.filter(receipt_type=rtype)
        search = (params.get('search') or '').strip()
        if search:
            qs = qs.filter(
                Q(favored_name__icontains=search)
                | Q(favored_document__icontains=search)
                | Q(description__icontains=search)
            )
        return qs.select_related('member', 'entry', 'exit').order_by('-year', '-number')

    def _check_locked(self, receipt):
        if is_admin(self.request.user):
            return
        if receipt.is_locked():
            raise PermissionDenied(
                'O mês deste recibo está fechado no Caixa IDB. '
                'Apenas download/reimpressão são permitidos.'
            )

    def _validate_links(self, church, attrs):
        member = attrs.get('member')
        if member is not None and member.church_id != church.id:
            raise ValidationError({'member': 'O membro deve pertencer a esta igreja.'})
        entry = attrs.get('entry')
        if entry is not None and entry.church_id != church.id:
            raise ValidationError({'entry': 'A entrada deve pertencer a esta igreja.'})
        exit_record = attrs.get('exit')
        if exit_record is not None and exit_record.church_id != church.id:
            raise ValidationError({'exit': 'A saída deve pertencer a esta igreja.'})

    @staticmethod
    def _refresh_pdf(receipt):
        from django.core.files.base import ContentFile
        payload = build_receipt_pdf(receipt)
        filename = f'recibo-{receipt.full_number}.pdf'
        if receipt.pdf and receipt.pdf.name:
            receipt.pdf.delete(save=False)
        receipt.pdf.save(filename, ContentFile(payload))
        receipt.save(update_fields=['pdf'])

    @staticmethod
    def _auto_launch(receipt):
        """Lança automaticamente o movimento no caixa do mês (entrada/saída)."""
        category = receipt.category or DepartmentCategory.ESPECIAL
        if receipt.receipt_type == FinancialReceipt.Type.SAIDA and not receipt.exit_id:
            exit_record = FinancialExit.objects.create(
                church=receipt.church,
                date=receipt.date,
                description=receipt.description[:200],
                category=category,
                amount=receipt.amount,
            )
            receipt.exit = exit_record
            receipt.save(update_fields=['exit'])
        elif receipt.receipt_type == FinancialReceipt.Type.ENTRADA and not receipt.entry_id:
            entry_record = FinancialEntry.objects.create(
                church=receipt.church,
                date=receipt.date,
                service_description=receipt.description[:150],
                category=category,
                amount=receipt.amount,
            )
            receipt.entry = entry_record
            receipt.save(update_fields=['entry'])

    def perform_create(self, serializer):
        church = self._church()
        user = self.request.user
        try:
            with transaction.atomic():
                self._validate_links(church, serializer.validated_data)
                auto_launch = bool(
                    serializer.validated_data.pop('auto_launch', False)
                )
                receipt_date = serializer.validated_data['date']
                year = receipt_date.year
                number = next_receipt_number(church, year)
                receipt = serializer.save(
                    church=church,
                    year=year,
                    number=number,
                    created_by=user,
                    auto_launched=auto_launch,
                )
                self._refresh_pdf(receipt)
                if auto_launch:
                    self._auto_launch(receipt)
        except IntegrityError:
            raise ValidationError(
                {'detail': 'Não foi possível gerar o número do recibo. '
                           'Tente novamente.'}
            ) from IntegrityError

    def perform_update(self, serializer):
        receipt = self.get_object()
        self._check_locked(receipt)
        church = self._church()
        self._validate_links(church, serializer.validated_data)
        serializer.validated_data.pop('auto_launch', None)
        serializer.save()
        self._refresh_pdf(receipt)

    def perform_destroy(self, instance):
        self._check_locked(instance)
        if instance.pdf and instance.pdf.name:
            instance.pdf.delete(save=False)
        instance.delete()

    @action(detail=True, methods=['get'], url_path='pdf')
    def download_pdf(self, request, pk=None):
        """Download/reimpressão do PDF emitido (2 vias)."""
        receipt = self.get_object()
        if not receipt.pdf or not receipt.pdf.name:
            return Response(
                {'detail': 'Este recibo não possui PDF emitido.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        filename = os.path.basename(
            receipt.pdf.name
        ) or f'recibo-{receipt.full_number}.pdf'
        return FileResponse(
            receipt.pdf.open('rb'),
            as_attachment=True,
            filename=filename,
        )


class FinancialReceiptViewSet(BaseFinancialReceiptViewSet):
    """Recibos da igreja ativa do usuário (Tesouraria/Pastor)."""

    def _church(self):
        return self.request.user.church


class AdminChurchReceiptViewSet(BaseFinancialReceiptViewSet):
    """Recibos de uma igreja específica (apenas staff)."""

    permission_classes = [CanAccessTargetChurch]

    def _church(self):
        return _get_admin_church(self.kwargs['church_pk'])
