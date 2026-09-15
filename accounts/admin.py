from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import (
    AccountingCategory,
    CertificateTemplate,
    Church,
    EcclesiasticalCertificate,
    Member,
    MinistryArea,
    SundaySchoolAttendance,
    SundaySchoolClass,
    SundaySchoolEnrollment,
    SundaySchoolSession,
    User,
)


@admin.register(SundaySchoolClass)
class SundaySchoolClassAdmin(admin.ModelAdmin):
    list_display = ['name', 'church', 'category', 'teacher_name', 'is_active']
    list_filter = ['church', 'category', 'is_active']
    search_fields = ['name', 'teacher_name', 'church__name']


@admin.register(Church)
class ChurchAdmin(admin.ModelAdmin):
    """Administração das congregações com moderação de status."""

    list_display = [
        'name', 'city', 'state', 'status', 'pastor_name',
        'treasurer_name', 'phone', 'created_at',
    ]
    list_filter = ['status', 'state', 'created_at']
    search_fields = ['name', 'city', 'state', 'pastor_name', 'treasurer_name', 'phone']
    list_editable = ['status']
    ordering = ['-created_at']
    actions = ['approve_churches', 'reject_churches']

    @admin.action(description='Aprovar congregações selecionadas')
    def approve_churches(self, request, queryset):
        allowed = queryset.filter(status='PENDING')
        for church in allowed:
            church.status = 'ACTIVE'
            church.save(update_fields=['status'])
            user = church.responsible_user
            if user is not None and not user.is_active:
                user.is_active = True
                user.save(update_fields=['is_active'])
            try:
                from finance.services import seed_default_calendar_events
                seed_default_calendar_events(church)
            except Exception:
                pass
        self.message_user(
            request, f'{allowed.count()} Igreja(s) aprovada(s).'
        )

    @admin.action(description='Rejeitar congregações selecionadas')
    def reject_churches(self, request, queryset):
        updated = queryset.filter(status='PENDING').update(status='REJECTED')
        self.message_user(request, f'{updated} Igreja(s) rejeitada(s).')


@admin.register(AccountingCategory)
class AccountingCategoryAdmin(admin.ModelAdmin):
    list_display = ['key', 'label', 'created_at']
    search_fields = ['key', 'label']
    ordering = ['label']


@admin.register(MinistryArea)
class MinistryAreaAdmin(admin.ModelAdmin):
    list_display = ['name', 'church', 'created_at']
    list_filter = ['church']
    search_fields = ['name']


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ['name', 'church', 'phone', 'status', 'church_entry', 'cpf']
    list_filter = ['church', 'status', 'church_entry']
    search_fields = ['name', 'phone', 'email', 'cpf', 'rg']


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Administração do usuário customizado (login por e-mail)."""

    ordering = ['email']
    list_display = ['email', 'name', 'is_staff', 'is_active', 'church', 'created_at']
    list_filter = ['is_staff', 'is_active', 'created_at']
    search_fields = ['email', 'name']

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Informações Pessoais', {'fields': ('name', 'church')}),
        (
            'Permissões',
            {
                'fields': (
                    'is_active',
                    'is_staff',
                    'is_superuser',
                    'groups',
                    'user_permissions',
                ),
            },
        ),
        ('Datas', {'fields': ('last_login', 'created_at', 'updated_at')}),
    )
    add_fieldsets = (
        (
            None,
            {
                'classes': ('wide',),
                'fields': ('email', 'name', 'password1', 'password2'),
            },
        ),
    )
    readonly_fields = ['created_at', 'updated_at', 'last_login']
    filter_horizontal = ['groups', 'user_permissions']


@admin.register(CertificateTemplate)
class CertificateTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'church', 'certificate_type', 'layout_mode', 'is_active', 'created_at']
    list_filter = ['church', 'certificate_type', 'layout_mode', 'is_active']
    search_fields = ['name', 'church__name']


@admin.register(EcclesiasticalCertificate)
class EcclesiasticalCertificateAdmin(admin.ModelAdmin):
    list_display = ['recipient_name', 'church', 'certificate_type', 'event_date', 'created_at']
    list_filter = ['church', 'certificate_type', 'event_date']
    search_fields = ['recipient_name', 'member__name', 'church__name']
