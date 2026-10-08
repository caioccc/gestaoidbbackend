"""Cria um registro sentinela por igreja e cobre os `discipler` NULL.

Roda entre `0041` (coluna ainda nullable) e `0043` (coluna vira NOT NULL).
O sentinela representa a "responsabilidade da igreja" e substitui o NULL:
`discipler` precisa ser NOT NULL, e não pode ser SET_NULL nem SET_DEFAULT
(o `default` do campo é avaliado uma vez, o que reatribuiria todos os
membros para o sentinela de uma única igreja em ambiente multi-igreja).
"""
from django.db import migrations

# Espelha Member.SENTINEL_NAME. O modelo histórico devolvido por
# `apps.get_model` não carrega atributos customizados da classe.
SENTINEL_NAME = 'Responsável pela Igreja (sem atribuição)'


def seed_sentinels(apps, schema_editor):
    Member = apps.get_model('accounts', 'Member')
    Church = apps.get_model('accounts', 'Church')
    db_alias = schema_editor.connection.alias

    member_church_ids = (
        Member.objects.using(db_alias)
        .exclude(church_id=None)
        .values_list('church_id', flat=True)
        .distinct()
    )
    # Igrejas sem nenhum membro também precisam de sentinela: o formulário de
    # convite público da Fase 3 cria o primeiro visitante direto.
    all_church_ids = Church.objects.using(db_alias).values_list('id', flat=True)
    church_ids = sorted(set(member_church_ids) | set(all_church_ids))

    sentinels = {}
    for church_id in church_ids:
        sentinel = (
            Member.objects.using(db_alias)
            .filter(church_id=church_id, is_sentinel=True)
            .first()
        )
        if sentinel is None:
            sentinel = Member.objects.using(db_alias).create(
                church_id=church_id,
                name=SENTINEL_NAME,
                status='ACTIVE',
                lifecycle_stage='ACTIVE',
                is_sentinel=True,
            )
        sentinels[church_id] = sentinel.id

    for church_id, sentinel_id in sentinels.items():
        Member.objects.using(db_alias).filter(
            church_id=church_id,
            discipler__isnull=True,
        ).exclude(is_sentinel=True).update(discipler_id=sentinel_id)


def unseed_sentinels(apps, schema_editor):
    """Libera os NULL antes de remover os sentinelas (reversão de 0042)."""
    Member = apps.get_model('accounts', 'Member')
    db_alias = schema_editor.connection.alias
    sentinels = Member.objects.using(db_alias).filter(is_sentinel=True)
    sentinels.update(discipler=None)
    sentinels.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0041_member_discipler_foundation'),
    ]

    operations = [
        migrations.RunPython(seed_sentinels, unseed_sentinels),
    ]
