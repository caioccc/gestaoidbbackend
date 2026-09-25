"""Permissões do módulo de música (Ownership + Governança).

Regras de músicas (Song):
- Leitura (SAFE methods): qualquer usuário autenticado com igreja ativa.
- Cadastro (POST): qualquer membro da igreja (o autor vira `created_by`).
- Mutação (PUT/PATCH/DELETE): apenas o criador, o ADMIN (staff/superuser)
  ou o Pastor na igreja ativa. Demais perfis recebem HTTP 403.
"""
from rest_framework import permissions

from accounts.permissions import is_admin
from accounts.models import ChurchMembership


# Governança do repertório: apenas PASTOR (ADMIN/superuser sempre passa).
SONG_GOVERNANCE_ROLES = ('PASTOR',)

# Editar/excluir setlists (de banda e a do culto): somente PASTOR/ADMIN.
SETLIST_GOVERNANCE_ROLES = ('PASTOR',)

_FORBIDDEN_MESSAGE = (
    'Apenas o criador desta música ou administradores '
    'da congregação podem alterá-la ou excluí-la.'
)


def can_govern_setlist(user) -> bool:
    """Governança global de setlists (todas, de qualquer criador):
    unicamente PASTOR/ADMIN (e pastor de sede herdado)."""
    if not user or not user.is_authenticated:
        return False
    if is_admin(user):
        return True
    return (
        user.church is not None
        and user.get_role_for(user.church) in SETLIST_GOVERNANCE_ROLES
    ) or _inherits_pastor(user)


def can_edit_song(user, song) -> bool:
    """Se o usuário pode editar/excluir a música (`song`)."""
    if not user or not user.is_authenticated:
        return False
    if is_admin(user):
        return True
    if song.created_by_id and song.created_by_id == user.id:
        return True
    if user.church is None:
        return False
    return (
        user.get_role_for(user.church) in SONG_GOVERNANCE_ROLES
        or _inherits_pastor(user)
    )


def _inherits_pastor(user) -> bool:
    """Pastor de Sede operando a congregação como igreja ativa herda o papel
    de gestão (espelha o comportamento de `IsChurchRole`)."""
    parent = user.church.parent_church if user.church else None
    if (
        parent is not None
        and parent.is_sede()
        and user.get_role_for(parent) == ChurchMembership.Role.PASTOR
    ):
        return True
    return False


class IsSongOwnerOrAdmin(permissions.BasePermission):
    """Ownership + Governança de músicas.

    - has_permission: libera para qualquer usuário autenticado com igreja
      ativa (cadastro e leitura); o controle de mutação é refinado em
      has_object_permission.
    - has_object_permission: SAFE liberado; PUT/PATCH/DELETE conforme
      `can_edit_song`.
    """

    message = _FORBIDDEN_MESSAGE

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return user.church is not None

    def has_object_permission(self, request, view, obj):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return can_edit_song(request.user, obj)


def can_edit_setlist(user, setlist) -> bool:
    """Ownership de setlists (de banda e a do culto).

    O criador da setlist pode editá-la/excluí-la; PASTOR/ADMIN governa
    todas. Demais perfis recebem HTTP 403 (espelha o padrão de músicas,
    porém sem o papel LOUVOR como governança global de setlists).
    """
    if not user or not user.is_authenticated:
        return False
    if is_admin(user):
        return True
    if setlist.created_by_id and setlist.created_by_id == user.id:
        return True
    return (
        user.church is not None
        and user.get_role_for(user.church) in SETLIST_GOVERNANCE_ROLES
    ) or _inherits_pastor(user)


class IsSetlistOwnerOrPastor(permissions.BasePermission):
    """Ownership + PASTOR/ADMIN para BandSetlist/WorshipSetlist/roster do culto.

    - has_permission: libera para qualquer usuário autenticado com igreja
      ativa (incluindo a criação e a leitura).
    - has_object_permission: SAFE liberado; update/destroy conforme
      `can_edit_setlist`.
    """

    message = _FORBIDDEN_MESSAGE

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return user.church is not None

    def has_object_permission(self, request, view, obj):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return can_edit_setlist(request.user, obj)