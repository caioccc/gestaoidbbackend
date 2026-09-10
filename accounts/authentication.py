"""Regras de autenticação customizadas para o SimpleJWT."""


def custom_user_authentication_rule(user):
    """Permite que o usuário alcance a validação final do token.

    O SimpleJWT por padrão rejeita usuários inativos antes de qualquer
    lógica de validação (com 401 genérico). Ao liberar aqui a autenticação,
    o serializer consegue diferenciar:
      - congregação ainda não aprovada pela Sede  -> 403 com mensagem clara
      - usuário inativo por outro motivo          -> 401 genérico

    Nenhum token é emitido nesses casos: o serializer valida e aborta.
    """
    return user is not None