import csv
from datetime import date

from django.db.models import Count, F, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.models import Church, Member
from accounts.permissions import CanAccessTargetChurch, IsChurchRole

from . import services
from .models import (
    Event,
    EventCheckin,
    EventCheckinConfig,
    EventCheckinTeamMember,
    EventRegistration,
    EventSession,
)
from .serializers import (
    BulkMembersSerializer,
    CheckinManualSerializer,
    CheckinScanSerializer,
    EventCheckinConfigSerializer,
    EventCheckinRowSerializer,
    EventCheckinTeamMemberSerializer,
    EventRegistrationSerializer,
    EventSerializer,
    EventSessionSerializer,
    PublicEventRegistrationMetaSerializer,
    PublicEventRegistrationPayloadSerializer,
    PublicEventRegistrationResultSerializer,
    PublicEventSessionSerializer,
    registration_payload,
)
from .services import CheckinServiceError

CHECKIN_ROLES = ('PASTOR', 'SECRETARIA', 'TESOUREIRO')
IsCheckinManager = IsChurchRole(*CHECKIN_ROLES)


class CheckinPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class EventChurchMixin:
    def _church(self):
        if 'church_pk' in self.kwargs:
            return get_object_or_404(Church, pk=self.kwargs['church_pk'])
        church = self.request.user.church
        if church is None:
            raise ValidationError({
                'detail': 'Selecione uma igreja ativa antes de operar o check-in.'
            })
        return church

    def _validate_event(self, church, event):
        if event is None:
            raise ValidationError({'event': 'Evento obrigatório.'})
        event_id = event.id if hasattr(event, 'id') else int(event)
        if EventCheckinConfig.objects.filter(event_id=event_id).exists():
            raise ValidationError({'event': 'Este evento já possui configuração de check-in.'})
        return _get_calendar_event(church, event_id)

    def _registration_event(self, church, value):
        event_id = value.id if hasattr(value, 'id') else value
        event = Event.objects.filter(pk=event_id, church=church).first()
        if event is None:
            raise ValidationError({'event': 'Evento inválido para esta igreja.'})
        return event


def _date_param(value):
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _get_calendar_event(church, event_id):
    event = church.calendar_events.filter(pk=event_id).first()
    if event is None:
        raise ValidationError({'event': 'Evento inválido para esta igreja.'})
    return event


class EventViewSet(EventChurchMixin, viewsets.ModelViewSet):
    serializer_class = EventSerializer
    permission_classes = [IsAuthenticated, IsCheckinManager]
    pagination_class = CheckinPagination

    def get_queryset(self):
        qs = Event.objects.filter(church=self._church()).select_related(
            'calendar_event'
        )
        calendar_event = str(self.request.query_params.get('calendar_event') or '').strip()
        if calendar_event.isdigit():
            qs = qs.filter(calendar_event_id=int(calendar_event))
        source = str(self.request.query_params.get('source') or '').strip()
        if source in (Event.Source.CALENDAR, Event.Source.STANDALONE):
            qs = qs.filter(source=source)
        search = str(self.request.query_params.get('search') or '').strip()
        if search:
            qs = qs.filter(
                Q(title__icontains=search)
                | Q(location__icontains=search)
                | Q(calendar_event__title__icontains=search)
            )
        date_from = _date_param(self.request.query_params.get('date_from'))
        date_to = _date_param(self.request.query_params.get('date_to'))
        if date_from:
            qs = qs.filter(date__gte=date_from)
        if date_to:
            qs = qs.filter(date__lte=date_to)
        month = str(self.request.query_params.get('month') or '').strip()
        year = str(self.request.query_params.get('year') or '').strip()
        if month.isdigit() and 1 <= int(month) <= 12:
            qs = qs.filter(date__month=int(month))
        if year.isdigit():
            qs = qs.filter(date__year=int(year))
        return qs.order_by('-date', '-id')

    def perform_create(self, serializer):
        serializer.save(church=self._church())

    def perform_update(self, serializer):
        serializer.save(church=self._church())

    @action(detail=True, methods=['post'], url_path='enable-checkin')
    def enable_checkin(self, request, *args, **kwargs):
        event = self.get_object()
        config = services.enable_checkin_for_event(
            event, mode=request.data.get('mode') or None
        )
        return Response(EventCheckinConfigSerializer(config).data)

    @action(detail=True, methods=['post'], url_path='regenerate-registration-token')
    def regenerate_registration_token(self, request, *args, **kwargs):
        event = self.get_object()
        token = event.regenerate_registration_token()
        return Response({
            'token': token,
            'url': event.registration_url,
        })

    @action(detail=True, methods=['get'], url_path='share-registration')
    def share_registration(self, request, *args, **kwargs):
        event = self.get_object()
        return Response({
            'url': event.registration_url,
            'token': event.registration_token,
            'is_active': event.registration_token_is_active,
            'event_title': event.title,
        })


