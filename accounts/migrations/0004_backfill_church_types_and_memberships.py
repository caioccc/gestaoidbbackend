"""Backfill de dados: classifica igrejas existentes e semeia vínculos.

- Igrejas ativas (status=ACTIVE) passam a ser Igrejas Independentes/Sedes,
  com is_approved=True (o sistema anterior operava no papel de sede).
- Para cada usuário com igreja vinculada é criado um ChurchMembership com
  papel PASTOR (responsável legado do cadastro), idempotente.
"""
from django.db import migrations


def backfill_forward(apps, schema_editor):
    Church = apps.get_model('accounts', 'Church')
    ChurchMembership = apps.get_model('accounts', 'ChurchMembership')
    User = apps.get_model('accounts', 'User')

    updated = Church.objects.filter(status='ACTIVE').update(
        church_type='INDEPENDENT',
        is_approved=True,
    )

    created = 0
    for user in User.objects.exclude(church__isnull=True).select_related('church'):
        _, was_created = ChurchMembership.objects.get_or_create(
            user=user,
            church=user.church,
            defaults={'role': 'PASTOR'},
        )
        if was_created:
            created += 1

    return (updated, created)


def backfill_reverse(apps, schema_editor):
    # Reversão intencionalmente não suportada: o script é idempotente.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_church_accounting_category_church_church_type_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_forward, backfill_reverse),
    ]