import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Event(models.Model):
    class Source(models.TextChoices):
        CALENDAR = 'CALENDAR', 'Do calendário'
        STANDALONE = 'STANDALONE', 'Avulso'

    church = models.ForeignKey(
        'accounts.Church',
        on_delete=models.CASCADE,
        related_name='events',
    )
    source = models.CharField(
        'Origem', max_length=12, choices=Source.choices, default=Source.STANDALONE,
    )
    calendar_event = models.OneToOneField(
        'finance.CalendarEvent',
        on_delete=models.CASCADE,
        related_name='checkin_event',
        null=True,
        blank=True,
    )
    title = models.CharField('Título', max_length=150)
    date = models.DateField('Data', null=True, blank=True)
    start_time = models.TimeField('Início', null=True, blank=True)
    end_time = models.TimeField('Fim', null=True, blank=True)
    location = models.CharField('Local', max_length=255, blank=True)
    description = models.TextField('Descrição', blank=True)
    registration_token = models.CharField('Token de inscrição', max_length=64, unique=True, blank=True)
    registration_token_is_active = models.BooleanField('Link de inscrição ativo', default=True)
    is_active = models.BooleanField('Ativo', default=True)
    created_at = models.DateTimeField('Criado em', auto_now_add=True)
    updated_at = models.DateTimeField('Atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'Evento'
        verbose_name_plural = 'Eventos'
        ordering = ['-date', '-id']

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.registration_token:
            self.registration_token = secrets.token_urlsafe(24)
        super().save(*args, **kwargs)

    def regenerate_registration_token(self):
        self.registration_token = secrets.token_urlsafe(24)
        self.registration_token_is_active = True
        self.save(update_fields=['registration_token', 'registration_token_is_active'])
        return self.registration_token

    @property
    def registration_url(self):
        return f'{settings.FRONTEND_URL}/inscricao/{self.registration_token}'

    @property
    def start_time_value(self):
        if self.start_time:
            return self.start_time
        if self.calendar_event_id:
            return self.calendar_event.start_time
        return None

    @property
    def end_time_value(self):
        if self.end_time:
            return self.end_time
        if self.calendar_event_id:
            return self.calendar_event.end_time
        return None

    @property
    def date_value(self):
        if self.date:
            return self.date
        if self.calendar_event_id:
            return self.calendar_event.date
        return None


class EventRegistration(models.Model):
    class Kind(models.TextChoices):
        MEMBER = 'MEMBER', 'Membro'
        EXTERNAL = 'EXTERNAL', 'Externo'

    class Source(models.TextChoices):
        SEED = 'SEED', 'Importado do calendário'
        MANUAL = 'MANUAL', 'Adicionado manualmente'
        PUBLIC_FORM = 'PUBLIC_FORM', 'Formulário público'
        WALK_IN = 'WALK_IN', 'Check-in avulso'
        LEGACY = 'LEGACY', 'Migração'

    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Ativo'
        CANCELLED = 'CANCELLED', 'Cancelado'

    CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'

    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name='registrations',
    )
    church = models.ForeignKey(
        'accounts.Church',
        on_delete=models.CASCADE,
        related_name='event_registrations',
    )
    kind = models.CharField(
        'Tipo', max_length=10, choices=Kind.choices, default=Kind.MEMBER,
    )
    member = models.ForeignKey(
        'accounts.Member',
        on_delete=models.CASCADE,
        related_name='event_registrations',
        null=True,
        blank=True,
    )
    name = models.CharField('Nome', max_length=150, blank=True)
    phone = models.CharField('Telefone', max_length=30, blank=True)
    email = models.EmailField('E-mail', blank=True)
    code = models.CharField('Código de validação', max_length=32, unique=True, blank=True)
    status = models.CharField(
        'Situação', max_length=10, choices=Status.choices, default=Status.ACTIVE,
    )
    source = models.CharField(
        'Origem', max_length=12, choices=Source.choices, default=Source.MANUAL,
    )
    registered_at = models.DateTimeField('Inscrição em', default=timezone.now)
    created_at = models.DateTimeField('Criado em', auto_now_add=True)
    updated_at = models.DateTimeField('Atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'Inscrição em evento'
        verbose_name_plural = 'Inscrições em eventos'
        ordering = ['-registered_at', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['event', 'member'],
                condition=Q(member__isnull=False),
                name='event_registration_member_unique',
            ),
        ]
        indexes = [
            models.Index(fields=['church', 'status']),
            models.Index(fields=['event', 'status']),
        ]

    def __str__(self):
        return f'{self.display_name} - {self.event.title}'

    @classmethod
    def new_code(cls):
        while True:
            code = 'EVT' + ''.join(
                secrets.choice(cls.CODE_ALPHABET) for _ in range(8)
            )
            if not EventRegistration.objects.filter(code=code).exists():
                return code

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self.new_code()
        if self.kind == self.Kind.MEMBER and self.member_id and not self.name:
            self.name = self.member.name
        super().save(*args, **kwargs)

    @property
    def display_name(self):
        if self.member_id:
            return self.member.name
        return self.name or self.code

    @property
    def card_number(self):
        if self.member_id:
            return self.member.card_number or self.code
        return self.code

    @property
    def photo(self):
        return self.member.photo if self.member_id else None

    @property
    def qr_url(self):
        return f'{settings.FRONTEND_URL}/validar/{self.code}'

    @property
    def in_roster(self):
        return self.status == self.Status.ACTIVE and self.source != self.Source.WALK_IN


