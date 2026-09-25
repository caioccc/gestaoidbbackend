import logging
import re

from django.db import models as django_models
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework.decorators import api_view, permission_classes
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsChurchRole
from .permissions import (
    IsSongOwnerOrAdmin,
    IsSetlistOwnerOrPastor,
)

from .models import (
    Band,
    BandSetlist,
    BandSetlistItem,
    Ministry,
    MinistryRole,
    RosterAssignment,
    SetlistItem,
    Song,
    VolunteerRoster,
    WorshipSetlist,
)
from .serializers import (
    BandSerializer,
    BandSetlistPayloadSerializer,
    BandSetlistSerializer,
    MinistryRoleSerializer,
    MinistrySerializer,
    RosterAssignmentSerializer,
    RosterCreatePayloadSerializer,
    RosterSerializer,
    SetlistCreateSerializer,
    SongHistorySerializer,
    SongSerializer,
    WorshipSetlistSerializer,
)
from .services import chordify as chordify_service
from .services import exports as exports_service
from .services import youtube as youtube_service

logger = logging.getLogger(__name__)

MANAGER_ROLES = ('LOUVOR', 'PASTOR', 'SECRETARIA')
VIEW_ROLES = ('MUSICO',) + MANAGER_ROLES

# Editar/excluir setlists (de banda e a do culto): apenas PASTOR/ADMIN.
SETLIST_GOVERNANCE_ROLES = ('PASTOR',)

# Ordenações aceitas no list de músicas (repertório). A "chave" é enviada
# via `?ordering=` pelo frontend; o valor é o alvo do `order_by`.
SONG_ORDERING_MAP = {
    'random': '?',
    'times_played': '-total_plays',
    '-times_played': 'total_plays',
    'band': 'band__name',
    '-band': '-band__name',
    'artist': 'artist',
    '-artist': '-artist',
    'title': 'title',
    '-title': '-title',
}


class SongPagination(PageNumberPagination):
    """Paginação das músicas do repertório (?page & ?page_size)."""

    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


def _apply_month_filter(qs, query_params):
    month = query_params.get('month')
    if month and len(month) == 7:
        try:
            year, month_num = month.split('-')
            qs = qs.filter(date__year=int(year), date__month=int(month_num))
        except ValueError:
            pass
    return qs


class BandViewSet(viewsets.ModelViewSet):
    """Bandas fixas da igreja (Águia da Paz, Siloé, ...).

    Cada banda tem membros fixos e repertório próprio; as músicas podem ser
    filtradas por `?band=<id>` no endpoint de músicas.
    """

    serializer_class = BandSerializer
    permission_classes = [IsChurchRole(*MANAGER_ROLES)]
    pagination_class = None

    def get_queryset(self):
        church = self.request.user.church
        if church is None:
            return Band.objects.none()
        return Band.objects.filter(church=church).annotate(
            song_count=django_models.Count('songs')
        )

    def perform_create(self, serializer):
        serializer.save(church=self.request.user.church)

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response(
                {'detail': 'Esta banda tem músicas vinculadas e não pode ser excluída.'},
                status=status.HTTP_400_BAD_REQUEST,
            )


