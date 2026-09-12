from django.db import migrations

DEFAULT_TEMPLATES = [
    {
        'title': 'Aniversário',
        'category': 'BIRTHDAY',
        'content': (
            '🎉 Feliz aniversário, {{PRIMEIRO_NOME}}! Que Deus abençoe o seu novo ano '
            'de vida com muita paz, saúde e alegria.\n\nUm abraço carinhoso de toda a '
            'família {{IGREJA}}. 🙏'
        ),
    },
    {
        'title': 'Boas-Vindas',
        'category': 'WELCOME',
        'content': (
            '🙏 Que alegria ter você conosco na {{IGREJA}}, {{NOME}}! Ficamos muito '
            'felizes com a sua chegada. Se precisar de alguma ajuda ou quiser conversar, '
            'estamos por aqui. 😊'
        ),
    },
    {
        'title': 'Cuidado / Ausência',
        'category': 'CARE',
        'content': (
            '💙 Oi, {{PRIMEIRO_NOME}}! Sentimos a sua falta por aqui e queremos saber '
            'como você está. Se precisar conversar, orar ou de qualquer ajuda, estamos '
            'bem pertinho. Um abraço da {{IGREJA}}.'
        ),
    },
]


def create_default_templates(apps, schema_editor):
    Church = apps.get_model('accounts', 'Church')
    MessageTemplate = apps.get_model('accounts', 'MessageTemplate')
    for church in Church.objects.iterator():
        for data in DEFAULT_TEMPLATES:
            defaults = {k: v for k, v in data.items() if k != 'category'}
            MessageTemplate.objects.get_or_create(
                church=church,
                category=data['category'],
                defaults=defaults,
            )


def remove_default_templates(apps, schema_editor):
    MessageTemplate = apps.get_model('accounts', 'MessageTemplate')
    MessageTemplate.objects.filter(
        created_by__isnull=True,
        title__in=[t['title'] for t in DEFAULT_TEMPLATES],
        category__in=[t['category'] for t in DEFAULT_TEMPLATES],
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0026_member_last_contact_at_member_lifecycle_stage_and_more"),
    ]

    operations = [
        migrations.RunPython(create_default_templates, remove_default_templates),
    ]