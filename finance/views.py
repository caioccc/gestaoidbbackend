from datetime import datetime
from io import BytesIO

from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Church
from accounts.permissions import CanAccessTargetChurch

from . import services

# Mapa de chave de template -> nome de arquivo no diretório oficial.
TEMPLATE_FILES = {
    'entries': 'modelo_entradas.xlsx',
    'exits': 'modelo_saidas.xlsx',
    'tithers': 'modelo_membros_dizimistas.xls',
    'caixa': 'modelo_caixa.xls',
    'relatorio': 'modelo_relatorio.xls',
}

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
XLS_MIME = 'application/vnd.ms-excel'


def _int_param(request, name, default):
    raw = request.query_params.get(name)
    if raw is None or raw == '':
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _month_valid(request, response):
    """Valida year/month da query string; retorna (year, month, erro_response)."""
    year = _int_param(request, 'year', datetime.now().year)
    month = _int_param(request, 'month', datetime.now().month)
    if not (1 <= month <= 12):
        return year, month, Response(
            {'detail': 'month deve estar entre 1 e 12.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return year, month, None


class TemplateDownloadView(APIView):
    """Baixa o modelo oficial (vazio) de uma das planilhas IDB."""

    permission_classes = [IsAuthenticated]

    def get(self, request, template_key):
        filename = TEMPLATE_FILES.get(template_key)
        if filename is None:
            return Response(
                {'detail': 'Template desconhecido.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        path = services.TEMPLATES_DIR / filename
        if not path.exists():
            return Response(
                {'detail': 'Template não encontrado no servidor.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        content_type = (
            XLSX_MIME if filename.endswith('.xlsx') else XLS_MIME
        )
        response = HttpResponse(
            path.read_bytes(), content_type=content_type,
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


class RegionalReportXlsView(APIView):
    """Baixa o Relatório Regional preenchido (.xls) para o mês."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        church = request.user.church
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
        year, month, err = _month_valid(request, None)
        if err:
            return err
        xls = services.generate_filled_regional_report(church, year, month)
        response = HttpResponse(xls, content_type=XLS_MIME)
        response['Content-Disposition'] = (
            f'attachment; filename="relatorio-regional-{year}-{month:02d}.xls"'
        )
        return response


class CaixaDownloadView(APIView):
    """Baixa o Caixa IDB (Balanço Local) preenchido (.xls) para o mês."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        church = request.user.church
        if church is None:
            return Response(
                {'detail': 'Usuário sem igreja vinculada.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        year, month, err = _month_valid(request, None)
        if err:
            return err
        xls = services.generate_filled_caixa(church, year, month)
        response = HttpResponse(xls, content_type=XLS_MIME)
        response['Content-Disposition'] = (
            f'attachment; filename="caixa-idb-{year}-{month:02d}.xls"'
        )
        return response


class NationalReportXlsxView(APIView):
    """Baixa o Relatório Nacional preenchido (.xlsx / openpyxl) do mês."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        church = request.user.church
        if church is None:
            return Response(
                {'detail': 'Usuário sem igreja vinculada.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        year, month, err = _month_valid(request, None)
        if err:
            return err
        xlsx = services.generate_filled_national_report(church, year, month)
        response = FileResponse(
            BytesIO(xlsx), content_type=XLSX_MIME, as_attachment=True,
            filename=f'relatorio_nacional_{month:02d}_{year}.xlsx',
        )
        return response


class AdminChurchNationalReportXlsxView(APIView):
    """Baixa o Relatório Nacional preenchido (.xlsx) de uma igreja (staff)."""

    permission_classes = [CanAccessTargetChurch]

    def get(self, request, church_pk):
        church = get_object_or_404(Church, pk=church_pk)
        year, month, err = _month_valid(request, None)
        if err:
            return err
        xlsx = services.generate_filled_national_report(church, year, month)
        response = FileResponse(
            BytesIO(xlsx), content_type=XLSX_MIME, as_attachment=True,
            filename=f'relatorio_nacional_{month:02d}_{year}.xlsx',
        )
        return response