class EventCheckinConfigViewSet(EventChurchMixin, viewsets.ModelViewSet):
    serializer_class = EventCheckinConfigSerializer
    permission_classes = [IsAuthenticated, IsCheckinManager]
    pagination_class = CheckinPagination

    def get_queryset(self):
        qs = EventCheckinConfig.objects.filter(church=self._church()).select_related(
            'event', 'checkin_event', 'church'
        )
        event = str(self.request.query_params.get('event') or '').strip()
        if event.isdigit():
            qs = qs.filter(event_id=int(event))
        checkin_event = str(self.request.query_params.get('checkin_event') or '').strip()
        if checkin_event.isdigit():
            qs = qs.filter(checkin_event_id=int(checkin_event))
        return qs.order_by('-created_at')

    def perform_create(self, serializer):
        church = self._church()
        calendar_event = self._validate_event(church, serializer.validated_data.get('event'))
        event = services.ensure_event(calendar_event, church)
        config = serializer.save(
            church=church, event=calendar_event, checkin_event=event
        )
        services.generate_sessions(config)
        services.seed_member_registrations(event)

    def perform_update(self, serializer):
        church = self._church()
        serializer.validated_data.pop('event', None)
        serializer.save(church=church)

    @action(detail=True, methods=['post'], url_path='generate-sessions')
    def generate_sessions(self, request, *args, **kwargs):
        config = self.get_object()
        start = _date_param(request.data.get('start')) or config.generation_start_date
        end = _date_param(request.data.get('end')) or config.generation_end_date
        try:
            created, total = services.generate_sessions(config, start, end)
        except CheckinServiceError as exc:
            raise ValidationError({'detail': str(exc)})
        sessions = EventSessionSerializer(
            config.sessions.all(), many=True, context={'request': request}
        ).data
        return Response({'created': created, 'total': total, 'sessions': sessions})

    @action(detail=True, methods=['get'], url_path='preview-dates')
    def preview_dates(self, request, *args, **kwargs):
        config = self.get_object()
        start = _date_param(request.query_params.get('start')) or config.generation_start_date
        end = _date_param(request.query_params.get('end')) or config.generation_end_date
        try:
            days = services.session_dates(config, start, end)
        except CheckinServiceError as exc:
            raise ValidationError({'detail': str(exc)})
        return Response({'dates': [day.isoformat() for day in days]})

    @action(detail=True, methods=['post'], url_path='set-active')
    def set_active(self, request, *args, **kwargs):
        config = self.get_object()
        active = str(request.data.get('active', 'true')).lower() in ('1', 'true', 'yes')
        config.is_active = active
        config.save(update_fields=['is_active', 'updated_at'])
        return Response(EventCheckinConfigSerializer(config).data)


class AdminChurchEventCheckinConfigViewSet(EventCheckinConfigViewSet):
    permission_classes = [IsAuthenticated, CanAccessTargetChurch, IsCheckinManager]


