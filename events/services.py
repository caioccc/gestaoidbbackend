import re
from datetime import date, timedelta

from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from accounts.services import normalize_qr_code, resolve_qr_code
from finance.services import calendar_event_anchor_date, calendar_event_occurs_on

from .models import (
    Event,
    EventCheckin,
    EventCheckinAttempt,
    EventCheckinConfig,
    EventRegistration,
    EventSession,
)

MAX_GENERATED_SESSIONS = 366


class CheckinServiceError(Exception):
    code = 'invalid'
    message = 'Não foi possível registrar o check-in.'


class InvalidCodeError(CheckinServiceError):
    code = 'invalid_code'
    message = 'Código inválido ou inscrição inativa.'


class WrongChurchError(CheckinServiceError):
    code = 'wrong_church'
    message = 'Este membro pertence a outra igreja.'


class SessionClosedError(CheckinServiceError):
    code = 'session_closed'
    message = 'O check-in desta sessão está fechado.'


def normalize_contact(value):
    return re.sub(r'\D+', '', str(value or ''))


def event_for(obj):
    if obj is None:
        return None
    if isinstance(obj, Event):
        return obj
    event_id = getattr(obj, 'checkin_event_id', None)
    if event_id:
        return Event.objects.select_related('calendar_event').get(pk=event_id)

    calendar_event_id = getattr(obj, 'event_id', None)
    if not calendar_event_id:
        return None
    event = Event.objects.select_related('calendar_event').filter(
        calendar_event_id=calendar_event_id
    ).first()
    if event:
        return event

    calendar_event = getattr(obj, 'event', None)
    if calendar_event is None:
        from finance.models import CalendarEvent

        calendar_event = CalendarEvent.objects.filter(pk=calendar_event_id).first()
    if calendar_event is None:
        return None
    church_id = getattr(obj, 'church_id', None) or calendar_event.church_id
    return Event.objects.create(
        church_id=church_id,
        source=Event.Source.CALENDAR,
        calendar_event=calendar_event,
        title=calendar_event.title,
        date=calendar_event.date,
        start_time=calendar_event.start_time,
        end_time=calendar_event.end_time,
    )


def event_members(event):
    from accounts.models import Member

    if event.calendar_event_id:
        return event.calendar_event.members.select_related('church').all()
    return Member.objects.none()


def seed_member_registrations(event):
    created = 0
    for member in event_members(event):
        _, was_created = ensure_member_registration(
            event, member, source=EventRegistration.Source.SEED
        )
        created += int(was_created)
    return created


@transaction.atomic
def ensure_event(calendar_event, church=None):
    event = Event.objects.select_related('calendar_event').filter(
        calendar_event=calendar_event
    ).first()
    if event:
        return event
    return Event.objects.create(
        church=church or calendar_event.church,
        source=Event.Source.CALENDAR,
        calendar_event=calendar_event,
        title=calendar_event.title,
        date=calendar_event.date,
        start_time=calendar_event.start_time,
        end_time=calendar_event.end_time,
    )


@transaction.atomic
def ensure_member_registration(event, member, source=None):
    registration = EventRegistration.objects.filter(
        event=event, member=member, status=EventRegistration.Status.ACTIVE
    ).first()
    if registration:
        return registration, False

    if source is None:
        in_event_roster = event_members(event).filter(pk=member.pk).exists()
        source = (
            EventRegistration.Source.SEED
            if in_event_roster
            else EventRegistration.Source.WALK_IN
        )
    registration = EventRegistration.objects.create(
        event=event,
        church_id=event.church_id,
        member=member,
        kind=EventRegistration.Kind.MEMBER,
        name=member.name,
        source=source,
    )
    return registration, True


@transaction.atomic
def ensure_external_registration(event, name, phone='', email='', source=None):
    source = source or EventRegistration.Source.PUBLIC_FORM
    phone_digits = normalize_contact(phone)
    email_value = str(email or '').strip().lower()
    queryset = EventRegistration.objects.filter(
        event=event,
        kind=EventRegistration.Kind.EXTERNAL,
        status=EventRegistration.Status.ACTIVE,
    )
    existing = None
    if phone_digits:
        for item in queryset.filter(phone__isnull=False):
            if normalize_contact(item.phone) == phone_digits:
                existing = item
                break
    if existing is None and email_value:
        existing = queryset.filter(email__iexact=email_value).first()
    if existing:
        return existing, False
    registration = EventRegistration.objects.create(
        event=event,
        church_id=event.church_id,
        kind=EventRegistration.Kind.EXTERNAL,
        name=name.strip(),
        phone=str(phone or '').strip(),
        email=email_value,
        source=source,
    )
    return registration, True


@transaction.atomic
def bulk_member_registrations(event, member_ids=None, all_members=False):
    from accounts.models import Member

    if all_members:
        queryset = Member.objects.filter(
            church_id=event.church_id, status=Member.Status.ACTIVE
        )
    else:
        queryset = Member.objects.filter(
            church_id=event.church_id, pk__in=member_ids or []
        )
    created = 0
    for member in queryset:
        _, was_created = ensure_member_registration(
            event, member, source=EventRegistration.Source.SEED
        )
        created += int(was_created)
    return created


