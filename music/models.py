from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from core.storage import church_upload_to, media_storage


class Ministry(models.Model):
    """Ministério (Louvor, Som, Mídia, Recepção) com cor e líder vinculados."""

    church = models.ForeignKey(
        'accounts.Church',
        on_delete=models.CASCADE,
        related_name='music_ministries',
        verbose_name='Igreja',
    )
    name = models.CharField('Nome', max_length=100)
    color = models.CharField(
        'Cor (hex)', max_length=7, help_text='Usada nos quadros e exportações.',
        default='#7c3aed',
    )
    leader = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='led_ministries',
        null=True,
        blank=True,
        verbose_name='Líder',
    )
    is_active = models.BooleanField('Ativo', default=True)
    created_at = models.DateTimeField('Criado em', auto_now_add=True)

    class Meta:
        verbose_name = 'Ministério'
        verbose_name_plural = 'Ministérios'
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['church', 'name'], name='unique_church_ministry_name'
            )
        ]

    def __str__(self):
        return self.name


class MinistryRole(models.Model):
    """Função escalável dentro de um ministério (Vocal, Violão, Operador...)."""

    ministry = models.ForeignKey(
        Ministry,
        on_delete=models.CASCADE,
        related_name='roles',
        verbose_name='Ministério',
    )
    name = models.CharField('Função', max_length=100)
    is_active = models.BooleanField('Ativo', default=True)

    class Meta:
        verbose_name = 'Função do Ministério'
        verbose_name_plural = 'Funções dos Ministérios'
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['ministry', 'name'], name='unique_ministry_role_name'
            )
        ]

    def __str__(self):
        return f'{self.ministry.name} — {self.name}'


class VolunteerRoster(models.Model):
    """Escala de voluntários para um culto/evento."""

    church = models.ForeignKey(
        'accounts.Church',
        on_delete=models.CASCADE,
        related_name='volunteer_rosters',
        verbose_name='Igreja',
    )
    date = models.DateField('Data')
    time = models.TimeField('Horário', null=True, blank=True)
    theme = models.CharField('Tema do culto', max_length=200, blank=True)
    notes = models.TextField('Notas', blank=True)
    is_published = models.BooleanField(
        'Publicada (visível para músicos)', default=False,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='created_rosters',
        null=True,
        blank=True,
        verbose_name='Criado por',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Escala de Voluntários'
        verbose_name_plural = 'Escalas de Voluntários'
        ordering = ['-date', 'time']

    def __str__(self):
        return f'Culto {self.date} — {self.theme or "Sem tema"}'


class RosterAssignment(models.Model):
    """Atribuição de um voluntário a um culto (ministério + função + status)."""

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pendente'
        CONFIRMED = 'CONFIRMED', 'Confirmado'
        DECLINED = 'DECLINED', 'Recusado'

    roster = models.ForeignKey(
        VolunteerRoster,
        on_delete=models.CASCADE,
        related_name='assignments',
        verbose_name='Escala',
    )
    ministry = models.ForeignKey(
        Ministry,
        on_delete=models.PROTECT,
        related_name='assignments',
        verbose_name='Ministério',
    )
    role = models.ForeignKey(
        MinistryRole,
        on_delete=models.CASCADE,
        related_name='assignments',
        verbose_name='Função',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='roster_assignments',
        verbose_name='Voluntário',
    )
    status = models.CharField(
        'Status', max_length=20, choices=Status.choices, default=Status.PENDING,
    )
    notes = models.TextField('Observações', blank=True)

    class Meta:
        verbose_name = 'Atribuição de Escala'
        verbose_name_plural = 'Atribuições de Escala'
        ordering = ['roster__date', 'ministry__name', 'role__name']
        constraints = [
            models.UniqueConstraint(
                fields=['roster', 'ministry', 'role', 'user'],
                name='unique_roster_ministry_role_user',
            )
        ]

    def __str__(self):
        return f'{self.user.name} — {self.role.name}'

    def clean(self):
        super().clean()
        if self.roster_id and self.user_id:
            duplicate = (
                RosterAssignment.objects.filter(
                    roster=self.roster,
                    user=self.user,
                )
                .exclude(pk=self.pk)
                .exclude(role=self.role)
            ).first()
            if duplicate:
                raise ValidationError(
                    {
                        'user': (
                            f'{self.user.name} já está escalado como '
                            f'"{duplicate.role.name}" no mesmo culto ({self.roster.date}). '
                            'Escolha um voluntário/função diferente para evitar conflito.'
                        )
                    }
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Band(models.Model):
    """Banda fixa da igreja (Águia da Paz, Siloé, ...) com repertório próprio."""

    church = models.ForeignKey(
        'accounts.Church',
        on_delete=models.CASCADE,
        related_name='music_bands',
        verbose_name='Igreja',
    )
    name = models.CharField('Nome', max_length=100)
    color = models.CharField(
        'Cor (hex)', max_length=7, help_text='Usada nos rótulos e filtros.',
        default='#7048e8',
    )
    photo = models.FileField(
        'Foto da banda',
        storage=media_storage,
        upload_to=church_upload_to('bands'),
        max_length=255,
        null=True,
        blank=True,
    )
    leader = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='led_bands',
        null=True,
        blank=True,
        verbose_name='Líder',
    )
    is_active = models.BooleanField('Ativo', default=True)
    created_at = models.DateTimeField('Criado em', auto_now_add=True)

    class Meta:
        verbose_name = 'Banda'
        verbose_name_plural = 'Bandas'
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['church', 'name'], name='unique_church_band_name'
            )
        ]

    def __str__(self):
        return self.name


