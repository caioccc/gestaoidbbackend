from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models


class DepartmentCategory(models.TextChoices):
    """Categorias contábeis / departamentos das entradas e saídas."""

    DIZIMO = 'DIZIMO', 'Dízimo'
    OFERTA = 'OFERTA', 'Ofertas Gerais'
    CONSTRUCAO = 'CONSTRUCAO', 'Construção'
    ESPECIAL = 'ESPECIAL', 'Especiais'
    MISSOES = 'MISSOES', 'Missões'
    MULHERES = 'MULHERES', 'Mulheres'
    HOMENS = 'HOMENS', 'Homens'
    JOVENS = 'JOVENS', 'Jovens'
    ESC_BIBLICA = 'ESC_BIBLICA', 'Escola Bíblica'
    INFANTIL = 'INFANTIL', 'Infantil'
    ADOLESCENTES = 'ADOLESCENTES', 'Adolescentes'


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
        'Comprovante', upload_to='receipts/', null=True, blank=True,
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
