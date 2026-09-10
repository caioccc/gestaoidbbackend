"""Migra a antiga agenda (accounts.ChurchEvent) para o calendário geral.

Copia cada evento da agenda para finance.CalendarEvent com audience=GENERAL
(preservando título, descrição, horário, autor e membros envolvidos). A deleção
do modelo ChurchEvent (accounts.0016) só roda depois desta migração.
"""
from django.db import migrations


def migrate_church_events(apps, schema_editor):
    ChurchEvent = apps.get_model('accounts', 'ChurchEvent')
    CalendarEvent = apps.get_model('finance', 'CalendarEvent')
    for old in ChurchEvent.objects.all():
        ev = CalendarEvent.objects.create(
            church_id=old.church_id,
            audience='GENERAL',
            created_by_id=old.created_by_id,
            title=old.title,
            category='event',
            description=old.description,
            start_time=old.start_time,
            repeat_monthly=False,
            date=old.event_date,
        )
        ev.members.set(list(old.members.values_list('id', flat=True)))


class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0010_calendarevent_audience_calendarevent_created_by_and_more'),
        ('accounts', '0014_churchevent'),
    ]

    operations = [
        migrations.RunPython(
            migrate_church_events,
            migrations.RunPython.noop,
        ),
    ]