class BandSetlistViewSet(viewsets.ModelViewSet):
    """Setlists das bandas (data, descrição, tema opcional e músicas do repertório).

    Leitura: MÚSICO+; escrita: gestores. No payload de escrita, `items` é uma
    lista de {song, order?, custom_key?, notes?}; a ordem usada é a posição
    quando `order` não vem informado.
    """

    pagination_class = None

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return BandSetlistPayloadSerializer
        return BandSetlistSerializer

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [IsChurchRole(*VIEW_ROLES)()]
        if self.action in ('create',):
            # Qualquer membro ativo pode criar a SUA setlist de banda
            # (quem cria vira `created_by`). Governança fica no objeto.
            return [IsSetlistOwnerOrPastor()]
        if self.action in ('update', 'partial_update', 'destroy'):
            # Ownership: o criador edita/exclui a sua; PASTOR/ADMIN
            # governa todas; demais perfis recebem HTTP 403.
            return [IsSetlistOwnerOrPastor()]
        return [IsChurchRole(*VIEW_ROLES)()]

    def get_queryset(self):
        church = self.request.user.church
        if church is None:
            return BandSetlist.objects.none()
        qs = BandSetlist.objects.filter(church=church).prefetch_related(
            'items__song', 'band'
        )
        month = self.request.query_params.get('month')
        if month and len(month) == 7 and month[4] == '-':
            year, _, month_num = month.partition('-')
            qs = qs.filter(date__year=int(year), date__month=int(month_num))
        band_id = self.request.query_params.get('band')
        if band_id:
            qs = qs.filter(band_id=band_id)
        return qs.order_by('-date', 'created_at')

    def _save_items(self, setlist, items_data):
        BandSetlistItem.objects.bulk_create(
            BandSetlistItem(
                setlist=setlist, song_id=data.pop('song'), **data
            )
            for data in items_data
        )

    def create(self, request, *args, **kwargs):
        payload = BandSetlistPayloadSerializer(
            data=request.data, context=self.get_serializer_context()
        )
        payload.is_valid(raise_exception=True)
        data = payload.validated_data.copy()
        items_data = data.pop('items', [])
        setlist = BandSetlist.objects.create(
            church=request.user.church, created_by=request.user, **data
        )
        self._save_items(setlist, items_data)
        return Response(
            BandSetlistSerializer(setlist).data, status=status.HTTP_201_CREATED
        )

    def update(self, request, *args, **kwargs):
        setlist = self.get_object()
        payload = BandSetlistPayloadSerializer(
            data=request.data, context=self.get_serializer_context()
        )
        payload.is_valid(raise_exception=True)
        data = payload.validated_data.copy()
        items_data = data.pop('items', None)
        for key, value in data.items():
            setattr(setlist, key, value)
        setlist.save()
        if items_data is not None:
            BandSetlistItem.objects.filter(setlist=setlist).delete()
            self._save_items(setlist, items_data)
        return Response(BandSetlistSerializer(setlist).data)


