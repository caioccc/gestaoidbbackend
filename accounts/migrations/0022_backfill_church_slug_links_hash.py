"""Backfill de slug e links_hash para igrejas existentes.

A geração acontece no save() do model; esta migração de dados preenche as
linhas já existentes para que todas tenham slug único e hash de links.
"""

from django.db import migrations
from django.utils.text import slugify
import secrets


def backfill(apps, schema_editor):
    Church = apps.get_model('accounts', 'Church')
    if Church.objects.filter(slug__isnull=True).exists():
        used = set(Church.objects.exclude(slug__isnull=True).values_list('slug', flat=True))
        for church in Church.objects.filter(slug__isnull=True).order_by('id'):
            base = (slugify(church.name)[:100] or 'igreja').strip()
            slug = base
            suffix = 2
            while slug in used:
                tail = f'-{suffix}'
                slug = f'{base[:100 - len(tail)]}{tail}'
                suffix += 1
            used.add(slug)
            church.slug = slug
            church.save(update_fields=['slug'])
    for church in Church.objects.filter(links_hash__isnull=True):
        church.links_hash = secrets.token_urlsafe(32)
        church.save(update_fields=['links_hash'])


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0021_church_default_pix_key_church_default_pix_type_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill, reverse_noop),
    ]