class EventSessionViewSet(EventChurchMixin, viewsets.ModelViewSet):
    serializer_class = EventSessionSerializer
    permission_classes = [IsAuthenticated, IsCheckinManager]
    pagination_class = CheckinPagination
    http_method_names = ['get', 'post', 'patch', 'put', 'head', 'options']

    def get_queryset(self):
        qs = EventSession.objects.filter(church=self._church()).select_related(
            'event', 'checkin_event', 'config', 'church'
        )
        config = str(self.request.query_params.get('config') or '').strip()
        if config.isdigit():
            qs = qs.filter(config_id=int(config))
        search = str(self.request.query_params.get('search') or '').strip()
        if search:
            qs = qs.filter(event__title__icontains=search) | qs.filter(
                checkin_event__title__icontains=search
            )
        return qs.order_by('occurrence_date', 'id')

    @action(detail=True, methods=['post'], url_path='regenerate-token')
    def regenerate_token(self, request, *args, **kwargs):
        session = self.get_object()
        session.regenerate_token()
        return Response(EventSessionSerializer(session).data)

    @action(detail=True, methods=['post'], url_path='set-token-active')
    def set_token_active(self, request, *args, **kwargs):
        session = self.get_object()
        active = str(request.data.get('active', 'true')).lower() in ('1', 'true', 'yes')
        session.token_is_active = active
        session.save(update_fields=['token_is_active'])
        return Response(EventSessionSerializer(session).data)


class AdminChurchEventSessionViewSet(EventSessionViewSet):
    permission_classes = [IsAuthenticated, CanAccessTargetChurch, IsCheckinManager]


class EventCheckinTeamMemberViewSet(EventChurchMixin, viewsets.ModelViewSet):
    serializer_class = EventCheckinTeamMemberSerializer
    permission_classes = [IsAuthenticated, IsCheckinManager]
    pagination_class = CheckinPagination

    def get_queryset(self):
        qs = EventCheckinTeamMember.objects.filter(church=self._church()).select_related(
            'member', 'config', 'config__event', 'config__checkin_event'
        )
        config = str(self.request.query_params.get('config') or '').strip()
        if config.isdigit():
            qs = qs.filter(config_id=int(config))
        search = str(self.request.query_params.get('search') or '').strip()
        if search:
            qs = qs.filter(member__name__icontains=search)
        return qs.order_by('role', 'member__name', 'id')

    def _validate_payload(self, church, data):
        config_value = data.get('config') or getattr(
            getattr(self, 'instance', None), 'config_id', None
        )
        config_id = config_value.id if hasattr(config_value, 'id') else config_value
        config = get_object_or_404(EventCheckinConfig, pk=config_id, church=church)
        member_value = data.get('member') or getattr(
            getattr(self, 'instance', None), 'member_id', None
        )
        member_id = member_value.id if hasattr(member_value, 'id') else member_value
        member = get_object_or_404(Member, pk=member_id, church=church)
        return config, member

    def perform_create(self, serializer):
        church = self._church()
        config, member = self._validate_payload(church, serializer.validated_data)
        serializer.save(church=church, config=config, member=member)

    def perform_update(self, serializer):
        church = self._church()
        config, member = self._validate_payload(church, serializer.validated_data)
        serializer.save(church=church, config=config, member=member)


class AdminChurchEventCheckinTeamMemberViewSet(EventCheckinTeamMemberViewSet):
    permission_classes = [IsAuthenticated, CanAccessTargetChurch, IsCheckinManager]