def session_dates(config, start=None, end=None):
    event_record = event_for(config)
    if event_record and event_record.source == Event.Source.STANDALONE:
        return [event_record.date] if event_record.date else []

    event = config.event
    if config.mode == EventCheckinConfig.Mode.ONCE:
        day = (
            (event.date if event else None)
            or calendar_event_anchor_date(event)
            or (event_record.date if event_record else None)
            or (start or date.today())
        )
        return [day]

    anchor = calendar_event_anchor_date(event)
    first = (
        start
        or config.generation_start_date
        or anchor
        or (event_record.date if event_record else None)
        or date.today()
    )
    last = (
        end
        or config.generation_end_date
        or (event.repeat_end_date if event else None)
        or first + timedelta(days=365)
    )
    if last < first:
        raise CheckinServiceError('Período inválido para gerar sessões.')
    if (last - first).days >= MAX_GENERATED_SESSIONS:
        raise CheckinServiceError('Período muito longo para gerar sessões.')

    if event is None:
        return [first]
    return [
        first + timedelta(days=offset)
        for offset in range((last - first).days + 1)
        if calendar_event_occurs_on(event, first + timedelta(days=offset))
    ]


@transaction.atomic
def generate_sessions(config, start=None, end=None):
    days = session_dates(config, start, end)
    event_record = event_for(config)
    created = 0
    for day in days:
        _, was_created = EventSession.objects.get_or_create(
            config=config,
            occurrence_date=day,
            defaults={
                'church': config.church,
                'event': config.event,
                'checkin_event': event_record,
            },
        )
        created += int(was_created)
    return created, EventSession.objects.filter(config=config).count()


@transaction.atomic
def enable_checkin_for_event(event, mode=None):
    if event.source == Event.Source.CALENDAR and event.calendar_event_id:
        config = EventCheckinConfig.objects.filter(
            event=event.calendar_event
        ).first()
    else:
        config = EventCheckinConfig.objects.filter(checkin_event=event).first()
    if config is None:
        config = EventCheckinConfig.objects.create(
            church=event.church,
            event=event.calendar_event if event.source == Event.Source.CALENDAR else None,
            checkin_event=event,
            mode=mode or EventCheckinConfig.Mode.ONCE,
            is_active=True,
        )
    elif mode:
        config.mode = mode
        config.is_active = True
        config.save(update_fields=['mode', 'is_active', 'updated_at'])
    generate_sessions(config)
    seed_member_registrations(event)
    return config


def get_session_by_token(token):
    return (
        EventSession.objects.select_related(
            'church', 'event', 'checkin_event', 'config', 'config__event'
        )
        .filter(token=token, token_is_active=True, config__is_active=True)
        .first()
    )


def resolve_member_from_code(code, session):
    resolved = resolve_qr_code(code, context=session.pk)
    if not resolved or resolved.get('kind') != 'member':
        raise InvalidCodeError()
    member = resolved['member']
    if member.church_id != session.church_id:
        raise WrongChurchError()
    return member


def resolve_member_by_id(member_id, session):
    from accounts.models import Member

    member = (
        Member.objects.select_related('church')
        .filter(pk=member_id, church=session.church)
        .first()
    )
    if member is None:
        raise InvalidCodeError()
    return member


def resolve_registration_from_code(code, session):
    event = event_for(session)
    if event is None:
        raise InvalidCodeError()
    normalized = normalize_qr_code(code)
    registration = EventRegistration.objects.select_related('member').filter(
        event=event,
        code=normalized,
        status=EventRegistration.Status.ACTIVE,
    ).first()
    if registration:
        return registration
    member = resolve_member_from_code(code, session)
    registration, _ = ensure_member_registration(event, member)
    return registration


def resolve_registration_by_id(event, registration_id):
    registration = EventRegistration.objects.select_related('member').filter(
        pk=registration_id,
        event=event,
        status=EventRegistration.Status.ACTIVE,
    ).first()
    if registration is None:
        raise InvalidCodeError()
    return registration


def resolve_operator(config, operator_id):
    if not operator_id:
        return None
    return config.team_members.filter(pk=operator_id).first()