class EventCheckinConfig(models.Model):
    class Mode(models.TextChoices):
        PER_DAY = 'PER_DAY', 'Por dia'
        ONCE = 'ONCE', 'Única'

    event = models.OneToOneField(
        'finance.CalendarEvent',
        on_delete=models.CASCADE,
        related_name='checkin_config',
        null=True,
        blank=True,
    )
    checkin_event = models.OneToOneField(
        Event,
        on_delete=models.CASCADE,
        related_name='checkin_config',
        null=True,
        blank=True,
    )
    church = models.ForeignKey(
        'accounts.Church',
        on_delete=models.CASCADE,
        related_name='event_checkin_configs',
    )
    mode = models.CharField(
        'Modo', max_length=10, choices=Mode.choices, default=Mode.PER_DAY,
    )
    checkin_opens_at = models.DateTimeField('Abertura do check-in', null=True, blank=True)
    checkin_closes_at = models.DateTimeField('Fechamento do check-in', null=True, blank=True)
    generation_start_date = models.DateField('Início da geração de sessões', null=True, blank=True)
    generation_end_date = models.DateField('Fim da geração de sessões', null=True, blank=True)
    is_active = models.BooleanField('Ativo', default=True)
    created_at = models.DateTimeField('Criado em', auto_now_add=True)
    updated_at = models.DateTimeField('Atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'Configuração de check-in de evento'
        verbose_name_plural = 'Configurações de check-in de eventos'
        ordering = ['-created_at']

    def __str__(self):
        return f'Check-in: {self.event_title}'

    @property
    def event_title(self):
        if self.checkin_event_id:
            return self.checkin_event.title
        return self.event.title if self.event_id else ''

    @property
    def source_event(self):
        return self.checkin_event or (self.event.checkin_event if self.event_id else None)


class EventSession(models.Model):
    config = models.ForeignKey(
        EventCheckinConfig,
        on_delete=models.CASCADE,
        related_name='sessions',
    )
    church = models.ForeignKey(
        'accounts.Church',
        on_delete=models.CASCADE,
        related_name='event_checkin_sessions',
    )
    event = models.ForeignKey(
        'finance.CalendarEvent',
        on_delete=models.CASCADE,
        related_name='checkin_sessions',
        null=True,
        blank=True,
    )
    checkin_event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name='checkin_sessions',
        null=True,
        blank=True,
    )
    occurrence_date = models.DateField('Data da ocorrência')
    token = models.CharField('Token público', max_length=64, unique=True)
    token_is_active = models.BooleanField('Token ativo', default=True)
    opens_at = models.DateTimeField('Abertura da sessão', null=True, blank=True)
    closes_at = models.DateTimeField('Fechamento da sessão', null=True, blank=True)
    created_at = models.DateTimeField('Criado em', auto_now_add=True)

    class Meta:
        verbose_name = 'Sessão de check-in'
        verbose_name_plural = 'Sessões de check-in'
        constraints = [
            models.UniqueConstraint(
                fields=['config', 'occurrence_date'],
                name='event_session_config_date_unique',
            ),
        ]
        indexes = [
            models.Index(fields=['church', 'occurrence_date']),
            models.Index(fields=['event', 'occurrence_date']),
            models.Index(fields=['checkin_event', 'occurrence_date']),
        ]
        ordering = ['occurrence_date', 'id']

    def __str__(self):
        return f'{self.event_title} - {self.occurrence_date.isoformat()}'

    @property
    def event_title(self):
        if self.checkin_event_id:
            return self.checkin_event.title
        return self.event.title if self.event_id else ''

    @property
    def start_time(self):
        if self.checkin_event_id:
            return self.checkin_event.start_time_value
        return self.event.start_time if self.event_id else None

    @property
    def end_time(self):
        if self.checkin_event_id:
            return self.checkin_event.end_time_value
        return self.event.end_time if self.event_id else None

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(24)
        super().save(*args, **kwargs)

    def regenerate_token(self):
        self.token = secrets.token_urlsafe(24)
        self.token_is_active = True
        self.save(update_fields=['token', 'token_is_active'])
        return self.token

    @property
    def effective_opens_at(self):
        return self.opens_at or self.config.checkin_opens_at

    @property
    def effective_closes_at(self):
        return self.closes_at or self.config.checkin_closes_at

    def is_open(self, now=None):
        now = now or timezone.now()
        opens_at = self.effective_opens_at
        closes_at = self.effective_closes_at
        if opens_at and now < opens_at:
            return False
        if closes_at and now > closes_at:
            return False
        return self.token_is_active and self.config.is_active