class EventRegistrationViewSet(EventChurchMixin, viewsets.ModelViewSet):
    serializer_class = EventRegistrationSerializer
    permission_classes = [IsAuthenticated, IsCheckinManager]
    pagination_class = CheckinPagination
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        qs = EventRegistration.objects.filter(church=self._church()).select_related(
            'event', 'member'
        )
        event = str(self.request.query_params.get('event') or '').strip()
        if event.isdigit():
            qs = qs.filter(event_id=int(event))
        kind = str(self.request.query_params.get('kind') or '').strip().upper()
        if kind in EventRegistration.Kind.values:
            qs = qs.filter(kind=kind)
        search = str(self.request.query_params.get('search') or '').strip()
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(member__name__icontains=search)
                | Q(code__icontains=search)
            )
        return qs.order_by('-registered_at', '-id')

    def perform_create(self, serializer):
        church = self._church()
        event = self._registration_event(church, serializer.validated_data.get('event'))
        kind = serializer.validated_data.get('kind', EventRegistration.Kind.MEMBER)
        member = serializer.validated_data.get('member')
        if kind == EventRegistration.Kind.MEMBER and member is None:
            raise ValidationError({'member': 'Membro obrigatório para inscrição de membro.'})
        if kind == EventRegistration.Kind.EXTERNAL and not serializer.validated_data.get('name'):
            raise ValidationError({'name': 'Nome obrigatório para inscrito externo.'})
        if member and member.church_id != church.pk:
            raise ValidationError({'member': 'Membro inválido para esta igreja.'})
        serializer.save(church=church, event=event)

    def perform_destroy(self, instance):
        instance.status = EventRegistration.Status.CANCELLED
        instance.save(update_fields=['status', 'updated_at'])

    @action(detail=False, methods=['post'], url_path='bulk-members')
    def bulk_members(self, request, *args, **kwargs):
        serializer = BulkMembersSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        church = self._church()
        event = self._registration_event(
            church,
            request.data.get('event') or request.query_params.get('event'),
        )
        created = services.bulk_member_registrations(
            event,
            member_ids=serializer.validated_data.get('members'),
            all_members=serializer.validated_data.get('all_members'),
        )
        return Response({'created': created}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, *args, **kwargs):
        registration = self.get_object()
        registration.status = EventRegistration.Status.CANCELLED
        registration.save(update_fields=['status', 'updated_at'])
        return Response(EventRegistrationSerializer(registration).data)

    @action(detail=True, methods=['post'], url_path='regenerate-code')
    def regenerate_code(self, request, *args, **kwargs):
        registration = self.get_object()
        registration.code = EventRegistration.new_code()
        registration.save(update_fields=['code', 'updated_at'])
        return Response(EventRegistrationSerializer(registration).data)


class AdminChurchEventRegistrationViewSet(EventRegistrationViewSet):
    permission_classes = [IsAuthenticated, CanAccessTargetChurch, IsCheckinManager]


