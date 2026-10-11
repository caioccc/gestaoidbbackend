"""Modelos do app boletim: Folder (folheto/boletim) semanal ou mensal."""
import secrets

from django.db import models

from accounts.models import Church, User, WorshipService


class FolderCulto(models.Model):
    """Folder / boletim semanal ou mensal da igreja, com link público."""

    class Tipo(models.TextChoices):
        MENSAL = 'MENSAL', 'Mensal'
        SEMANAL = 'SEMANAL', 'Semanal'

    class CoverTemplate(models.IntegerChoices):
        CLASSICO = 1, 'Clássico'
        MODERNO = 2, 'Moderno'
        ELEGANTE = 3, 'Elegante'
        MINIMAL = 4, 'Minimalista P&B'
        CUSTOM = 5, 'Customizável'

    church = models.ForeignKey(
        Church,
        on_delete=models.CASCADE,
        related_name='folders',
        verbose_name='Igreja',
    )
    tipo = models.CharField(
        'Tipo', max_length=10, choices=Tipo.choices, default=Tipo.MENSAL,
    )
    title = models.CharField('Título do folder', max_length=200)
    subtitle = models.CharField('Subtítulo', max_length=200, blank=True)
    service_type = models.CharField(
        'Tipo de culto (capa)',
        max_length=20,
        choices=WorshipService.ServiceType.choices,
        blank=True,
        default='',
        help_text='Tipo de culto exibido no badge da capa (Pág 1).',
    )
    versiculo = models.CharField('Versículo chave', max_length=255, blank=True)
    cover_template = models.PositiveSmallIntegerField(
        'Template de capa',
        choices=CoverTemplate.choices,
        default=CoverTemplate.CLASSICO,
    )
    theme_color = models.CharField('Cor de tema', max_length=7, blank=True, default='')
    background_color = models.CharField(
        'Cor de fundo', max_length=7, blank=True, default='',
    )
    cover_font = models.CharField(
        'Fonte', max_length=20, blank=True, default='',
        help_text='Chave da família tipográfica (ex.: sans, serif, mono, condensed).',
    )
    mensagem_pastoral = models.TextField('Mensagem pastoral (HTML)', blank=True)
    article_title = models.CharField(
        'Título do artigo/texto', max_length=200, blank=True,
        help_text='Título da seção Mensagem/Artigo (Pág 2).',
    )
    reference_start = models.DateField('Início do período')
    reference_end = models.DateField('Fim do período')
    programacao = models.JSONField('Programação', default=list, blank=True)
    anuncios = models.JSONField('Anúncios', default=list, blank=True)
    aniversariantes = models.JSONField('Aniversariantes', default=list, blank=True)
    qr = models.JSONField('QR codes', default=dict, blank=True)
    social = models.JSONField('Redes sociais exibidas', default=dict, blank=True)
    blocks = models.JSONField('Blocos da contra-capa', default=dict, blank=True)
    block_order = models.JSONField(
        'Ordem dos blocos dinâmicos', default=list, blank=True,
    )
    public_hash = models.CharField(
        'Hash público', max_length=64, unique=True, null=True, blank=True,
        editable=False,
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='folders_created',
        verbose_name='Criado por',
    )
    updated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='folders_updated',
        verbose_name='Atualizado por',
    )
    created_at = models.DateTimeField('Criado em', auto_now_add=True)
    updated_at = models.DateTimeField('Atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'Folder'
        verbose_name_plural = 'Folders'
        ordering = ['-reference_start', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['church', 'tipo', 'reference_start'],
                name='boletim_unique_folder_period',
            ),
        ]

    def __str__(self):
        return f'{self.title} ({self.get_tipo_display()} {self.reference_start})'

    def save(self, *args, **kwargs):
        if not self.public_hash:
            self.public_hash = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def regenerate_public_hash(self):
        self.public_hash = secrets.token_urlsafe(32)
        self.save(update_fields=['public_hash', 'updated_at'])


class FolderCultoRow(models.Model):
    """Linha da agenda de cultos de um folder (Dia / Evento)."""

    folder = models.ForeignKey(
        FolderCulto,
        on_delete=models.CASCADE,
        related_name='rows',
        verbose_name='Folder',
    )
    service_type = models.CharField(
        'Tipo de culto',
        max_length=20,
        choices=WorshipService.ServiceType.choices,
        default=WorshipService.ServiceType.CELEBRACAO,
    )
    date = models.DateField('Data do culto')
    start_time = models.TimeField('Início', null=True, blank=True)
    end_time = models.TimeField('Fim', null=True, blank=True)
    title = models.CharField('Tema / título', max_length=200, blank=True)
    preacher = models.CharField('Preletor / dirigente', max_length=200, blank=True)
    sort_order = models.PositiveIntegerField('Ordem', default=0)

    class Meta:
        verbose_name = 'Culto do folder'
        verbose_name_plural = 'Cultos do folder'
        ordering = ['date', 'sort_order', 'id']

    def __str__(self):
        return f'{self.get_service_type_display()} — {self.date.isoformat()}'