class MinistryViewSet(viewsets.ModelViewSet):
    serializer_class = MinistrySerializer
    permission_classes = [IsChurchRole(*MANAGER_ROLES)]
    pagination_class = None

    def get_queryset(self):
        church = self.request.user.church
        if church is None:
            return Ministry.objects.none()
        return Ministry.objects.filter(church=church).prefetch_related('roles')

    def perform_create(self, serializer):
        serializer.save(church=self.request.user.church)

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response(
                {'detail': 'Este ministério tem escalas vinculadas e não pode ser excluído.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=['post'], url_path='roles')
    def add_role(self, request, pk=None):
        """Cria uma função (role) dentro do ministério."""
        ministry = self.get_object()
        name = (request.data.get('name') or '').strip()
        if not name:
            return Response({'name': 'Informe o nome da função.'}, status=400)
        role = MinistryRole.objects.create(ministry=ministry, name=name)
        return Response(MinistryRoleSerializer(role).data, status=201)

    @action(detail=True, methods=['delete'], url_path=r'roles/(?P<role_pk>[^/.]+)')
    def delete_role(self, request, pk=None, role_pk=None):
        """Remove uma função do ministério."""
        ministry = self.get_object()
        role = get_object_or_404(MinistryRole, pk=role_pk, ministry=ministry)
        role.delete()
        return Response(status=204)


class VolunteerRosterViewSet(viewsets.ModelViewSet):
    """Escalas de voluntários.

    - `GET .../rosters/?month=YYYY-MM` lista os cultos do mês.
    - `POST .../rosters/` cria a escala e permite `assignments` inline.
    - `GET .../rosters/<id>/export-text/` texto para o WhatsApp da equipe.
    - `GET .../rosters/<id>/board/` quadro agrupado por ministério.
    - `POST/PUT/DELETE .../rosters/<id>/setlist/` substitui a setlist do culto.
    """

    pagination_class = None

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return RosterCreatePayloadSerializer
        return RosterSerializer

    def get_permissions(self):
        if self.action in ('my_assignments', 'my_rosters'):
            return [IsChurchRole(*VIEW_ROLES)()]
        if self.action == 'manage_setlist':
            # Ownership: quem criou o roster governa a setlist do culto; a
            # sua; PASTOR/ADMIN governa todas. Demais perfis HTTP 403.
            return [IsSetlistOwnerOrPastor()]
        if self.action == 'create':
            # Criação aberta a qualquer membro com igreja ativa (a setlist
            # do culto vira "sua", com ownership no `created_by` do roster).
            return [IsChurchRole(*VIEW_ROLES)()]
        return [IsChurchRole(*MANAGER_ROLES)()]

    def get_queryset(self):
        church = self.request.user.church
        if church is None:
            return VolunteerRoster.objects.none()
        qs = VolunteerRoster.objects.filter(church=church).prefetch_related(
            'assignments__ministry',
            'assignments__role',
            'assignments__user',
            'setlist__items__song',
        )
        month = self.request.query_params.get('month')
        if month and len(month) == 7:
            year, month_num = month.split('-')
            qs = qs.filter(date__year=int(year), date__month=int(month_num))
        return qs.order_by('date', 'time')

    def perform_create(self, serializer):
        serializer.save(church=self.request.user.church)

    @action(detail=False, methods=['get'])
    def my_rosters(self, request):
        """Lista de escalas do MÚSICO logado (somente as que tem atribuição)."""
        church = request.user.church
        if church is None:
            return Response([], status=200)
        qs = VolunteerRoster.objects.filter(
            church=church, assignments__user=request.user,
        ).prefetch_related(
            'assignments__ministry',
            'assignments__role',
            'assignments__user',
            'setlist__items__song',
        ).distinct()
        qs = _apply_month_filter(qs, request.query_params).order_by('date', 'time')
        serializer = RosterSerializer(qs, many=True, context=self.get_serializer_context())
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def volunteers(self, request):
        """Lista os usuários da igreja para seleção de voluntários (dados mínimos)."""
        church = request.user.church
        if church is None:
            return Response([], status=status.HTTP_200_OK)
        User = get_user_model()
        users = (
            User.objects.filter(church_memberships__church=church, is_active=True)
            .order_by('name', 'email')
            .values_list('id', 'name', 'email')
        )
        data = [
            {'id': uid, 'name': name or email, 'email': email}
            for uid, name, email in users
        ]
        return Response(data)

    @action(detail=True, methods=['get'])
    def export_text(self, request, pk=None):
        roster = self.get_object()
        return Response({'text': exports_service.format_roster_export(roster)})

    @action(detail=True, methods=['get'])
    def board(self, request, pk=None):
        roster = self.get_object()
        return Response(exports_service.build_roster_board(roster))

    @action(detail=True, methods=['get'])
    def my_assignments(self, request, pk=None):
        """Escalas do mês em que o MÚSICO está escalado (visão do voluntário)."""
        user = request.user
        roster = self.get_object()
        qs = roster.assignments.filter(user=user).select_related('ministry', 'role')
        rows = [
            {
                'assignment_id': a.id,
                'ministry': a.ministry.name,
                'ministry_color': a.ministry.color,
                'role': a.role.name,
                'status': a.status,
                'status_display': a.get_status_display(),
            }
            for a in qs
        ]
        return Response({'roster_id': roster.id, 'date': roster.date, 'rows': rows})

    @action(detail=True, methods=['post', 'put', 'delete'], url_path='setlist')
    def manage_setlist(self, request, pk=None):
        """Define/substitui a ordem das músicas de um culto.

        POST/PUT: corpo `{items: [{song, order?, custom_key?, notes?}]}`.
        DELETE: remove o setlist do culto.
        """
        roster = self.get_object()
        if request.method == 'DELETE':
            WorshipSetlist.objects.filter(roster=roster).delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = SetlistCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        items_data = serializer.validated_data['items']

        setlist, _created = WorshipSetlist.objects.get_or_create(
            roster=roster, defaults={'created_by': request.user},
        )
        song_ids = [item['song'] for item in items_data]
        valid_ids = set(
            Song.objects.filter(
                church=request.user.church, id__in=song_ids,
            ).values_list('id', flat=True)
        )
        for item in items_data:
            if item['song'] not in valid_ids:
                raise ValidationError(
                    {'items': f'Música {item["song"]} não pertence à sua igreja.'}
                )

        with transaction.atomic():
            SetlistItem.objects.filter(setlist=setlist).delete()
            items = [
                SetlistItem(
                    setlist=setlist, song_id=item['song'],
                    order=item['order'], custom_key=item['custom_key'],
                    notes=item['notes'],
                )
                for item in items_data
            ]
            SetlistItem.objects.bulk_create(items)

        setlist.refresh_from_db()
        return Response(WorshipSetlistSerializer(setlist).data)


class RosterAssignmentViewSet(viewsets.ModelViewSet):
    serializer_class = RosterAssignmentSerializer
    permission_classes = [IsChurchRole(*MANAGER_ROLES)]
    pagination_class = None

    def get_queryset(self):
        church = self.request.user.church
        if church is None:
            return RosterAssignment.objects.none()
        return RosterAssignment.objects.filter(
            roster__church=church,
        ).select_related('ministry', 'role', 'user')


class SongViewSet(viewsets.ModelViewSet):
    serializer_class = SongSerializer
    pagination_class = SongPagination

    def get_permissions(self):
        if self.action in (
            'list', 'retrieve', 'history', 'check_youtube', 'reprocess',
        ):
            return [IsChurchRole(*VIEW_ROLES)()]
        return [IsSongOwnerOrAdmin()]

    def get_queryset(self):
        church = self.request.user.church
        if church is None:
            return Song.objects.none()
        today = timezone.localdate()
        qs = Song.objects.filter(church=church).select_related(
            'band', 'created_by'
        ).annotate(
            worship_plays=django_models.Count(
                'setlist_items',
                filter=django_models.Q(
                    setlist_items__setlist__roster__date__lte=today
                ),
                distinct=True,
            ),
            band_plays=django_models.Count(
                'band_setlist_items',
                filter=django_models.Q(
                    band_setlist_items__setlist__date__lte=today
                ),
                distinct=True,
            ),
            worship_last=django_models.Max(
                'setlist_items__setlist__roster__date',
                filter=django_models.Q(
                    setlist_items__setlist__roster__date__lte=today
                ),
            ),
            band_last=django_models.Max(
                'band_setlist_items__setlist__date',
                filter=django_models.Q(
                    band_setlist_items__setlist__date__lte=today
                ),
            ),
            total_plays=django_models.F('worship_plays')
            + django_models.F('band_plays'),
        )
        band = self.request.query_params.get('band')
        if band:
            qs = qs.filter(band_id=band)
        key = self.request.query_params.get('key')
        if key:
            qs = qs.filter(church_key__iexact=key)
        tag = self.request.query_params.get('tag')
        if tag:
            qs = qs.filter(tags__icontains=tag)
        search = self.request.query_params.get('q')
        if search:
            qs = qs.filter(
                django_models.Q(title__icontains=search)
                | django_models.Q(artist__icontains=search)
                | django_models.Q(tags__icontains=search)
            )
        ordering = self.request.query_params.get('ordering')
        order_target = SONG_ORDERING_MAP.get(ordering, '?')
        return qs.order_by(order_target)

    def list(self, request, *args, **kwargs):
        """Lista paginada quando `?page=`/`?page_size=` é informado; caso
        contrário devolve o array completo (compatibilidade com os pickers
        de setlists, que precisam de todo o repertório)."""
        queryset = self.filter_queryset(self.get_queryset())
        if request.query_params.get('page') or request.query_params.get('page_size'):
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def perform_create(self, serializer):
        saved = serializer.save(
            church=self.request.user.church, created_by=self.request.user
        )
        logger.info(
            'Songs[create] música #%s "%s" (youtube_id=%s) salva com chord_status=%s%s',
            saved.pk,
            saved.title,
            saved.youtube_id or '-',
            saved.chord_status,
            ' | cifras fornecidas manualmente' if saved.chord_status == 'MANUAL' else '',
        )

    def perform_update(self, serializer):
        saved = serializer.save()
        logger.info(
            'Songs[update] música #%s "%s" atualizada (chord_status=%s retries=%d)',
            saved.pk,
            saved.title,
            saved.chord_status,
            saved.chord_retries,
        )

    @action(detail=True, methods=['post'], url_path='reprocess')
    def reprocess(self, request, pk=None):
        """Coloca a música de volta na fila do worker de extração de cifras.

        Qualquer perfil da igreja com acesso ao repertório pode reprocessar.
        Reseta status/erro/tentativas; o worker local (Docker, IP residencial)
        faz a coleta no Chordify em background.
        """
        song = self.get_object()
        song.chord_status = Song.ChordStatus.PENDING
        song.chord_error = ''
        song.chord_retries = 0
        song.chord_processed_at = None
        song.save(update_fields=[
            'chord_status', 'chord_error', 'chord_retries', 'chord_processed_at',
            'updated_at',
        ])
        logger.info(
            'Songs[reprocess] música #%s "%s" (youtube_id=%s) reenfileirada '
            'para extração (PENDING) por usuário #%s',
            song.pk,
            song.title,
            song.youtube_id or '-',
            request.user.pk,
        )
        return Response(
            {
                'success': True,
                'message': 'Música enviada para reprocessamento da cifra.',
                'song': SongSerializer(song).data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['get'], url_path='check-youtube')
    def check_youtube(self, request):
        """Busca global (catálogo da denominação) por youtube_id para pré-cadastro.

        Se um vídeo já foi cadastrado por qualquer igreja, devolve os dados para
        preencher o formulário e evitar novo scraping (Selenium/Chordify).
        """
        video_id = (request.query_params.get('video_id') or '').strip()
        if not video_id:
            return Response({'found': False, 'song': None})
        existing = (
            Song.objects.filter(youtube_id=video_id)
            .select_related('band')
            .order_by('-updated_at')
            .first()
        )
        if existing is None:
            return Response({'found': False, 'song': None})
        return Response({'found': True, 'song': SongSerializer(existing).data})

    @action(detail=True, methods=['get'])
    def history(self, request, pk=None):
        song = self.get_object()
        fallback_key = song.church_key or song.original_key
        rows = []

        worship_items = (
            SetlistItem.objects.filter(song=song)
            .select_related('setlist__roster')
        )
        for item in worship_items:
            roster = item.setlist.roster
            rows.append({
                'date': roster.date,
                'name': roster.theme or 'Culto',
                'key': item.custom_key or fallback_key,
                'kind': 'worship',
                'setlist_id': item.setlist_id,
            })

        band_items = (
            BandSetlistItem.objects.filter(song=song)
            .select_related('setlist')
        )
        for item in band_items:
            setlist = item.setlist
            rows.append({
                'date': setlist.date,
                'name': setlist.description or setlist.theme or 'Setlist',
                'key': item.custom_key or fallback_key,
                'kind': 'band',
                'setlist_id': setlist.id,
            })

        rows.sort(key=lambda row: row['date'], reverse=True)
        serializer = SongHistorySerializer(rows[:60], many=True)
        return Response({'results': serializer.data})


# ---------------------------------------------------------------------------
# Scraping endpoints (YouTube / Chordify)
# ---------------------------------------------------------------------------

class YouTubeSearchView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = (request.GET.get('q') or '').strip()
        if not query:
            return Response({'error': 'Parâmetro "q" é obrigatório.'}, status=400)
        limit = min(int(request.GET.get('limit') or 20), 30)
        youtube_id = youtube_service.extract_youtube_id(query)
        if youtube_id:
            query = youtube_id
        data = youtube_service.youtube_search(query, limit=limit)
        return Response(data)


class ChordifyView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        youtube_id = (request.GET.get('youtube_id') or '').strip()
        if not youtube_id:
            return Response({'error': 'O parâmetro "youtube_id" é obrigatório.'}, status=400)
        if not re.fullmatch(r'[\w-]{11}', youtube_id):
            return Response(
                {'error': 'O parâmetro "youtube_id" deve ter exatamente 11 caracteres.',
                 'success': False},
                status=400,
            )
        instrument = request.GET.get('instrument') or 'guitar'
        try:
            data = chordify_service.enrich_chordify_data(youtube_id, instrument)
        except Exception as exc:  # noqa: BLE001
            logger.error('Erro inesperado no Chordify para %s: %s', youtube_id, exc)
            data = chordify_service.chordify_unavailable(str(exc))
        return Response(data)