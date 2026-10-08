from django.db import migrations, models


class Migration(migrations.Migration):
    """Normaliza `Member.discipler` para SET_NULL após o seed do sentinela.

    Só é seguro depois de `0042`, que garante um sentinela por igreja e cobre
    todos os `discipler` NULL.

    O plano original previa `NOT NULL` + `on_delete=PROTECT`, e as duas partes
    se provam incompatíveis com o restante do modelo:

    1. `NOT NULL` não é adiável em SQLite nem Postgres, e o sentinela é o
       próprio responsável — o banco só atribui o pk no INSERT, então a linha
       do sentinela jamais poderia existir.
    2. `PROTECT` em auto-vínculo quebra o cascade de `Church`. O collector
       avalia `PROTECT` antes de adicionar o objeto referenciado a
       `collector.data`, então a ordem de coleta decide — e `Model.delete()`
       customizado não é chamado nesse caminho, não há como contornar.

    A alternativa escolhida preserva o que importa: `SET_NULL` degrada o
    vínculo a "sem atribuição" (visível no filtro `unassigned`) em vez de
    perder visitantes, e `Member.delete()` reaponta para o sentinela da igreja
    na exclusão direta. A obrigatoriedade do responsável para Visitante e Em
    Integração é validada nas duas entradas da aplicação — `MemberSerializer`
    e a action `stage`.
    """

    dependencies = [
        ('accounts', '0042_seed_member_sentinels'),
    ]

    operations = [
        migrations.AlterField(
            model_name='member',
            name='discipler',
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    'Membro ativo da mesma igreja responsável pelo acompanhamento. '
                    'Obrigatório para Visitante e Em Integração.'
                ),
                null=True,
                on_delete=models.SET_NULL,
                related_name='disciplados',
                to='accounts.member',
                verbose_name='Discipulador / Responsável',
            ),
        ),
    ]
