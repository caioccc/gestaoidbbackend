"""Exportação das escalas/setlists em texto (WhatsApp) e helpers de organização."""

from datetime import date

from music.models import VolunteerRoster


def _weekday_pt(value: date) -> str:
    days = {
        0: 'Segunda-feira', 1: 'Terça-feira', 2: 'Quarta-feira',
        3: 'Quinta-feira', 4: 'Sexta-feira', 5: 'Sábado', 6: 'Domingo',
    }
    return days[value.weekday()]


def _month_pt(value: date) -> str:
    months = [
        'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho',
        'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro',
    ]
    return months[value.month - 1]


def format_roster_export(roster: VolunteerRoster) -> str:
    """Texto formatado (com emojis) para colar no WhatsApp da equipe."""
    lines: list[str] = []

    date_str = f'{roster.date.day:02d}/{roster.date.month:02d}'
    lines.append(f'🎶 *ESCALA — {roster.theme or "Culto"}*')
    lines.append(f'📅 {_weekday_pt(roster.date)}, {date_str} de {_month_pt(roster.date)}')
    if roster.time:
        lines.append(f'🕓 Horário: {roster.time.strftime("%H:%M")}')
    lines.append('')

    setlist = getattr(roster, 'setlist', None)
    if setlist and setlist.items.exists():
        lines.append('🎵 *Músicas do culto:*')
        for idx, item in enumerate(setlist.items.all(), start=1):
            key = item.custom_key or item.song.church_key or item.song.original_key
            key_str = f'  ➜ Tom: {key}' if key else ''
            lines.append(f'{idx}. {item.song.title}{key_str}')
        lines.append('')

    assignments = list(roster.assignments.select_related('ministry', 'role', 'user'))
    by_ministry: dict[str, list] = {}
    for assignment in assignments:
        by_ministry.setdefault(assignment.ministry.name, []).append(assignment)

    if by_ministry:
        lines.append('👥 *Voluntários:*')
        for ministry_name, items in by_ministry.items():
            lines.append(f'▫️ {ministry_name}:')
            for a in items:
                status_icon = {
                    'PENDING': '⏳', 'CONFIRMED': '✅', 'DECLINED': '❌',
                }.get(a.status, '⏳')
                lines.append(f'    {status_icon} {a.user.name or a.user.email} — {a.role.name}')
        lines.append('')

    if roster.notes:
        lines.append('📝 *Observações:*')
        lines.append(roster.notes)
        lines.append('')

    lines.append('Beijo no coração e até o culto! 🙌')
    return '\n'.join(lines)


def build_roster_board(roster: VolunteerRoster) -> dict[str, list[dict]]:
    """Agrupa as atribuições por ministério (ordem alfabética)."""
    board: dict[str, list[dict]] = {}
    assignments = list(
        roster.assignments.select_related('ministry', 'role', 'user').order_by(
            'role__name'
        )
    )
    for a in assignments:
        board.setdefault(a.ministry.name, []).append({
            'user_name': a.user.name or a.user.email,
            'role_name': a.role.name,
            'status': a.status,
            'ministry_color': a.ministry.color,
        })
    return board