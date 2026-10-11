import django.db.models

from django.db import migrations


def backfill_service_type(apps, schema_editor):
    FolderCulto = apps.get_model('boletim', 'FolderCulto')
    FolderCultoRow = apps.get_model('boletim', 'FolderCultoRow')
    for folder in FolderCulto.objects.filter(service_type=''):
        first = (
            FolderCultoRow.objects.filter(folder=folder, service_type__gt='')
            .order_by('id')
            .first()
        )
        if first:
            folder.service_type = first.service_type
            folder.save(update_fields=['service_type'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('boletim', '0007_folderculto_service_type'),
    ]

    operations = [
        migrations.RunPython(backfill_service_type, noop),
    ]