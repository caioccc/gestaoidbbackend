"""Permissões baseadas em papéis e hierarquia eclesiástica."""
from rest_framework import permissions

from .models import Church, ChurchMembership


def is_admin(user) -> bool:
    """Superadmin nacional (staff/superuser)."""
    return bool(
        user and user.is_authenticated and (user.is_staff or user.is_superuser)
    )


def can_manage_church(user, church) -> bool:
    """Se o usuário pode operar a igreja informada.

    Regras:
    - ADMIN (staff/superuser) opera qualquer igreja.
    - A igreja ativa (user.church) é sempre operável.
    - Pastor de Sede opera suas congregações aprovadas.
    - Congregação ativa: opera a própria congregação e a sua Sede; sendo
      Pastor da Sede, também as demais congregações da mesma Sede, podendo
      trocar diretamente entre elas.
    """
    if is_admin(user):
        return True
    if user is None or user.church is None:
        return False
    active = user.church
    if church.id == active.id:
        return True
    if active.is_sede():
        return (
            church.church_type == Church.ChurchType.CONGREGATION
            and church.parent_church_id == active.id
            and church.is_approved
        )
    # Congregação ativa.
    parent = active.parent_church
    if parent is None or not parent.is_sede():
        return False
    if church.id == parent.id:
        return True
    return (
        user.get_role_for(parent) == ChurchMembership.Role.PASTOR
        and church.church_type == Church.ChurchType.CONGREGATION
        and church.parent_church_id == parent.id
        and church.is_approved
    )


def accessible_churches(user):
    """Queryset de igrejas acessíveis pelo usuário (ADMIN: todas).

    Espelha can_manage_church: além da igreja ativa, cobre a Sede e as
    congregações aprovadas no contexto em que o usuário opera.
    """
    qs = Church.objects.all()
    if is_admin(user):
        return qs
    if user.church is None:
        return qs.none()
    if user.church.is_sede():
        return Church.objects.filter(
            parent_church=user.church
        ) | Church.objects.filter(pk=user.church.pk)
    # Congregação ativa: a própria congregação e a Sede governante; o Pastor
    # da Sede também enxerga as demais congregações aprovadas dessa Sede.
    qs = Church.objects.filter(pk=user.church.pk)
    parent_id = user.church.parent_church_id
    if parent_id:
        qs = qs | Church.objects.filter(pk=parent_id)
        if user.get_role_for(user.church.parent_church) == ChurchMembership.Role.PASTOR:
            qs = qs | Church.objects.filter(
                parent_church_id=parent_id,
                church_type=Church.ChurchType.CONGREGATION,
                is_approved=True,
            )
    return qs


def IsChurchRole(*roles) -> permissions.BasePermission:
    """Fábrica: ADMIN sempre passa; demais usuários precisam do papel na igreja ativa."""

    class _ChurchRolePermission(permissions.BasePermission):
        def has_permission(self, request, view):
            user = request.user
            if not (user and user.is_authenticated):
                return False
            if is_admin(user):
                return True
            if user.church is None:
                return False
            role = user.get_role_for(user.church)
            if role in roles:
                return True
            # Pastor de Sede operando a congregação como igreja ativa herda o
            # papel requerido (equivale a gerenciar a própria congregação).
            parent = user.church.parent_church
            if (
                parent is not None
                and parent.is_sede()
                and user.get_role_for(parent) == ChurchMembership.Role.PASTOR
                and 'PASTOR' in roles
            ):
                return True
            return False

    return _ChurchRolePermission


IsStaffPermission = type(
    'IsStaffPermission',
    (permissions.BasePermission,),
    {
        'message': 'Acesso restrito a administradores.',
        'has_permission': lambda self, request, view: is_admin(request.user),
        '__doc__': 'Permite acesso apenas a usuários staff/superuser.',
    },
)


class CanAccessTargetChurch(permissions.BasePermission):
    """Acessa a igreja da rota (church_pk ou pk) se ADMIN/manager da sede ou ativa."""

    message = 'Acesso restrito à sua igreja ou às suas congregações.'

    def has_permission(self, request, view):
        pk = view.kwargs.get('church_pk') or view.kwargs.get('pk')
        if not pk:
            return False
        church = Church.objects.filter(pk=pk).first()
        if church is None:
            return False
        return can_manage_church(request.user, church)


class IsSedeManager(permissions.BasePermission):
    """ADMIN ou Pastor da sua igreja Independente (Sede)."""

    message = 'Acesso restrito a Pastores de Igreja Independente.'

    def has_permission(self, request, view):
        user = request.user
        if is_admin(user):
            return True
        if not (user and user.is_authenticated and user.church):
            return False
        return (
            user.church.is_sede()
            and user.get_role_for(user.church) == ChurchMembership.Role.PASTOR
        )


class GrowthGroupPermission(permissions.BasePermission):
    """Permissões do módulo de Grupos de Crescimento (GCs).

    Manual de regras (RBAC):
    - Leitura (list/retrieve): qualquer usuário autenticado com igreja ativa.
    - Criar/Editar (create/update/partial_update): secretaria, tesoureiro,
      pastor e admin.
    - Excluir (destroy): estritamente secretaria, pastor e admin — o perfil
      tesoureiro NÃO pode excluir.

    A checagem de papel usa a igreja ativa (user.church), espelhando
    `IsChurchRole`. O ADMIN (staff/superuser) sempre passa.
    """

    MANAGE_ROLES = ('PASTOR', 'SECRETARIA', 'TESOUREIRO')
    # O tesoureiro pode criar/editar, mas não excluir.
    DELETE_ROLES = ('PASTOR', 'SECRETARIA')

    message = 'Ação não permitida para o seu perfil.'

    def _role(self, request) -> str | None:
        user = request.user
        if not (user and user.is_authenticated):
            return None
        if user.church is None:
            return None
        role = user.get_role_for(user.church)
        # Pastor de Sede operando a congregação como igreja ativa herda o
        # papel requerido (espelha IsChurchRole).
        parent = user.church.parent_church
        if (
            role in (None, 'TESOUREIRO', 'SECRETARIA')
            and parent is not None
            and parent.is_sede()
            and user.get_role_for(parent) == ChurchMembership.Role.PASTOR
        ):
            return 'PASTOR'
        return role

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if is_admin(user):
            return True
        if request.method in permissions.SAFE_METHODS:
            # Leitura para qualquer autenticado com igreja ativa.
            return user.church is not None
        role = self._role(request)
        if request.method == 'DELETE':
            return role in self.DELETE_ROLES
        return role in self.MANAGE_ROLES

    def has_object_permission(self, request, view, obj):
        user = request.user
        if is_admin(user):
            return True
        if request.method in permissions.SAFE_METHODS:
            if user.church is None:
                return False
            return obj.church_id == user.church.id
        role = self._role(request)
        if request.method == 'DELETE':
            return role in self.DELETE_ROLES
        return role in self.MANAGE_ROLES and obj.church_id == user.church.id