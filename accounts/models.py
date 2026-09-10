from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from decimal import Decimal
import secrets
import uuid

from cloudinary.models import CloudinaryField

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager

member_docs_storage = FileSystemStorage(
    location=settings.MEDIA_ROOT,
    base_url=settings.MEDIA_URL,
)


class MemberDocumentsStorage(FileSystemStorage):
    """Armazenamento local dos documentos dos membros.

    Deriva `location`/`base_url` das settings em tempo de instanciação para que
    a deconstruct cleanup fique portátil (sem caminho absoluto nas migrações).

    Uso: documents FileField(storage=member_doc_storage, ...).
    """

    def __init__(self, **kwargs):
        kwargs.setdefault('location', settings.MEDIA_ROOT)
        kwargs.setdefault('base_url', settings.MEDIA_URL)
        super().__init__(**kwargs)

    def deconstruct(self):
        return (
            'accounts.models.MemberDocumentsStorage',
            [],
            {},
        )


member_doc_storage = MemberDocumentsStorage()

class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('O e-mail é obrigatório.')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        return self.create_user(email, password, **extra_fields)

class Church(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pendente de Aprovação'),
        ('ACTIVE', 'Ativa'),
        ('REJECTED', 'Rejeitada'),
    ]

    class ChurchType(models.TextChoices):
        INDEPENDENT = 'INDEPENDENT', 'Igreja Independente / Sede'
        CONGREGATION = 'CONGREGATION', 'Congregação'

    church_type = models.CharField(
        "Tipo de Igreja",
        max_length=20,
        choices=ChurchType.choices,
        default=ChurchType.CONGREGATION,
    )
    parent_church = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='congregations',
        verbose_name='Igreja Sede (Independente)',
    )
    is_approved = models.BooleanField(
        "Aprovada pela Sede",
        default=False,
        help_text='Congregações só são aprovadas após o fluxo de aprovação da Sede.',
    )
    accounting_category = models.CharField(
        "Categoria Contábil do Repasse",
        max_length=100,
        null=True,
        blank=True,
        help_text='Categoria à qual os repasses da congregação são atribuídos na Sede.',
    )

    name = models.CharField("Nome da Congregação", max_length=200)
    pastor_name = models.CharField("Pastor Responsável", max_length=150, blank=True)
    treasurer_name = models.CharField("Tesoureiro Responsável", max_length=150, blank=True)
    phone = models.CharField("Telefone / WhatsApp", max_length=20, blank=True)

    # Regras contábeis da congregação.
    pastoral_prebenda_percent = models.DecimalField(
        "Percentual da Prebenda Pastoral (%)",
        max_digits=5, decimal_places=2,
        default=Decimal('10.00'),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text='Percentual da prebenda sobre o total arrecadado no mês.',
    )

    # Endereço & Localização
    cep = models.CharField("CEP", max_length=9, blank=True)
    street = models.CharField("Logradouro", max_length=200, blank=True)
    number = models.CharField("Número", max_length=20, blank=True)
    neighborhood = models.CharField("Bairro", max_length=100, blank=True)
    city = models.CharField("Cidade", max_length=100)
    state = models.CharField("UF", max_length=2)
    latitude = models.FloatField("Latitude", null=True, blank=True)
    longitude = models.FloatField("Longitude", null=True, blank=True)

    # Configuração da Carteirinha de Membro.
    card_primary_color = models.CharField(
        "Cor Primária da Carteirinha", max_length=7, blank=True, default='#0f766e',
    )
    card_secondary_color = models.CharField(
        "Cor Secundária da Carteirinha", max_length=7, blank=True, default='#0ea5e9',
    )
    card_valid_until = models.DateField(
        "Carteirinha Válida até", null=True, blank=True,
    )
    card_front_phrase = models.CharField(
        "Frase da Frente da Carteirinha", max_length=200, blank=True,
    )
    card_back_phrase = models.TextField(
        "Frase do Verso da Carteirinha", blank=True,
    )

    calendar_public_hash = models.CharField(
        "Hash público do Calendário",
        max_length=32,
        unique=True,
        null=True,
        blank=True,
        help_text='Hash único usado na URL pública do calendário da igreja.',
    )
    member_form_hash = models.CharField(
        "Hash público do Formulário de Membros",
        max_length=44,
        unique=True,
        null=True,
        blank=True,
        help_text='Hash usado no link genérico de cadastro de candidatos.',
    )

    status = models.CharField("Status", max_length=20, choices=STATUS_CHOICES, default='PENDING')
    created_at = models.DateTimeField("Cadastrado em", auto_now_add=True)
    updated_at = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Igreja"
        verbose_name_plural = "Igrejas"

    def ensure_public_hash(self) -> str:
        """Retorna (gerando se preciso) o hash público exclusivo do calendário."""
        if not self.calendar_public_hash:
            self.calendar_public_hash = uuid.uuid4().hex
        return self.calendar_public_hash

    def ensure_member_form_hash(self) -> str:
        """Retorna (gerando se preciso) o hash do formulário público de membros."""
        if not self.member_form_hash:
            self.member_form_hash = secrets.token_urlsafe(32)
        return self.member_form_hash

    def save(self, *args, **kwargs):
        if not self.calendar_public_hash:
            self.calendar_public_hash = uuid.uuid4().hex
        if not self.member_form_hash:
            self.member_form_hash = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} - {self.city}/{self.state} ({self.get_status_display()})"

    def is_sede(self) -> bool:
        return self.church_type == self.ChurchType.INDEPENDENT

    def sede(self):
        """Sede que governa esta igreja (ela própria quando Independente)."""
        return self if self.is_sede() else self.parent_church

    @property
    def responsible_user(self):
        """Usuário responsável pela igreja (prioridade: Pastor(a) vinculado)."""
        pastor = (
            self.members
            .select_related('user')
            .filter(role=ChurchMembership.Role.PASTOR)
            .order_by('id')
            .first()
        )
        if pastor:
            return pastor.user
        return self.users.order_by('id').first()


