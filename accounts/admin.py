from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Church, User


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
            user = getattr(church, 'user_account', None)
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