@transaction.atomic
def register_checkin(
    session, member=None, operator=None, source='SCAN', code='', registration=None
):
    if not session.is_open():
        raise SessionClosedError()

    event = event_for(session)
    if registration is None:
        if member is None:
            raise InvalidCodeError()
        registration, _ = ensure_member_registration(event, member)
    if registration.event_id != event.id:
        raise WrongChurchError()
    if registration.status != EventRegistration.Status.ACTIVE:
        raise InvalidCodeError()

    in_roster = registration.in_roster
    now = timezone.now()
    checkin, created = EventCheckin.objects.get_or_create(
        session=session,
        registration=registration,
        defaults={
            'church': session.church,
            'member': registration.member,
            'operator': operator,
            'in_roster': in_roster,
            'source': source,
            'status': EventCheckin.Status.CHECKED_IN,
            'checked_at': now,
        },
    )
    if created:
        result = 'REGISTERED'
    elif checkin.status == EventCheckin.Status.CANCELLED:
        checkin.status = EventCheckin.Status.CHECKED_IN
        checkin.operator = operator
        checkin.in_roster = in_roster
        checkin.source = source
        checkin.checked_at = now
        checkin.save(
            update_fields=['status', 'operator', 'in_roster', 'source', 'checked_at']
        )
        result = 'REGISTERED'
    else:
        result = 'ALREADY'

    EventCheckinAttempt.objects.create(
        session=session,
        church=session.church,
        member=registration.member,
        registration=registration,
        operator=operator,
        code_fingerprint=EventCheckinAttempt.fingerprint(normalize_qr_code(code)),
        result=result,
        detail='' if result == 'REGISTERED' else 'Check-in já registrado.',
    )
    return {
        'checkin': checkin,
        'result': result,
        'already': result == 'ALREADY',
        'in_roster': in_roster,
        'registration': registration,
    }


@transaction.atomic
def cancel_checkin(checkin, operator=None):
    if checkin.status == EventCheckin.Status.CANCELLED:
        return checkin
    checkin.status = EventCheckin.Status.CANCELLED
    checkin.operator = operator or checkin.operator
    checkin.save(update_fields=['status', 'operator', 'updated_at'])
    EventCheckinAttempt.objects.create(
        session=checkin.session,
        church=checkin.church,
        member=checkin.member,
        registration=checkin.registration,
        operator=operator,
        result='CANCELLED',
        detail='Check-in cancelado.',
    )
    return checkin


CHECKIN_ORDERING_MAP = {
    'checked_at': 'checked_at',
    '-checked_at': '-checked_at',
    'member_name': 'registration__name',
    '-member_name': '-registration__name',
    'occurrence_date': 'session__occurrence_date',
    '-occurrence_date': '-session__occurrence_date',
    'source': 'source',
    '-source': '-source',
    'in_roster': 'in_roster',
    '-in_roster': '-in_roster',
}


def _int_param(params, key):
    value = str(params.get(key) or '').strip()
    if value.isdigit():
        return int(value)
    return None


def _bool_param(params, key):
    value = str(params.get(key) or '').strip().lower()
    if value in ('1', 'true', 'yes'):
        return True
    if value in ('0', 'false', 'no'):
        return False
    return None


def filter_checkins(queryset, params):
    search = str(params.get('search') or '').strip()
    if search:
        queryset = queryset.filter(
            Q(member__name__icontains=search)
            | Q(member__card_number__icontains=search)
            | Q(registration__name__icontains=search)
            | Q(registration__code__icontains=search)
        )

    session_id = _int_param(params, 'session')
    if session_id:
        queryset = queryset.filter(session_id=session_id)

    team_member_id = _int_param(params, 'team_member')
    if team_member_id:
        queryset = queryset.filter(operator_id=team_member_id)

    status = str(params.get('status') or '').strip().upper()
    if status in EventCheckin.Status.values:
        queryset = queryset.filter(status=status)

    source = str(params.get('source') or '').strip().upper()
    if source in EventCheckin.Source.values:
        queryset = queryset.filter(source=source)

    in_roster = _bool_param(params, 'in_roster')
    if in_roster is not None:
        queryset = queryset.filter(in_roster=in_roster)

    date_from = str(params.get('date_from') or '').strip()
    date_to = str(params.get('date_to') or '').strip()
    try:
        if date_from:
            queryset = queryset.filter(session__occurrence_date__gte=date.fromisoformat(date_from))
        if date_to:
            queryset = queryset.filter(session__occurrence_date__lte=date.fromisoformat(date_to))
    except ValueError:
        pass

    ordering = str(params.get('ordering') or '-checked_at')
    field = CHECKIN_ORDERING_MAP.get(ordering, '-checked_at')
    return queryset.select_related(
        'session', 'session__event', 'session__checkin_event',
        'member', 'registration', 'operator'
    ).order_by(field, '-id')


def expected_registration_count(sessions):
    events = {}
    for session in sessions:
        event = event_for(session)
        if event:
            events[event.pk] = event
    if not events:
        return 0

    registrations = EventRegistration.objects.filter(
        event_id__in=events.keys(),
        status=EventRegistration.Status.ACTIVE,
    ).exclude(source=EventRegistration.Source.WALK_IN)
    counted_member_ids = {
        member_id for member_id in registrations.values_list('member_id', flat=True)
        if member_id
    }
    total = registrations.count()
    for event in events.values():
        for member_id in event_members(event).values_list('pk', flat=True):
            if member_id not in counted_member_ids:
                counted_member_ids.add(member_id)
                total += 1
    return total
