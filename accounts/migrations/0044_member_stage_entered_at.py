from django.db import migrations, models


class Migration(migrations.Migration):
    """Campo que marca a entrada na etapa atual, base do SLA de visitante.

    Fica nulo nos registros já existentes: o serviço de SLA usa `created_at`
    como referência quando o campo é nulo, então nenhum cadastro antigo entra
    como se tivesse iniciado o SLA hoje.
    """

    dependencies = [
        ('accounts', '0043_discipler_required'),
    ]

    operations = [
        migrations.AddField(
            model_name='member',
            name='stage_entered_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name='Entrou na etapa em',
            ),
        ),
    ]
