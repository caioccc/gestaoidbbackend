import hashlib

from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from cloudinary.models import CloudinaryField

from core.storage import media_storage, raw_storage, church_upload_to


class DepartmentCategory(models.TextChoices):
    """Categorias contábeis / departamentos das entradas e saídas."""

    DIZIMO = 'DIZIMO', 'Dízimo'
    OFERTA = 'OFERTA', 'Ofertas Gerais'
    VISAO_CORPORATIVA = 'VISAO_CORPORATIVA', 'Visão Corporativa'
    CONSTRUCAO = 'CONSTRUCAO', 'Construção'
    ESPECIAL = 'ESPECIAL', 'Especiais'
    MISSOES = 'MISSOES', 'Missões'
    MULHERES = 'MULHERES', 'Mulheres'
    HOMENS = 'HOMENS', 'Homens'
    JOVENS = 'JOVENS', 'Jovens'
    ESC_BIBLICA = 'ESC_BIBLICA', 'Escola Bíblica'
    INFANTIL = 'INFANTIL', 'Infantil'
    ADOLESCENTES = 'ADOLESCENTES', 'Adolescentes'
    CASAIS = 'CASAIS', 'Casais'


class FinancialEntry(models.Model):
    """Controle das Entradas (receitas) por departamento."""

    church = models.ForeignKey(
        'accounts.Church',
        on_delete=models.CASCADE,
        related_name='financial_entries',
        verbose_name='Igreja',
    )
    date = models.DateField('Data')
    service_description = models.CharField(
        'Descrição do Culto / Serviço', max_length=150,
        help_text='Ex: Culto de Ensino, Culto de Ceia',
    )
    category = models.CharField(
        'Categoria', max_length=30, choices=DepartmentCategory.choices,
    )
    amount = models.DecimalField('Valor', max_digits=12, decimal_places=2)
    receipt = models.FileField(
        'Comprovante',
        storage=media_storage,
        upload_to=church_upload_to('receipts'),
        max_length=255,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField('Criado em', auto_now_add=True)

    class Meta:
        verbose_name = 'Entrada'
        verbose_name_plural = 'Entradas'
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f'{self.date} - {self.get_category_display()} - R$ {self.amount}'


class FinancialExit(models.Model):
    """Controle das Saídas (despesas) por departamento."""

    church = models.ForeignKey(
        'accounts.Church',
        on_delete=models.CASCADE,
        related_name='financial_exits',
        verbose_name='Igreja',
    )
    date = models.DateField('Data')
    description = models.CharField(
        'Descrição', max_length=200,
        help_text='Ex: Zeladoria, Energisa, Cagepa',
    )
    category = models.CharField(
        'Categoria', max_length=30, choices=DepartmentCategory.choices,
    )
    amount = models.DecimalField('Valor', max_digits=12, decimal_places=2)
    receipt = models.FileField(
        'Comprovante',
        storage=media_storage,
        upload_to=church_upload_to('receipts'),
        max_length=255,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField('Criado em', auto_now_add=True)

    class Meta:
        verbose_name = 'Saída'
        verbose_name_plural = 'Saídas'
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f'{self.date} - {self.description} - R$ {self.amount}'


class Tither(models.Model):
    """Membro dizimista da congregação."""

    church = models.ForeignKey(
        'accounts.Church',
        on_delete=models.CASCADE,
        related_name='tithers',
        verbose_name='Igreja',
    )
    name = models.CharField('Nome', max_length=150)
    is_anonymous = models.BooleanField('Anônimo', default=False)
    created_at = models.DateTimeField('Criado em', auto_now_add=True)

    class Meta:
        verbose_name = 'Dizimista'
        verbose_name_plural = 'Dizimistas'
        ordering = ['name']

    def __str__(self):
        return self.name if not self.is_anonymous else 'Dizimista Anônimo'


class TitheRecord(models.Model):
    """Registro mensal de dízimo de um determinado dizimista."""

    tither = models.ForeignKey(
        Tither,
        on_delete=models.CASCADE,
        related_name='tithe_records',
        verbose_name='Dizimista',
    )
    year = models.PositiveIntegerField('Ano')
    month = models.PositiveIntegerField(
        'Mês', validators=[MinValueValidator(1), MaxValueValidator(12)],
    )
    amount = models.DecimalField('Valor', max_digits=12, decimal_places=2)

    class Meta:
        verbose_name = 'Registro de Dízimo'
        verbose_name_plural = 'Registros de Dízimos'
        unique_together = ('tither', 'year', 'month')
        ordering = ['-year', '-month']

    def __str__(self):
        return f'{self.tither} - {self.month}/{self.year} - R$ {self.amount}'


class CalendarEvent(models.Model):
    """Calendário geral da igreja (agenda da secretaria + financeiro do tesoureiro).

    `audience=GENERAL`: eventos criados pela secretaria — visíveis a qualquer
    usuário com igreja ativa e, anonimamente, na página pública por hash.
    `audience=FINANCE`: eventos do tesoureiro — visíveis e editáveis apenas por
    TESOUREIRO/PASTOR/ADMIN (a secretaria não os enxerga).

    Suporta eventos recorrentes — mensais (repeat_monthly=True, usando
    month/day), semanais/quinzenais (repeat_weekly=True, usando weekdays +
    repeat_interval + date como âncora) — e eventos pontuais
    (repeat_monthly=False/repeat_weekly=False, usando date).
    """

    class Category(models.TextChoices):
        BILL = 'bill', 'Conta fixa'
        DEADLINE = 'deadline', 'Prazo / Vencimento'
        MEETING = 'meeting', 'Reunião'
        EVENT = 'event', 'Evento'
        CULTO = 'culto', 'Culto'
        ENSAIO = 'ensaio', 'Ensaio'

    class Audience(models.TextChoices):
        GENERAL = 'GENERAL', 'Agenda da Igreja (Secretaria)'
        FINANCE = 'FINANCE', 'Financeiro (Tesouraria)'

    church = models.ForeignKey(
        'accounts.Church',
        on_delete=models.CASCADE,
        related_name='calendar_events',
        verbose_name='Igreja',
    )
    audience = models.CharField(
        'Audiência', max_length=20, choices=Audience.choices,
        default=Audience.FINANCE,
        help_text='GENERAL: visível a todos e na página pública. FINANCE: só tesouraria.',
    )
    created_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='calendar_events_created',
        verbose_name='Criado por',
    )
    title = models.CharField('Título', max_length=150)
    category = models.CharField(
        'Categoria', max_length=20, choices=Category.choices, default=Category.EVENT,
    )
    color = models.CharField(
        'Cor', max_length=7, blank=True, default='',
        help_text='Cor em hex (ex.: #228be6) exibida no calendário.',
    )
    description = models.TextField('Descrição', blank=True)
    start_time = models.TimeField('Horário de início', null=True, blank=True)
    end_time = models.TimeField('Horário de fim', null=True, blank=True,
                                help_text='Usado p/ blocos de horário (ex.: 9h às 12h).')
    members = models.ManyToManyField(
        'accounts.Member',
        related_name='calendar_events',
        blank=True,
        verbose_name='Membros envolvidos',
    )
    repeat_monthly = models.BooleanField('Recorre mensalmente', default=False)
    repeat_weekly = models.BooleanField('Recorre semanalmente', default=False)
    weekdays = models.JSONField(
        'Dias da semana (ISO: 0=Seg..6=Dom)',
        default=list,
        blank=True,
        help_text='Usado quando repeat_weekly=True.',
    )
    repeat_interval = models.PositiveIntegerField(
        'Intervalo em semanas',
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(2)],
        help_text='1 = toda semana; 2 = quinzenal (âncora = date).',
    )
    repeat_end_date = models.DateField(
        'Fim da recorrência', null=True, blank=True,
        help_text='Último dia de repetição (opcional).',
    )
    date = models.DateField(
        'Data (evento pontual)', null=True, blank=True,
        help_text='Evento pontual, ou âncora/início de eventos semanais/quinzenais.',
    )
    month = models.PositiveIntegerField(
        'Mês (recorrente)',
        validators=[MinValueValidator(1), MaxValueValidator(12)],
        null=True,
        blank=True,
    )
    day = models.PositiveIntegerField(
        'Dia (recorrente)',
        validators=[MinValueValidator(1), MaxValueValidator(31)],
        null=True,
        blank=True,
    )
    repeat_monthly_weekday = models.PositiveIntegerField(
        'Dia da semana (mensal por ocorrência)',
        validators=[MinValueValidator(0), MaxValueValidator(6)],
        null=True,
        blank=True,
        help_text='Usado com repeat_monthly_ordinal p/ cultos de frequência '
                  '(ex.: primeiro domingo do mês → ordinal=1, weekday=6).',
    )
    repeat_monthly_ordinal = models.IntegerField(
        'Ocorrência no mês',
        validators=[MinValueValidator(-1), MaxValueValidator(5)],
        null=True,
        blank=True,
        help_text='1..5 = 1ª..5ª ocorrência; -1 = última do mês. '
                  'Use com repeat_monthly_weekday.',
    )
    created_at = models.DateTimeField('Criado em', auto_now_add=True)

    class Meta:
        verbose_name = 'Evento do Calendário'
        verbose_name_plural = 'Eventos do Calendário'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.title} - {self.church}'


