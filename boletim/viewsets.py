"""Views do app boletim (Folder / Boletim)."""
from django.utils.dateparse import parse_date
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsChurchRole
from accounts.services import cloudinary_url
from finance.models import CalendarEvent

from . import services
from .models import FolderCulto, FolderCultoRow
from .serializers import FolderCultoSerializer


class FolderCultoPagination(PageNumberPagination):
    """Paginação dos folders (ativa com `?paginate=1`)."""

    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


class FolderPaginationMixin:
    """Paginação por demanda: sem `?paginate=1` a resposta é lista plana."""

    pagination_class = None
    paginated_pagination_class = FolderCultoPagination

    def list(self, request, *args, **kwargs):
        if request.query_params.get('paginate') == '1':
            self.pagination_class = self.paginated_pagination_class
        return super().list(request, *args, **kwargs)


_ALLOWED_ORDERINGS = {
    'reference_start', '-reference_start',
    'created_at', '-created_at',
    'title', '-title',
    'tipo', '-tipo',
}


def _int_param(params, name):
    raw = (params.get(name) or '').strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _period_from_data(data):
    """Lê `reference_start`/`reference_end` (ISO) e valida a janela."""
    start = parse_date((data.get('reference_start') or '').strip()[:10])
    end = parse_date((data.get('reference_end') or '').strip()[:10])
    if not start or not end:
        raise ValidationError({'reference_start': 'Período inválido.'})
    if end < start:
        raise ValidationError({'reference_end': 'Data final anterior à inicial.'})
    return start, end


class FolderCultoViewSet(FolderPaginationMixin, viewsets.ModelViewSet):
    """CRUD de folders da igreja ativa (PASTOR / SECRETARIA / TESOUREIRO).

    Filtros:
    - `?tipo=<MENSAL|SEMANAL>`;
    - `?year=<AAAA>` / `?month=<1..12>` (período, via `reference_start`);
    - `?search=<texto>` (título / subtítulo);
    - `?ordering=<campo>` (whitelist; default `-reference_start`).
    """

    serializer_class = FolderCultoSerializer
    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]
    pagination_class = None

    def get_queryset(self):
        church = self.request.user.church
        if church is None:
            return FolderCulto.objects.none()
        qs = FolderCulto.objects.filter(church=church).prefetch_related('rows')
        params = self.request.query_params
        tipo = (params.get('tipo') or '').strip().upper()
        if tipo in dict(FolderCulto.Tipo.choices):
            qs = qs.filter(tipo=tipo)
        year = _int_param(params, 'year')
        if year:
            qs = qs.filter(reference_start__year=year)
        month = _int_param(params, 'month')
        if month is not None:
            if not 1 <= month <= 12:
                raise ValidationError({'month': 'Mês inválido (1 a 12).'})
            qs = qs.filter(reference_start__month=month)
        search = (params.get('search') or '').strip()
        if search:
            from django.db.models import Q
            qs = qs.filter(
                Q(title__icontains=search) | Q(subtitle__icontains=search)
            )
        ordering = (params.get('ordering') or '-reference_start').strip()
        if ordering not in _ALLOWED_ORDERINGS:
            ordering = '-reference_start'
        return qs.order_by(ordering, '-id')

    def perform_create(self, serializer):
        serializer.save(
            church=self.request.user.church,
            created_by=self.request.user,
            updated_by=self.request.user,
        )

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    @action(detail=True, methods=['post'], url_path='sync-events')
    def sync_events(self, request, pk=None):
        """Puxa os eventos gerais do calendário do período como linhas de agenda.

        Não duplica linhas já existentes com a mesma data/tipo/título.
        """
        folder = self.get_object()
        events = services.agenda_calendar_events(
            folder.church, folder.reference_start, folder.reference_end,
        )
        existing = set(
            folder.rows.values_list('date', 'service_type', 'title')
        )
        created = 0
        for day, ev in services.expand_calendar_events(
            list(events), folder.reference_start, folder.reference_end,
        ):
            service_type = services.event_service_type(ev)
            key = (day, service_type, ev.title or '')
            if key in existing:
                continue
            FolderCultoRow.objects.create(
                folder=folder,
                date=day,
                service_type=service_type,
                title=ev.title or '',
                start_time=ev.start_time,
                end_time=ev.end_time,
                sort_order=created,
            )
            existing.add(key)
            created += 1
        return Response({
            'created': created,
            'folder': self.get_serializer(self.get_object()).data,
        })

    @action(detail=True, methods=['get'], url_path='birthdays')
    def birthdays(self, request, pk=None):
        """Aniversariantes ativos cujo aniversário cai no período do folder."""
        folder = self.get_object()
        return Response(services.folder_birthdays(folder))

    @action(detail=False, methods=['post'], url_path='preview-sync-events')
    def preview_sync_events(self, request):
        """Prévia das linhas de agenda do calendário, sem persistir (criação)."""
        church = request.user.church
        if church is None:
            raise ValidationError({'church': 'Usuário sem igreja ativa.'})
        start, end = _period_from_data(request.data)
        rows = services.preview_agenda_rows(church, start, end)
        return Response({'created': len(rows), 'rows': rows})

    @action(detail=False, methods=['get', 'post'], url_path='preview-birthdays')
    def preview_birthdays(self, request):
        """Prévia dos aniversariantes do período, sem persistir (criação)."""
        church = request.user.church
        if church is None:
            raise ValidationError({'church': 'Usuário sem igreja ativa.'})
        data = request.data if request.method == 'POST' else request.query_params
        start, end = _period_from_data(data)
        return Response(services.birthdays_for_period(church, start, end))

    @action(detail=True, methods=['post'], url_path='regenerate-hash')
    def regenerate_hash(self, request, pk=None):
        """Regenera o hash público do folder (invalida a URL anterior)."""
        folder = self.get_object()
        folder.regenerate_public_hash()
        return Response(self.get_serializer(folder).data)


class PublicFolderView(APIView):
    """Consulta pública de um folder pelo hash (sem autenticação)."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def get(self, request, hash):
        try:
            folder = (
                FolderCulto.objects
                .select_related('church', 'created_by', 'updated_by')
                .prefetch_related('rows')
                .get(public_hash=hash)
            )
        except FolderCulto.DoesNotExist:
            raise NotFound('Folder não encontrado.')
        church = folder.church
        data = FolderCultoSerializer(folder).data
        data['church'] = {
            'id': church.id,
            'slug': church.slug,
            'name': church.name,
            'city': church.city,
            'state': church.state,
            'neighborhood': church.neighborhood,
            'street': church.street,
            'number': church.number,
            'logo': cloudinary_url(church.logo),
            'theme_color': church.theme_color,
            'phone': church.phone,
            'default_pix_key': church.default_pix_key,
            'default_pix_type': church.default_pix_type,
        }
        return Response(data)