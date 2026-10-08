from django.db import migrations

EBD_TEMPLATES = [
    {
        'title': 'Aviso de Aula (EBD)',
        'category': 'EBD_CLASS_ANNOUNCEMENT',
        'content': (
            '📖 {{PRIMEIRO_NOME}}, neste domingo a {{IGREJA}} realizará a Escola '
            'Bíblica Dominical! Teremos turmas para todas as idades e uma palavra '
            'abençoada para você. Venha participar! 🙌'
        ),
    },
    {
        'title': 'Resgate de Ausência (EBD)',
        'category': 'EBD_ABSENCE_RESCUE',
        'content': (
            '👋 Oi, {{PRIMEIRO_NOME}}! Sentimos a sua falta na Escola Bíblica '
            'Dominical da {{IGREJA}}. Queremos saber como você está e te esperamos '
            'de braços abertos na próxima aula. 📖😊'
        ),
    },
    {
        'title': 'Aniversariante da Semana (EBD)',
        'category': 'EBD_BIRTHDAY',
        'content': (
            '🎉 Aniversariante da semana, parabéns, {{PRIMEIRO_NOME}}! Que Deus '
            'abençoe o seu novo ano de vida. Te esperamos na Escola Bíblica '
            'Dominical da {{IGREJA}}! 📖🙏'
        ),
    },
]


def seed_ebd_templates(apps, schema_editor):
    Church = apps.get_model('accounts', 'Church')
    MessageTemplate = apps.get_model('accounts', 'MessageTemplate')
    for church in Church.objects.iterator():
        for data in EBD_TEMPLATES:
            defaults = {k: v for k, v in data.items() if k != 'category'}
            MessageTemplate.objects.get_or_create(
                church=church,
                category=data['category'],
                defaults=defaults,
            )


def remove_ebd_templates(apps, schema_editor):
    MessageTemplate = apps.get_model('accounts', 'MessageTemplate')
    MessageTemplate.objects.filter(
        created_by__isnull=True,
        title__in=[t['title'] for t in EBD_TEMPLATES],
        category__in=[t['category'] for t in EBD_TEMPLATES],
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0047_alter_membercontactlog_category_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_ebd_templates, remove_ebd_templates),
    ]