class MonthlyClosing(models.Model):
    """Fechamento mensal (Caixa IDB) da congregação."""

    church = models.ForeignKey(
        'accounts.Church',
        on_delete=models.CASCADE,
        related_name='monthly_closings',
        verbose_name='Igreja',
    )
    year = models.PositiveIntegerField('Ano')
    month = models.PositiveIntegerField(
        'Mês', validators=[MinValueValidator(1), MaxValueValidator(12)],
    )
    is_closed = models.BooleanField('Fechado', default=False)
    previous_balance = models.DecimalField(
        'Saldo Anterior', max_digits=14, decimal_places=2, default=0,
    )
    total_entries = models.DecimalField(
        'Total de Entradas', max_digits=14, decimal_places=2, default=0,
    )
    total_exits = models.DecimalField(
        'Total de Saídas', max_digits=14, decimal_places=2, default=0,
    )
    final_balance = models.DecimalField(
        'Saldo Final', max_digits=14, decimal_places=2, default=0,
    )

    class Meta:
        verbose_name = 'Fechamento Mensal'
        verbose_name_plural = 'Fechamentos Mensais'
        unique_together = ('church', 'year', 'month')

    def __str__(self):
        return f'Caixa {self.church} - {self.month}/{self.year}'

    def calculate_final_balance(self) -> None:
        """Calcula o saldo final a partir do saldo anterior e os totais."""
        self.final_balance = (
            self.previous_balance + self.total_entries - self.total_exits
        )