class ChurchMembership(models.Model):
    """Vínculo de um usuário a uma igreja com um papel operacional.

    O ADMIN (superadmin nacional) é representado por is_staff/is_superuser e
    não possui linha aqui. Este vínculo autoriza PASTOR/SECRETARIA/TESOUREIRO
    a operar determinada igreja. `User.church` continua sendo a igreja ativa.
    """

    class Role(models.TextChoices):
        PASTOR = 'PASTOR', 'Pastor(a)'
        SECRETARIA = 'SECRETARIA', 'Secretário(a)'
        TESOUREIRO = 'TESOUREIRO', 'Tesoureiro(a)'

    user = models.ForeignKey(
        'User',
        on_delete=models.CASCADE,
        related_name='church_memberships',
        verbose_name='Usuário',
    )
    church = models.ForeignKey(
        Church,
        on_delete=models.CASCADE,
        related_name='members',
        verbose_name='Igreja',
    )
    role = models.CharField(
        'Papel', max_length=20, choices=Role.choices,
    )
    created_at = models.DateTimeField('Criado em', auto_now_add=True)

    class Meta:
        verbose_name = 'Vínculo de Usuário'
        verbose_name_plural = 'Vínculos de Usuários'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'church'], name='unique_user_church_membership',
            )
        ]

    def __str__(self):
        return f'{self.user.email} => {self.church.name} ({self.get_role_display()})'


class AccountingCategory(models.Model):
    """Catálogo de categorias contábeis para o repasse de congregações.

    Reúne as categorias personalizadas criadas pela Sede no fluxo de aprovação
    ou na criação/edição de congregações. As categorias padrão do módulo
    financeiro (DepartmentCategory) permanecem em código e são mescladas a este
    catálogo na resposta da API — sem duplicação.
    """

    key = models.CharField(
        'Chave', max_length=60, unique=True,
        help_text='Chave normalizada (ex.: CONGREGACAO_ABC).',
    )
    label = models.CharField('Rótulo', max_length=100)
    created_at = models.DateTimeField('Criado em', auto_now_add=True)

    class Meta:
        verbose_name = 'Categoria Contábil'
        verbose_name_plural = 'Categorias Contábeis'
        ordering = ['label']

    def __str__(self):
        return f'{self.key} ({self.label})'