class EventCheckinTeamMember(models.Model):
    class Role(models.TextChoices):
        LEAD = 'LEAD', 'Líder'
        OPERATOR = 'OPERATOR', 'Operador'

    config = models.ForeignKey(
        EventCheckinConfig,
        on_delete=models.CASCADE,
        related_name='team_members',
    )
    church = models.ForeignKey(
        'accounts.Church',
        on_delete=models.CASCADE,
        related_name='event_checkin_team_members',
    )
    member = models.ForeignKey(
        'accounts.Member',
        on_delete=models.CASCADE,
        related_name='event_checkin_memberships',
    )
    role = models.CharField(
        'Função', max_length=10, choices=Role.choices, default=Role.OPERATOR,
    )
    created_at = models.DateTimeField('Criado em', auto_now_add=True)

    class Meta:
        verbose_name = 'Membro da equipe de check-in'
        verbose_name_plural = 'Equipes de check-in'
        constraints = [
            models.UniqueConstraint(
                fields=['config', 'member'],
                name='event_checkin_team_member_unique',
            ),
        ]
        ordering = ['role', 'member__name']

    def __str__(self):
        return f'{self.member.name} - {self.get_role_display()}'


class EventCheckin(models.Model):
    class Source(models.TextChoices):
        SCAN = 'SCAN', 'Scanner'
        MANUAL = 'MANUAL', 'Manual'

    class Status(models.TextChoices):
        CHECKED_IN = 'CHECKED_IN', 'Presente'
        CANCELLED = 'CANCELLED', 'Cancelado'

    session = models.ForeignKey(
        EventSession,
        on_delete=models.CASCADE,
        related_name='checkins',
    )
    church = models.ForeignKey(
        'accounts.Church',
        on_delete=models.CASCADE,
        related_name='event_checkins',
    )
    member = models.ForeignKey(
        'accounts.Member',
        on_delete=models.CASCADE,
        related_name='event_checkins',
        null=True,
        blank=True,
    )
    registration = models.ForeignKey(
        EventRegistration,
        on_delete=models.CASCADE,
        related_name='checkins',
        null=True,
        blank=True,
    )
    operator = models.ForeignKey(
        EventCheckinTeamMember,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='checkins',
    )
    in_roster = models.BooleanField('Participante do evento', default=True)
    source = models.CharField(
        'Origem', max_length=10, choices=Source.choices, default=Source.SCAN,
    )
    status = models.CharField(
        'Situação', max_length=12, choices=Status.choices, default=Status.CHECKED_IN,
    )
    checked_at = models.DateTimeField('Check-in em', default=timezone.now)
    updated_at = models.DateTimeField('Atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'Check-in de evento'
        verbose_name_plural = 'Check-ins de eventos'
        constraints = [
            models.UniqueConstraint(
                fields=['session', 'member'],
                condition=Q(member__isnull=False),
                name='event_checkin_session_member_unique',
            ),
            models.UniqueConstraint(
                fields=['session', 'registration'],
                condition=Q(registration__isnull=False),
                name='event_checkin_session_registration_unique',
            ),
        ]
        indexes = [
            models.Index(fields=['church', 'checked_at']),
            models.Index(fields=['session', 'status']),
        ]
        ordering = ['-checked_at', '-id']

    def __str__(self):
        return f'{self.display_name} - {self.session}'

    @property
    def display_name(self):
        if self.registration_id:
            return self.registration.display_name
        return self.member.name if self.member_id else ''


class EventCheckinAttempt(models.Model):
    session = models.ForeignKey(
        EventSession,
        on_delete=models.CASCADE,
        related_name='attempts',
    )
    church = models.ForeignKey(
        'accounts.Church',
        on_delete=models.CASCADE,
        related_name='event_checkin_attempts',
    )
    member = models.ForeignKey(
        'accounts.Member',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='checkin_attempts',
    )
    registration = models.ForeignKey(
        EventRegistration,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='checkin_attempts',
    )
    operator = models.ForeignKey(
        EventCheckinTeamMember,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='attempts',
    )
    code_fingerprint = models.CharField('Impressão do código', max_length=64, blank=True)
    result = models.CharField('Resultado', max_length=30)
    detail = models.CharField('Detalhe', max_length=180, blank=True)
    created_at = models.DateTimeField('Criado em', auto_now_add=True)

    class Meta:
        verbose_name = 'Tentativa de check-in'
        verbose_name_plural = 'Tentativas de check-in'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['session', 'created_at']),
        ]

    def __str__(self):
        return f'{self.result} - {self.session}'

    @staticmethod
    def fingerprint(code):
        return hashlib.sha256(str(code or '').encode('utf-8')).hexdigest()