class MonthlyValidation(models.Model):
    """Validação mensal da Rotina Contábil IDB (Tesouraria/Liderança).

    Registra o resultado do checklist (fechamento, prebenda, repetição de
    dizimistas e classificações) e o fluxo de aprovação em duas etapas:
    Tesouraria (igreja) → Liderança (staff).
    """

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pendente'
        TREASURY_APPROVED = 'TREASURY_APPROVED', 'Aprovado pela Tesouraria'
        LEADERSHIP_APPROVED = 'LEADERSHIP_APPROVED', 'Aprovado pela Liderança'
        REJECTED = 'REJECTED', 'Rejeitado'

    church = models.ForeignKey(
        'accounts.Church',
        on_delete=models.CASCADE,
        related_name='monthly_validations',
        verbose_name='Igreja',
    )
    year = models.PositiveIntegerField('Ano')
    month = models.PositiveIntegerField(
        'Mês', validators=[MinValueValidator(1), MaxValueValidator(12)],
    )
    status = models.CharField(
        'Status', max_length=30, choices=Status.choices, default=Status.PENDING,
    )
    checks = models.JSONField('Resultado dos checks', default=dict, blank=True)
    note = models.CharField('Observação', max_length=500, blank=True)
    approved_by_treasury = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
        verbose_name='Aprovado pela Tesouraria',
    )
    approved_by_leadership = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
        verbose_name='Aprovado pela Liderança',
    )
    treasury_approved_at = models.DateTimeField(
        'Tesouraria aprovou em', null=True, blank=True,
    )
    leadership_approved_at = models.DateTimeField(
        'Liderança aprovou em', null=True, blank=True,
    )
    rejected_by_treasury = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
        verbose_name='Rejeitado pela Tesouraria',
    )
    treasury_rejected_at = models.DateTimeField(
        'Tesouraria rejeitou em', null=True, blank=True,
    )
    rejected_by_leadership = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
        verbose_name='Rejeitado pela Liderança',
    )
    leadership_rejected_at = models.DateTimeField(
        'Liderança rejeitou em', null=True, blank=True,
    )
    treasury_photo_url = CloudinaryField(
        'Foto do signatário (Tesouraria)', null=True, blank=True,
        folder='gestao_idb/validations',
    )
    treasury_signature_url = CloudinaryField(
        'Assinatura digital (Tesouraria)', null=True, blank=True,
        folder='gestao_idb/validations',
    )
    signature_hash = models.CharField(
        'Hash da Assinatura', max_length=64, blank=True, editable=False,
        help_text='SHA-256 da imagem da assinatura digital.',
    )
    created_at = models.DateTimeField('Criado em', auto_now_add=True)
    updated_at = models.DateTimeField('Atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'Validação Mensal'
        verbose_name_plural = 'Validações Mensais'
        unique_together = ('church', 'year', 'month')

    def __str__(self):
        return f'Validação {self.church} - {self.month}/{self.year}'

    def recompute_status(self):
        """Recomputa o status agregado a partir das aprovações/rejeições por lado.

        Cada perfil (Tesouraria e Liderança) pode aprovar de forma independente;
        o status é apenas um resumo para exibição.
        """
        if self.treasury_approved_at and self.leadership_approved_at:
            self.status = self.Status.LEADERSHIP_APPROVED
        elif self.treasury_approved_at:
            self.status = self.Status.TREASURY_APPROVED
        elif self.treasury_rejected_at or self.leadership_rejected_at:
            self.status = self.Status.REJECTED
        else:
            self.status = self.Status.PENDING

    def _compute_signature_hash(self):
        """SHA-256 da imagem da assinatura digital (fingerprint de integridade)."""
        sig = self.treasury_signature_url
        if sig is None:
            return None
        if hasattr(sig, 'read'):
            try:
                sig.seek(0)
                data = sig.read()
                sig.seek(0)
                sha256 = hashlib.sha256(data).hexdigest()
                return sha256
            except Exception:
                pass
        return hashlib.sha256(str(sig).encode('utf-8')).hexdigest()

    def save(self, *args, **kwargs):
        """Gera o hash da assinatura quando um novo arquivo é anexado.

        Só recalcula quando o campo ainda guarda um arquivo recém-enviado;
        em saves posteriores (já com o resource Cloudinary) preserva o hash.
        """
        if hasattr(self.treasury_signature_url, 'read'):
            self.signature_hash = self._compute_signature_hash() or ''
        super().save(*args, **kwargs)


class FinancialReceipt(models.Model):
    """Recibo Financeiro Eclesial (saída/pagamento ou entrada/doação).

    Numeração sequencial por igreja e ano (ex.: 001/2026), sem saltos ou
    duplicidades — garantida pelo número único em (`church`, `year`, `number`).

    O PDF oficial (A4, 2 vias) é gerado no backend e salvo no Cloudinary
    (raw_storage). Quando o mês da emissão está fechado no Caixa IDB
    (MonthlyClosing.is_closed), o recibo fica somente leitura (download/
    reimpressão), exceto para admin/staff.
    """

    class Type(models.TextChoices):
        SAIDA = 'SAIDA', 'Saída / Pagamento'
        ENTRADA = 'ENTRADA', 'Entrada / Doação'

    church = models.ForeignKey(
        'accounts.Church',
        on_delete=models.CASCADE,
        related_name='financial_receipts',
        verbose_name='Igreja',
    )
    year = models.PositiveIntegerField('Ano')
    number = models.PositiveIntegerField('Número')
    receipt_type = models.CharField(
        'Tipo', max_length=10, choices=Type.choices,
    )
    date = models.DateField('Data de emissão')
    amount = models.DecimalField('Valor', max_digits=12, decimal_places=2)
    description = models.CharField('Descrição', max_length=200)
    category = models.CharField(
        'Categoria (lançamento automático)',
        max_length=30,
        choices=DepartmentCategory.choices,
        blank=True,
        help_text='Categoria usada quando o recibo também lança o movimento no caixa.',
    )

    # Dados do favorecido (pagamento) / doador (doação).
    favored_name = models.CharField('Nome completo', max_length=150)
    favored_document = models.CharField('CPF / CNPJ', max_length=20, blank=True)
    favored_rg = models.CharField('RG', max_length=20, blank=True)
    favored_city = models.CharField('Cidade', max_length=100, blank=True)
    favored_state = models.CharField('UF', max_length=2, blank=True)
    pix = models.CharField('Chave PIX', max_length=140, blank=True)

    # Vinculações opcionais (autofill a partir de membro / lançamento do caixa).
    member = models.ForeignKey(
        'accounts.Member',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='financial_receipts',
        verbose_name='Membro vinculado',
    )
    entry = models.ForeignKey(
        FinancialEntry,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='receipts',
        verbose_name='Entrada vinculada',
    )
    exit = models.ForeignKey(
        FinancialExit,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='receipts',
        verbose_name='Saída vinculada',
    )
    auto_launched = models.BooleanField(
        'Lançado automaticamente no caixa',
        default=False,
        help_text='True quando o recibo também gerou o lançamento (entrada/saída).',
    )
    pdf = models.FileField(
        'PDF (2 vias)',
        storage=raw_storage,
        upload_to=church_upload_to('receipts'),
        max_length=255,
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        verbose_name='Emitido por',
    )
    created_at = models.DateTimeField('Criado em', auto_now_add=True)
    updated_at = models.DateTimeField('Atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'Recibo Financeiro'
        verbose_name_plural = 'Recibos Financeiros'
        ordering = ['-year', '-number']
        unique_together = ('church', 'year', 'number')

    def __str__(self):
        return (
            f'Nº {self.full_number} - {self.get_receipt_type_display()}'
            f' - R$ {self.amount}'
        )

    @property
    def full_number(self) -> str:
        """Número formatado do recibo (ex.: '001/2026')."""
        return f'{self.number:03d}/{self.year}'

    def is_locked(self) -> bool:
        """Mês de emissão fechado no Caixa IDB → recibo somente leitura."""
        closing = MonthlyClosing.objects.filter(
            church=self.church,
            year=self.date.year,
            month=self.date.month,
        ).first()
        return bool(closing and closing.is_closed)