class MinistryArea(models.Model):
    """Área de atuação/ministerial de um membro (por igreja).

    Ex.: Coral, Diaconia, Ensino, Liderança, Banda. Gerenciável pela
    Secretaria/Pastor (CRUD completo).
    """

    church = models.ForeignKey(
        Church,
        on_delete=models.CASCADE,
        related_name='ministry_areas',
        verbose_name='Igreja',
    )
    name = models.CharField('Nome', max_length=100)
    created_at = models.DateTimeField('Criado em', auto_now_add=True)

    class Meta:
        verbose_name = 'Área de Atuação'
        verbose_name_plural = 'Áreas de Atuação'
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['church', 'name'], name='unique_church_ministry_area',
            )
        ]

    def __str__(self):
        return f'{self.name} - {self.church.name}'


class Member(models.Model):
    """Membro da igreja (diretório). Sem qualquer dado financeiro/dízimo.

    Utilizado pela Secretaria/Pastor para cadastro, edição e inativação.
    """

    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Ativo'
        INACTIVE = 'INACTIVE', 'Inativo'

    class ChurchEntry(models.TextChoices):
        ACLAMACAO = 'ACLAMACAO', 'Aclamação'
        BATISMO = 'BATISMO', 'Batismo'
        RECONCILIACAO = 'RECONCILIACAO', 'Reconciliação'
        TRANSFERENCIA = 'TRANSFERENCIA', 'Carta de Transferência'
        OUTRO = 'OUTRO', 'Outro'

    class MaritalStatus(models.TextChoices):
        SOLTEIRO = 'SOLTEIRO', 'Solteiro(a)'
        CASADO = 'CASADO', 'Casado(a)'
        UNIAO_ESTAVEL = 'UNIAO_ESTAVEL', 'União Estável'
        SEPARADO = 'SEPARADO', 'Separado(a)'
        DIVORCIADO = 'DIVORCIADO', 'Divorciado(a)'
        VIUVO = 'VIUVO', 'Viúvo(a)'

    class EducationLevel(models.TextChoices):
        SEM_ESCOLARIDADE = 'SEM_ESCOLARIDADE', 'Sem Escolaridade'
        FUNDAMENTAL = 'FUNDAMENTAL', 'Ensino Fundamental'
        MEDIO_INCOMPLETO = 'MEDIO_INCOMPLETO', 'Ensino Médio Incompleto'
        MEDIO = 'MEDIO', 'Ensino Médio'
        SUPERIOR_INCOMPLETO = 'SUPERIOR_INCOMPLETO', 'Ensino Superior Incompleto'
        SUPERIOR = 'SUPERIOR', 'Ensino Superior'
        POS_GRADUACAO = 'POS_GRADUACAO', 'Pós-graduação'

    church = models.ForeignKey(
        Church,
        on_delete=models.CASCADE,
        related_name='members_directory',
        verbose_name='Igreja',
    )
    name = models.CharField('Nome', max_length=150)
    phone = models.CharField('Telefone / WhatsApp', max_length=20, blank=True)
    email = models.EmailField('E-mail', blank=True)
    birth_date = models.DateField('Data de Nascimento', null=True, blank=True)
    baptism_date = models.DateField('Data de Batismo', null=True, blank=True)
    cpf = models.CharField('CPF', max_length=14, blank=True)
    rg = models.CharField('RG', max_length=20, blank=True)
    born_in_city = models.CharField(
        'Cidade de Nascimento (Naturalidade)', max_length=100, blank=True,
    )
    born_in_state = models.CharField(
        'UF de Nascimento', max_length=2, blank=True,
    )
    profession = models.CharField(
        'Profissão / Ocupação', max_length=100, blank=True,
    )
    education_level = models.CharField(
        'Escolaridade',
        max_length=20,
        choices=EducationLevel.choices,
        blank=True,
    )
    marital_status = models.CharField(
        'Estado Civil',
        max_length=20,
        choices=MaritalStatus.choices,
        blank=True,
    )
    marriage_date = models.DateField('Data do Casamento', null=True, blank=True)
    father_name = models.CharField('Nome do Pai', max_length=150, blank=True)
    mother_name = models.CharField('Nome da Mãe', max_length=150, blank=True)
    card_number = models.CharField(
        'Matrícula / Número da Carteirinha',
        max_length=20,
        null=True,
        blank=True,
    )
    church_entry = models.CharField(
        'Forma de Entrada na Igreja',
        max_length=20,
        choices=ChurchEntry.choices,
        blank=True,
    )
    church_entry_other = models.CharField(
        'Forma de Entrada (outra)', max_length=120, blank=True,
    )
    ministry_areas = models.ManyToManyField(
        MinistryArea,
        related_name='members',
        blank=True,
        verbose_name='Áreas de Atuação',
    )
    photo = CloudinaryField(
        'Foto',
        null=True,
        blank=True,
        folder='members',
    )
    status = models.CharField(
        'Status', max_length=20, choices=Status.choices, default=Status.ACTIVE,
    )
    notes = models.TextField('Observações', blank=True)
    public_hash = models.CharField(
        'Hash público do cartão', max_length=44, unique=True,
        editable=False, default=None, null=True,
        help_text='Usado na URL pública do cartão de membro.',
    )

    # Endereço residencial do membro
    street = models.CharField('Rua / Avenida', max_length=150, blank=True)
    number = models.CharField('Número', max_length=20, blank=True)
    complement = models.CharField('Complemento', max_length=100, blank=True)
    neighborhood = models.CharField('Bairro', max_length=100, blank=True)
    city = models.CharField('Cidade', max_length=100, blank=True)
    state = models.CharField('UF', max_length=2, blank=True)
    cep = models.CharField('CEP', max_length=9, blank=True)

    created_at = models.DateTimeField('Criado em', auto_now_add=True)
    updated_at = models.DateTimeField('Atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'Membro'
        verbose_name_plural = 'Membros'
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['church', 'card_number'],
                name='unique_church_member_card_number',
                condition=models.Q(card_number__isnull=False),
            )
        ]

    def __str__(self):
        return f'{self.name} - {self.church.name}'

    def save(self, *args, **kwargs):
        if not self.public_hash:
            self.public_hash = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def ensure_public_hash(self) -> str:
        """Retorna (gerando se preciso) o hash público do cartão."""
        if not self.public_hash:
            self.public_hash = secrets.token_urlsafe(32)
        return self.public_hash

    def regenerate_public_hash(self):
        self.public_hash = secrets.token_urlsafe(32)
        self.save(update_fields=['public_hash', 'updated_at'])