class EventCheckinViewSet(EventChurchMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = EventCheckinRowSerializer
    permission_classes = [IsAuthenticated, IsCheckinManager]
    pagination_class = CheckinPagination

    def get_queryset(self):
        qs = EventCheckin.objects.filter(church=self._church())
        return services.filter_checkins(qs, self.request.query_params)

    def _session(self, session_id, church):
        if not session_id:
            raise ValidationError({'session': 'Sessão obrigatória.'})
        session = EventSession.objects.filter(pk=session_id, church=church).first()
        if session is None:
            raise ValidationError({'session': 'Sessão inválida para esta igreja.'})
        return session

    def _serialize_result(self, result, request):
        checkin = result['checkin']
        return {
            'result': result['result'],
            'already': result['already'],
            'in_roster': result['in_roster'],
            'person': registration_payload(result['registration']),
            'checkin': EventCheckinRowSerializer(checkin, context={'request': request}).data,
        }

    @action(detail=False, methods=['post'], url_path='scan')
    def scan(self, request, *args, **kwargs):
        serializer = CheckinScanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        church = self._church()
        session = self._session(
            request.data.get('session') or request.query_params.get('session'), church
        )
        try:
            registration = services.resolve_registration_from_code(
                serializer.validated_data['code'], session
            )
            operator = services.resolve_operator(
                session.config, serializer.validated_data.get('operator')
            )
            result = services.register_checkin(
                session,
                registration=registration,
                operator=operator,
                source=serializer.validated_data['source'],
                code=serializer.validated_data['code'],
            )
        except CheckinServiceError as exc:
            raise ValidationError({'detail': exc.message, 'code': exc.code})
        return Response(
            self._serialize_result(result, request),
            status=status.HTTP_200_OK if result['already'] else status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=['post'], url_path='manual')
    def manual(self, request, *args, **kwargs):
        serializer = CheckinManualSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        church = self._church()
        session = self._session(
            request.data.get('session') or serializer.validated_data.get('session'),
            church,
        )
        try:
            event = services.event_for(session)
            if serializer.validated_data.get('registration'):
                registration = services.resolve_registration_by_id(
                    event, serializer.validated_data['registration']
                )
            else:
                member = services.resolve_member_by_id(
                    serializer.validated_data['member'], session
                )
                registration, _ = services.ensure_member_registration(event, member)
            operator = services.resolve_operator(
                session.config, serializer.validated_data.get('operator')
            )
            result = services.register_checkin(
                session,
                registration=registration,
                operator=operator,
                source=EventCheckin.Source.MANUAL,
            )
        except CheckinServiceError as exc:
            raise ValidationError({'detail': exc.message, 'code': exc.code})
        return Response(
            self._serialize_result(result, request),
            status=status.HTTP_200_OK if result['already'] else status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, *args, **kwargs):
        checkin = self.get_object()
        operator = services.resolve_operator(
            checkin.session.config, request.data.get('operator')
        )
        services.cancel_checkin(checkin, operator=operator)
        return Response(EventCheckinRowSerializer(checkin).data)

    @action(detail=False, methods=['get'], url_path='summary')
    def summary(self, request, *args, **kwargs):
        church = self._church()
        queryset = services.filter_checkins(
            EventCheckin.objects.filter(church=church), request.query_params
        )
        checked = queryset.filter(status=EventCheckin.Status.CHECKED_IN)
        total = checked.count()
        sessions = EventSession.objects.filter(church=church).select_related('event')
        session_id = str(request.query_params.get('session') or '').strip()
        if session_id.isdigit():
            sessions = sessions.filter(pk=int(session_id))
        expected = services.expected_registration_count(sessions)
        by_source = {
            row['source']: row['total']
            for row in checked.values('source').annotate(total=Count('id'))
        }
        by_kind = {
            row['registration__kind'] or 'UNKNOWN': row['total']
            for row in checked.values('registration__kind').annotate(total=Count('id'))
        }
        by_team = [
            {
                'id': row['operator_id'] or 0,
                'name': row['name'] or 'Sem operador',
                'total': row['total'],
            }
            for row in checked.values(
                'operator_id', 'operator__member__name'
            ).annotate(
                total=Count('id'), name=F('operator__member__name')
            ).order_by('-total')
        ]
        by_date = [
            {
                'date': row['session__occurrence_date'].isoformat(),
                'total': row['total'],
            }
            for row in checked.values('session__occurrence_date').annotate(
                total=Count('id')
            ).order_by('session__occurrence_date')
        ]
        return Response({
            'expected_count': expected,
            'checked_count': total,
            'outside_roster_count': checked.filter(in_roster=False).count(),
            'rate': round(total / expected, 4) if expected else 0,
            'by_source': by_source,
            'by_kind': by_kind,
            'by_team': by_team,
            'by_date': by_date,
        })

    @action(detail=False, methods=['get'], url_path='export')
    def export(self, request, *args, **kwargs):
        queryset = services.filter_checkins(
            EventCheckin.objects.filter(church=self._church()), request.query_params
        )[:10000]
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="checkins-eventos.csv"'
        response.write('\ufeff')
        writer = csv.writer(response)
        writer.writerow([
            'Data', 'Evento', 'Inscrito', 'Tipo', 'Código/Cartão', 'Igreja',
            'Inscrição', 'Origem da inscrição', 'Participante do evento',
            'Origem', 'Situação', 'Operador', 'Check-in em',
        ])
        for item in queryset:
            registration = item.registration
            writer.writerow([
                item.session.occurrence_date.isoformat(),
                item.session.event_title,
                item.display_name,
                registration.get_kind_display() if registration else 'Membro',
                registration.card_number if registration else (
                    item.member.card_number if item.member_id else ''
                ),
                item.member.church.name if item.member_id else item.church.name,
                registration.get_source_display() if registration else 'Migração',
                item.get_source_display(),
                'Sim' if item.in_roster else 'Não',
                item.get_source_display(),
                item.get_status_display(),
                item.operator.member.name if item.operator else '',
                item.checked_at.isoformat(),
            ])
        return response


class AdminChurchEventCheckinViewSet(EventCheckinViewSet):
    permission_classes = [IsAuthenticated, CanAccessTargetChurch, IsCheckinManager]


class PublicEventCheckinView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'public_event_checkin'

    def _session(self, token):
        return services.get_session_by_token(token)

    def get(self, request, token):
        session = self._session(token)
        if session is None:
            return Response(
                {'detail': 'Link de check-in inválido ou desativado.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        team = [
            {
                'id': item.pk,
                'name': item.member.name,
                'role': item.role,
                'role_display': item.get_role_display(),
            }
            for item in session.config.team_members.select_related('member').all()
        ]
        event = services.event_for(session)
        data = {
            'token': session.token,
            'event_id': event.pk if event else None,
            'event_title': session.event_title,
            'church_name': session.church.name,
            'occurrence_date': session.occurrence_date,
            'start_time': session.start_time,
            'end_time': session.end_time,
            'mode': session.config.mode,
            'is_open': session.is_open(),
            'team': team,
        }
        return Response(PublicEventSessionSerializer(data).data)

    def post(self, request, token):
        session = self._session(token)
        if session is None:
            return Response(
                {'detail': 'Link de check-in inválido ou desativado.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = CheckinScanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            registration = services.resolve_registration_from_code(
                serializer.validated_data['code'], session
            )
            operator = services.resolve_operator(
                session.config, serializer.validated_data.get('operator')
            )
            result = services.register_checkin(
                session,
                registration=registration,
                operator=operator,
                source=serializer.validated_data['source'],
                code=serializer.validated_data['code'],
            )
        except CheckinServiceError as exc:
            return Response(
                {'detail': exc.message, 'code': exc.code, 'valid': False},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({
            'valid': True,
            'result': result['result'],
            'already': result['already'],
            'in_roster': result['in_roster'],
            'person': registration_payload(result['registration']),
            'checked_at': result['checkin'].checked_at,
        })


class PublicEventRegistrationView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'public_event_registration'

    def _event(self, token):
        return Event.objects.select_related('church').filter(
            registration_token=token,
            registration_token_is_active=True,
            is_active=True,
        ).first()

    def get(self, request, token):
        event = self._event(token)
        if event is None:
            return Response(
                {'detail': 'Link de inscrição inválido ou desativado.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        data = {
            'token': event.registration_token,
            'event_id': event.pk,
            'event_title': event.title,
            'church_name': event.church.name,
            'date': event.date_value,
            'start_time': event.start_time_value,
            'end_time': event.end_time_value,
            'location': event.location,
            'description': event.description,
            'is_open': True,
        }
        return Response(PublicEventRegistrationMetaSerializer(data).data)

    def post(self, request, token):
        event = self._event(token)
        if event is None:
            return Response(
                {'detail': 'Link de inscrição inválido ou desativado.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = PublicEventRegistrationPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        registration, created = services.ensure_external_registration(
            event,
            name=serializer.validated_data['name'],
            phone=serializer.validated_data.get('phone', ''),
            email=serializer.validated_data.get('email', ''),
        )
        result = PublicEventRegistrationResultSerializer({
            'created': created,
            'already': not created,
            'code': registration.code,
            'qr_url': registration.qr_url,
            'name': registration.display_name,
            'event_title': event.title,
        })
        return Response(
            result.data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
