"""Serviços do app boletim: expansão de recorrência, aniversariantes etc."""
from datetime import date, timedelta

from django.conf import settings

from accounts.models import Member
from accounts.services import member_age
from finance.models import CalendarEvent

#: Categorias do calendário que jamais viram linha de agenda de folder.
PRIVATE_EVENT_CATEGORIES = (
    CalendarEvent.Category.BILL,
    CalendarEvent.Category.DEADLINE,
)


def _days_in_month(day: date) -> int:
    import calendar
    return calendar.monthrange(day.year, day.month)[1]


def event_occurs_on(ev: CalendarEvent, day: date) -> bool:
    """Resolve se um evento do calendário ocorre em `day`.

    Espelha a lógica de recorrência do frontend
    (`src/utils/calendarRecurrence.ts`): mensal fixo no dia ou "n-ésimo dia da
    semana", semanal (intervalo em semanas a partir da âncora) e pontual.
    `date.weekday()` (0=Seg..6=Dom) coincide com o ISO usado no frontend.
    """
    if ev.repeat_monthly:
        nth_wd = ev.repeat_monthly_weekday
        nth_ord = ev.repeat_monthly_ordinal
        if nth_wd is not None and nth_ord is not None:
            occ = [
                k for k in range(1, _days_in_month(day) + 1)
                if date(day.year, day.month, k).weekday() == nth_wd
            ]
            target = occ[-1] if nth_ord == -1 else (
                occ[nth_ord - 1] if 0 < nth_ord <= len(occ) else None
            )
            return target == day.day
        return ev.day == day.day

    if ev.repeat_weekly:
        if day.weekday() not in (ev.weekdays or []):
            return False
        if ev.repeat_end_date and day > ev.repeat_end_date:
            return False
        if ev.date is not None:
            if day < ev.date:
                return False
            interval = ev.repeat_interval or 1
            if (day - ev.date).days % (7 * interval) != 0:
                return False
        return True

    if ev.date is not None:
        return day == ev.date
    return False


def expand_calendar_events(events, start: date, end: date):
    """Expande eventos em ocorrências (data, evento) dentro da janela, em ordem."""
    out = []
    day = start
    while day <= end:
        for ev in events:
            if event_occurs_on(ev, day):
                out.append((day, ev))
        day += timedelta(days=1)
    return out


def agenda_calendar_events(church, start: date, end: date):
    """Eventos gerais da igreja na janela (exclui categorias financeiras)."""
    return CalendarEvent.objects.filter(
        church=church,
        audience=CalendarEvent.Audience.GENERAL,
    ).exclude(category__in=PRIVATE_EVENT_CATEGORIES)


def event_service_type(ev: CalendarEvent):
    """Mapeia uma categoria de evento para um tipo de culto (editável depois)."""
    if ev.category == CalendarEvent.Category.CULTO:
        return 'CELEBRACAO'
    return 'OUTRO'


def folder_birthdays(folder):
    """Aniversariantes ativos cujo aniversário (dia/mês) cai no período."""
    return birthdays_for_period(folder.church, folder.reference_start, folder.reference_end)


def birthdays_for_period(church, start: date, end: date):
    """Aniversariantes ativos cujo aniversário (dia/mês) cai na janela."""
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    md_pairs = {(d.month, d.day) for d in days}
    months = list({m for m, _ in md_pairs})
    members = Member.objects.filter(
        church=church,
        status=Member.Status.ACTIVE,
        birth_date__isnull=False,
        birth_date__month__in=months,
    )
    seen = set()
    rows = []
    for m in members:
        if m.id in seen:
            continue
        md = (m.birth_date.month, m.birth_date.day)
        if md not in md_pairs:
            continue
        seen.add(m.id)
        rows.append({
            'id': m.id,
            'name': m.name,
            'day': m.birth_date.day,
            'month': m.birth_date.month,
            'age': member_age(m.birth_date),
            'phone': m.phone or '',
        })
    rows.sort(key=lambda r: (r['month'], r['day']))
    return rows


def preview_agenda_rows(church, start: date, end: date):
    """Linhas de agenda (não persistidas) a partir do calendário, na janela."""
    events = list(agenda_calendar_events(church, start, end))
    rows = []
    for day, ev in expand_calendar_events(events, start, end):
        rows.append({
            'service_type': event_service_type(ev),
            'date': day.isoformat(),
            'start_time': ev.start_time.isoformat() if ev.start_time else None,
            'end_time': ev.end_time.isoformat() if ev.end_time else None,
            'title': ev.title or '',
            'preacher': '',
            'sort_order': len(rows),
        })
    return rows


def default_qr_urls(church):
    """URLs padrão dos QR codes do folder (pré-preenchidas pelo frontend)."""
    fe = settings.FRONTEND_URL
    return {
        'visita': {
            'enabled': False,
            'url': '',
        },
        'oracao': {
            'enabled': False,
            'url': f'{fe}/p/{church.slug}',
        },
        'agenda': {
            'enabled': False,
            'url': (
                f'{fe}/calendario/{church.calendar_public_hash}'
                if church.calendar_public_hash else ''
            ),
        },
        'cadastro': {
            'enabled': False,
            'url': (
                f'{fe}/formulario/{church.member_form_hash}'
                if church.member_form_hash else ''
            ),
        },
    }