class MemberSubmission(models.Model):
    """Submissão de dados de membro via link público (cartão ou formulário).

    Uma submissão entra como **pendência de revisão**: a Secretaria/Pastor
    aprova (aplica os dados ao member existente ou cria candidato novo) ou
    rejeita. `data` guarda o JSON enviado e `source_hash` identifica o link
    usado (hash do cartão do membro ou o formulário genérico da igreja).
    """

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pendente de revisão'
        APPROVED = 'APPROVED', 'Aprovado'
        REJECTED = 'REJECTED', 'Rejeitado'

    church = models.ForeignKey(
        Church,
        on_delete=models.CASCADE,
        related_name='member_submissions',
        verbose_name='Igreja',
    )
    member = models.ForeignKey(
        Member,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='submissions',
        verbose_name='Membro (se submissão vem do cartão)',
    )
    source_hash = models.CharField(
        'Hash do link de origem', max_length=44,
        help_text='Hash do cartão do membro ou do formulário genérico da igreja.',
    )
    data = models.JSONField('Dados enviados')
    status = models.CharField(
        'Status', max_length=20, choices=Status.choices, default=Status.PENDING,
    )
    reviewed_at = models.DateTimeField('Revisado em', null=True, blank=True)
    reviewed_by = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        verbose_name='Revisado por',
    )
    notes = models.TextField('Observações', blank=True)
    created_at = models.DateTimeField('Enviado em', auto_now_add=True)
    updated_at = models.DateTimeField('Atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'Submissão de membro'
        verbose_name_plural = 'Submissões de membros'
        ordering = ['-created_at']

    def __str__(self):
        target = self.member.name if self.member else 'candidato(a)'
        return f'{target} → {self.church.name} ({self.get_status_display()})'


class MemberRelative(models.Model):
    """Parente de um membro (relações de parentesco do diretório).

    Cobre cônjuge, filhos, pais, irmãos e demais vínculos familiares,
    exibidos na carteirinha e no cadastro do membro.
    """

    class Kinship(models.TextChoices):
        CONJUGE = 'CONJUGE', 'Cônjuge'
        PAI = 'PAI', 'Pai'
        MAE = 'MAE', 'Mãe'
        FILHO = 'FILHO', 'Filho(a)'
        IRMAO = 'IRMAO', 'Irmão(ã)'
        AVO = 'AVO', 'Avô(ó)'
        NETO = 'NETO', 'Neto(a)'
        OUTRO = 'OUTRO', 'Outro'

    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name='relatives',
        verbose_name='Membro',
    )
    name = models.CharField('Nome', max_length=150)
    kinship = models.CharField(
        'Grau de Parentesco',
        max_length=20,
        choices=Kinship.choices,
    )
    birth_date = models.DateField('Data de Nascimento', null=True, blank=True)
    phone = models.CharField('Telefone', max_length=20, blank=True)
    created_at = models.DateTimeField('Criado em', auto_now_add=True)

    class Meta:
        verbose_name = 'Parente do Membro'
        verbose_name_plural = 'Parentes dos Membros'
        ordering = ['name']
