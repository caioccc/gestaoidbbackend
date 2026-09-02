from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from decimal import Decimal

# Create your models here.
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager

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

    status = models.CharField("Status", max_length=20, choices=STATUS_CHOICES, default='PENDING')
    created_at = models.DateTimeField("Cadastrado em", auto_now_add=True)
    updated_at = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Igreja"
        verbose_name_plural = "Igrejas"

    def __str__(self):
        return f"{self.name} - {self.city}/{self.state} ({self.get_status_display()})"

class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField("E-mail", unique=True)
    name = models.CharField("Nome Completo", max_length=150, blank=True)
    church = models.OneToOneField(
        Church,
        on_delete=models.CASCADE,
        related_name="user_account",
        null=True,
        blank=True,
        verbose_name="Igreja Vinculada"
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