class Song(models.Model):
    """Música do repertório: link YouTube, tom, BPM, acordes com timing e letra."""

    class ChordStatus(models.TextChoices):
        PENDING = 'PENDING', 'Fila (aguardando extração)'
        PROCESSING = 'PROCESSING', 'Processando'
        COMPLETED = 'COMPLETED', 'Concluída'
        FAILED = 'FAILED', 'Falhou'
        MANUAL = 'MANUAL', 'Manual'

    church = models.ForeignKey(
        'accounts.Church',
        on_delete=models.CASCADE,
        related_name='songs',
        verbose_name='Igreja',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_songs',
        verbose_name='Criado por',
        help_text='Usuário que adicionou a música ao repertório.',
    )
    band = models.ForeignKey(
        Band,
        on_delete=models.SET_NULL,
        related_name='songs',
        null=True,
        blank=True,
        verbose_name='Banda',
    )
    title = models.CharField('Título', max_length=200)
    artist = models.CharField('Artista', max_length=200, blank=True)
    youtube_id = models.CharField('YouTube ID', max_length=20, blank=True)
    youtube_title = models.CharField('Título no YouTube', max_length=300, blank=True)
    thumbnail_url = models.URLField('Thumbnail', blank=True)
    duration_seconds = models.PositiveIntegerField('Duração (s)', null=True, blank=True)
    original_key = models.CharField('Tom original', max_length=20, blank=True)
    church_key = models.CharField('Tom da congregação', max_length=20, blank=True)
    bpm = models.PositiveIntegerField('BPM', null=True, blank=True)
    time_signature = models.CharField('Compasso', max_length=10, blank=True, default='4/4')
    chords = models.TextField('Acordes (bruto)', blank=True)
    chords_json = models.JSONField('Acordes com timing', null=True, blank=True)
    lyrics = models.TextField('Letra', blank=True)
    tags = models.CharField('Tags (separadas por vírgula)', max_length=500, blank=True)
    times_played = models.PositiveIntegerField('Vezes tocada', default=0)
    last_played = models.DateField('Última vez', null=True, blank=True)
    is_active = models.BooleanField('Ativo', default=True)
    chord_status = models.CharField(
        'Status da cifra', max_length=20, choices=ChordStatus.choices,
        default=ChordStatus.PENDING, db_index=True,
    )
    chord_error = models.TextField('Erro da extração', blank=True, default='')
    chord_retries = models.PositiveSmallIntegerField('Tentativas de extração', default=0)
    chord_processed_at = models.DateTimeField(
        'Processado em', null=True, blank=True,
    )
    created_at = models.DateTimeField('Criado em', auto_now_add=True)
    updated_at = models.DateTimeField('Atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'Música'
        verbose_name_plural = 'Músicas (Repertório)'
        ordering = ['title']

    def __str__(self):
        return f'{self.title} — {self.artist}'


class WorshipSetlist(models.Model):
    """Setlist de um culto (vinculada à escala)."""

    roster = models.OneToOneField(
        VolunteerRoster,
        on_delete=models.CASCADE,
        related_name='setlist',
        verbose_name='Escala',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='Criado por',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Setlist'
        verbose_name_plural = 'Setlists'

    def __str__(self):
        return f'Setlist de {self.roster.date}'


class SetlistItem(models.Model):
    """Música ordenada dentro de um setlist (com tom customizado e notas)."""

    setlist = models.ForeignKey(
        WorshipSetlist,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name='Setlist',
    )
    song = models.ForeignKey(
        Song,
        on_delete=models.CASCADE,
        related_name='setlist_items',
        verbose_name='Música',
    )
    order = models.PositiveIntegerField('Ordem', default=0)
    custom_key = models.CharField('Tom (customizado)', max_length=20, blank=True)
    notes = models.TextField('Notas dinâmicas', blank=True)

    class Meta:
        verbose_name = 'Item do Setlist'
        verbose_name_plural = 'Itens do Setlist'
        ordering = ['order']
        constraints = [
            models.UniqueConstraint(
                fields=['setlist', 'order'], name='unique_setlist_item_order'
            )
        ]

    def __str__(self):
        return f'{self.order}. {self.song.title}'


class BandSetlist(models.Model):
    """Setlist de uma banda: data, descrição, tema (opcional) e músicas do repertório."""

    church = models.ForeignKey(
        'accounts.Church',
        on_delete=models.CASCADE,
        related_name='band_setlists',
        verbose_name='Igreja',
    )
    band = models.ForeignKey(
        Band,
        on_delete=models.SET_NULL,
        related_name='setlists',
        null=True,
        blank=True,
        verbose_name='Banda',
    )
    date = models.DateField('Data')
    description = models.CharField('Descrição', max_length=200)
    theme = models.CharField('Tema (opcional)', max_length=200, blank=True)
    notes = models.TextField('Observações', blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_band_setlists',
        verbose_name='Criado por',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Setlist'
        verbose_name_plural = 'Setlists'
        ordering = ['-date', 'created_at']

    def __str__(self):
        return f'{self.date} — {self.description}'


class BandSetlistItem(models.Model):
    """Música ordenada dentro de um setlist de banda."""

    setlist = models.ForeignKey(
        BandSetlist,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name='Setlist',
    )
    song = models.ForeignKey(
        Song,
        on_delete=models.CASCADE,
        related_name='band_setlist_items',
        verbose_name='Música',
    )
    order = models.PositiveIntegerField('Ordem', default=0)
    custom_key = models.CharField('Tom (customizado)', max_length=20, blank=True)
    notes = models.TextField('Notas dinâmicas', blank=True)

    class Meta:
        verbose_name = 'Item do Setlist'
        verbose_name_plural = 'Itens dos Setlists'
        ordering = ['order']
        constraints = [
            models.UniqueConstraint(
                fields=['setlist', 'order'], name='unique_band_setlist_item_order'
            )
        ]

    def __str__(self):
        return f'{self.order}. {self.song.title}'