def __str__(self):
        return f'{self.name} ({self.get_kinship_display()}) - {self.member.name}'


class MemberTransfer(models.Model):
    """Transferência de membresia entre igrejas.

    A igreja de origem emite um "cartão de transferência" (snapshot dos dados
    do membro) para uma igreja de destino; esta, ao receber, cria o membro no
    próprio rol (church_entry = TRANSFERENCIA) e inativa o membro de origem.
    """

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pendente de Recebimento'
        RECEIVED = 'RECEIVED', 'Recebida'
        CANCELED = 'CANCELED', 'Cancelada'

    source_church = models.ForeignKey(
        Church,
        on_delete=models.CASCADE,
        related_name='outgoing_transfers',
        verbose_name='Igreja de Origem',
    )
    source_member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name='transfers_out',
        verbose_name='Membro Transferido',
    )
    target_church = models.ForeignKey(
        Church,
        on_delete=models.CASCADE,
        related_name='incoming_transfers',
        verbose_name='Igreja de Destino',
    )
    status = models.CharField(
        'Status', max_length=20, choices=Status.choices, default=Status.PENDING,
    )
    issued_at = models.DateTimeField('Emitida em', auto_now_add=True)
    received_at = models.DateTimeField('Recebida em', null=True, blank=True)
    received_by = models.ForeignKey(
        'User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transfers_received',
        verbose_name='Recebida por',
    )
    canceled_at = models.DateTimeField('Cancelada em', null=True, blank=True)
    canceled_by = models.ForeignKey(
        'User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transfers_canceled',
        verbose_name='Cancelada por',
    )

    # Snapshot do membro no momento da emissão (estável mesmo se o rol de
    # origem for editado ou excluído).
    member_name = models.CharField('Nome', max_length=150)
    member_cpf = models.CharField('CPF', max_length=14, blank=True)
    member_rg = models.CharField('RG', max_length=20, blank=True)
    member_birth_date = models.DateField('Data de Nascimento', null=True, blank=True)
    member_baptism_date = models.DateField('Data de Batismo', null=True, blank=True)
    member_phone = models.CharField('Telefone / WhatsApp', max_length=20, blank=True)
    member_email = models.EmailField('E-mail', blank=True)
    member_profession = models.CharField(
        'Profissão / Ocupação', max_length=100, blank=True,
    )
    member_father_name = models.CharField('Nome do Pai', max_length=150, blank=True)
    member_mother_name = models.CharField('Nome da Mãe', max_length=150, blank=True)
    member_street = models.CharField('Rua / Avenida', max_length=150, blank=True)
    member_number = models.CharField('Número', max_length=20, blank=True)
    member_complement = models.CharField('Complemento', max_length=100, blank=True)
    member_neighborhood = models.CharField('Bairro', max_length=100, blank=True)
    member_city = models.CharField('Cidade', max_length=100, blank=True)
    member_state = models.CharField('UF', max_length=2, blank=True)
    member_cep = models.CharField('CEP', max_length=9, blank=True)
    member_notes = models.TextField('Observações', blank=True)

    class Meta:
        verbose_name = 'Transferência de Membro'
        verbose_name_plural = 'Transferências de Membros'
        ordering = ['-issued_at']

    def __str__(self):
        return (
            f'{self.member_name} - {self.source_church.name} → '
            f'{self.target_church.name} ({self.get_status_display()})'
        )


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField("E-mail", unique=True)
    name = models.CharField("Nome Completo", max_length=150, blank=True)
    church = models.ForeignKey(
        Church,
        on_delete=models.SET_NULL,
        related_name="users",
        null=True,
        blank=True,
        verbose_name="Igreja Ativa (contexto)",
        help_text=(
            'Igreja em operação no momento (contexto ativo). A autorização é '
            'definida pelo ChurchMembership; User.church é apenas um ponteiro.'
        ),
    )
    is_active = models.BooleanField("Ativo", default=False)
    is_staff = models.BooleanField("Acesso Admin", default=False)
    created_at = models.DateTimeField("Criado em", auto_now_add=True)
    updated_at = models.DateTimeField("Atualizado em", auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = "Usuário"
        verbose_name_plural = "Usuários"

    def __str__(self):
        return self.email

    def get_role_for(self, church) -> str | None:
        """Papel operacional do usuário em uma igreja (ou None se não vinculado).

        Superadmins (is_staff/is_superuser) não possuem papel: as permissões de
        admin são avaliadas à parte e permitem acesso a tudo.
        """
        if self.is_staff or self.is_superuser:
            return None
        membership = self.church_memberships.filter(church=church).first()
        return membership.role if membership else None

    @property
    def active_role(self) -> str | None:
        """Papel do usuário na igreja ativa (user.church)."""
        if self.church is None:
            return None
        return self.get_role_for(self.church)


class MemberDocument(models.Model):
    """Documento anexado ao cadastro do membro (RG, CPF, comprovantes).

    Arquivos confidenciais ficam no armazenamento local do servidor
    (FileSystemStorage), fora do Cloudinary, e são baixados exclusivamente
    pelo endpoint autenticado e restrito à igreja do membro.
    """

    class DocType(models.TextChoices):
        RG = 'RG', 'RG'
        CPF = 'CPF', 'CPF'
        RESIDENCE_PROOF = 'RESIDENCE_PROOF', 'Comprovante de Residência'
        OTHER = 'OTHER', 'Outro'

    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name='documents',
        verbose_name='Membro',
    )
    doc_type = models.CharField(
        'Tipo de Documento',
        max_length=20,
        choices=DocType.choices,
    )
    file = models.FileField(
        'Arquivo',
        storage=member_doc_storage,
        upload_to='member_documents',
        max_length=255,
    )
    notes = models.CharField('Observações', max_length=200, blank=True)
    uploaded_by = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='Enviado por',
    )
    uploaded_at = models.DateTimeField('Enviado em', auto_now_add=True)

    class Meta:
        verbose_name = 'Documento do Membro'
        verbose_name_plural = 'Documentos dos Membros'
        ordering = ['-uploaded_at', 'id']


class StorageLocation(models.Model):
    """Local de armazenamento dos materiais/equipamentos da igreja."""

    church = models.ForeignKey(
        Church,
        on_delete=models.CASCADE,
        related_name='storage_locations',
        verbose_name='Igreja',
    )
    name = models.CharField('Nome do local', max_length=200)
    created_at = models.DateTimeField('Criado em', auto_now_add=True)

    class Meta:
        verbose_name = 'Local de Armazenamento'
        verbose_name_plural = 'Locais de Armazenamento'
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['church', 'name'],
                name='unique_church_storage_location',
            )
        ]

    def __str__(self):
        return self.name


class MaterialItem(models.Model):
    """Material/instrumento do inventário da igreja.

    Sem numeração: cada unidade física é um registro próprio, identificado pelo
    nome e pelas características (ex.: duas guitarras iguais = dois registros
    com descrições que as distinguem).
    """

    church = models.ForeignKey(
        Church,
        on_delete=models.CASCADE,
        related_name='material_items',
        verbose_name='Igreja',
    )
    name = models.CharField('Nome', max_length=200)
    description = models.TextField('Características', blank=True)
    location = models.ForeignKey(
        StorageLocation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='items',
        verbose_name='Local de armazenamento',
    )

    created_at = models.DateTimeField('Criado em', auto_now_add=True)
    updated_at = models.DateTimeField('Atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'Material/Equipamento'
        verbose_name_plural = 'Materiais/Equipamentos'
        ordering = ['name']

    def __str__(self):
        return self.name

    def current_open_loan(self):
        """Empréstimo aberto mais recente do item (ou None)."""
        return (
            self.loans.filter(returned_at__isnull=True)
            .order_by('-borrowed_at', '-id')
            .first()
        )


class Loan(models.Model):
    """Empréstimo de um material/equipamento com prazo de devolução esperada."""

    church = models.ForeignKey(
        Church,
        on_delete=models.CASCADE,
        related_name='loans',
        verbose_name='Igreja',
    )
    item = models.ForeignKey(
        MaterialItem,
        on_delete=models.CASCADE,
        related_name='loans',
        verbose_name='Material/Equipamento',
    )
    member = models.ForeignKey(
        Member,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='loans',
        verbose_name='Membro',
    )
    borrower_name = models.CharField(
        'Nome do tomador (quando não vinculado a um membro)',
        max_length=200,
        blank=True,
    )
    borrowed_at = models.DateField('Data do empréstimo')
    expected_return = models.DateField('Devolução prevista')
    returned_at = models.DateTimeField(
        'Devolvido em', null=True, blank=True,
    )
    notes = models.TextField('Observações', blank=True)
    created_by = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        verbose_name='Registrado por',
    )
    returned_by = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        verbose_name='Baixa dada por',
    )
    created_at = models.DateTimeField('Criado em', auto_now_add=True)
    updated_at = models.DateTimeField('Atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'Empréstimo de Material'
        verbose_name_plural = 'Empréstimos de Materiais'
        ordering = ['-borrowed_at', '-id']

    def __str__(self):
        return f'{self.item} → {self.borrower_display}'

    @property
    def borrower_display(self):
        if self.member_id:
            return self.member.name
        return self.borrower_name or '—'

    def status_on(self, on=None):
        """Situação do empréstimo na data `on` (injetável para testes).

        - 'returned': baixa da devolução registrada;
        - 'overdue':  prazo passou sem baixa;
        - 'active':   aberto dentro do prazo.
        """
        from django.utils import timezone  # noqa: PLC0415

        if self.returned_at is not None:
            return 'returned'
        today = on or timezone.localdate()
        if self.expected_return < today:
            return 'overdue'
        return 'active'


class WorshipService(models.Model):
    """Registro de um culto/serviço já realizado pela igreja (livro de cultos)."""

    class ServiceType(models.TextChoices):
        CELEBRACAO = 'CELEBRACAO', 'Culto de Celebração'
        DOUTRINA = 'DOUTRINA', 'Culto de Doutrina'
        ORACAO = 'ORACAO', 'Culto de Oração'
        VIGILIA = 'VIGILIA', 'Vigília'
        CEIA = 'CEIA', 'Ceia do Senhor'
        ESCOLA_BIBLICA = 'ESCOLA_BIBLICA', 'Escola Bíblica'
        JOVENS = 'JOVENS', 'Culto de Jovens'
        OUTRO = 'OUTRO', 'Outro'

    church = models.ForeignKey(
        Church,
        on_delete=models.CASCADE,
        related_name='worship_services',
        verbose_name='Igreja',
    )
    date = models.DateField('Data do culto')
    time = models.TimeField('Horário', null=True, blank=True)
    service_type = models.CharField(
        'Tipo de culto',
        max_length=20,
        choices=ServiceType.choices,
        default=ServiceType.CELEBRACAO,
    )
    presider = models.CharField('Dirigente', max_length=200, blank=True)
    preacher = models.CharField(
        'Pregador/ministrante', max_length=200, blank=True,
    )
    theme = models.CharField('Tema', max_length=200, blank=True)
    scripture = models.CharField('Texto bíblico', max_length=200, blank=True)
    attendees = models.PositiveIntegerField('Presentes', default=0)
    visitors = models.PositiveIntegerField('Visitantes', default=0)
    conversions = models.PositiveIntegerField('Conversões', default=0)
    offering = models.DecimalField(
        'Ofertas', max_digits=12, decimal_places=2, default=Decimal('0.00'),
    )
    notes = models.TextField('Observações', blank=True)
    created_by = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        verbose_name='Registrado por',
    )
    created_at = models.DateTimeField('Criado em', auto_now_add=True)
    updated_at = models.DateTimeField('Atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'Registro de Culto'
        verbose_name_plural = 'Registros de Cultos'
        ordering = ['-date', '-id']

    def __str__(self):
        return f'{self.get_service_type_display()} — {self.date.isoformat()}'


class MinutesStorage(FileSystemStorage):
    """Armazenamento local dos PDFs de atas.

    Deriva `location`/`base_url` das settings em tempo de instanciação para que
    a deconstruct cleanup fique portátil (sem caminho absoluto nas migrações).
    """

    def __init__(self, **kwargs):
        kwargs.setdefault('location', settings.MEDIA_ROOT)
        kwargs.setdefault('base_url', settings.MEDIA_URL)
        super().__init__(**kwargs)

    def deconstruct(self):
        return (
            'accounts.models.MinutesStorage',
            [],
            {},
        )


minutes_storage = MinutesStorage()


class ChurchMinutes(models.Model):
    """Ata de reunião/assembleia da igreja, com link público por hash."""

    class MeetingType(models.TextChoices):
        ASSEMBLEIA_GERAL = 'ASSEMBLEIA_GERAL', 'Assembleia Geral'
        ASSEMBLEIA_EXTRAORDINARIA = (
            'ASSEMBLEIA_EXTRAORDINARIA',
            'Assembleia Extraordinária',
        )
        DIRETORIA = 'DIRETORIA', 'Reunião da Diretoria'
        CONSELHO = 'CONSELHO', 'Reunião do Conselho'
        OUTRO = 'OUTRO', 'Outro'

    church = models.ForeignKey(
        Church,
        on_delete=models.CASCADE,
        related_name='minutes',
        verbose_name='Igreja',
    )
    title = models.CharField('Título da ata', max_length=200)
    meeting_type = models.CharField(
        'Tipo de reunião',
        max_length=30,
        choices=MeetingType.choices,
        default=MeetingType.ASSEMBLEIA_GERAL,
    )
    meeting_date = models.DateField('Data da reunião')
    location = models.CharField('Local', max_length=200, blank=True)
    recorder = models.CharField('Redator (quem lavrou)', max_length=200, blank=True)
    participants = models.TextField('Participantes / presentes', blank=True)
    content = models.TextField('Texto da ata')
    pdf = models.FileField(
        'PDF da ata',
        storage=minutes_storage,
        upload_to='minutes/',
        blank=True,
        null=True,
    )
    public_hash = models.CharField(
        'Hash público', max_length=44, unique=True, editable=False,
        default=None, null=True,
    )
    created_by = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        verbose_name='Redigida por',
    )
    created_at = models.DateTimeField('Criado em', auto_now_add=True)
    updated_at = models.DateTimeField('Atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'Ata'
        verbose_name_plural = 'Atas'
        ordering = ['-meeting_date', '-id']

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.public_hash:
            self.public_hash = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def regenerate_public_hash(self):
        self.public_hash = secrets.token_urlsafe(32)
        self.save(update_fields=['public_hash', 'updated_at'])