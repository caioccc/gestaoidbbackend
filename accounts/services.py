"""Serviços de notificação por e-mail do app accounts.

Centraliza o envio de e-mails transacionais: credenciais no cadastro,
e notificações de aprovação/rejeição de congregações.
"""
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

APP_NAME = 'Financeiro IDB'
APP_TAGLINE = 'Gestão Financeira das Igrejas de Deus no Brasil'


def _send_template_email(to_email, subject, html_template, context):
    """Envia e-mail HTML + texto com template, sem quebrar o fluxo em erro."""
    base_ctx = {
        'app_name': APP_NAME,
        'app_tagline': APP_TAGLINE,
        'frontend_url': settings.FRONTEND_URL,
    }
    base_ctx.update(context)

    try:
        html = render_to_string(html_template, base_ctx)
        msg = EmailMultiAlternatives(
            subject=subject,
            body=html,
            from_email=settings.DEFAULT_FROM_EMAIL or settings.EMAIL_HOST_USER,
            to=[to_email],
        )
        msg.attach_alternative(html, 'text/html')
        msg.send(fail_silently=False)
        logger.info('E-mail enviado para %s (assunto: %s)', to_email, subject)
    except Exception:  # noqa: BLE001 — nunca derruba o fluxo principal
        logger.exception('Falha ao enviar e-mail para %s (%s)', to_email, subject)


def send_registration_credentials(email, password, church_name, account_name):
    """Envia email + senha no momento do cadastro da congregação.

    A senha existe em texto puro apenas durante a request de cadastro
    (o banco guarda somente o hash). Por isso ela é enviada aqui.
    """
    _send_template_email(
        email,
        f'Credenciais de acesso — {APP_NAME}',
        'accounts/emails/registration_credentials.html',
        {
            'email': email,
            'password': password,
            'church_name': church_name,
            'account_name': account_name,
        },
    )


def send_approval_notification(email, church_name, account_name):
    """Notifica que a congregação foi aprovada e o acesso está ativo."""
    _send_template_email(
        email,
        f'Igreja aprovada — {APP_NAME}',
        'accounts/emails/approval.html',
        {
            'email': email,
            'church_name': church_name,
            'account_name': account_name,
            'status': 'approved',
        },
    )


def send_rejection_notification(email, church_name, account_name):
    """Notifica que a congregação foi rejeitada."""
    _send_template_email(
        email,
        f'Cadastro não aprovado — {APP_NAME}',
        'accounts/emails/rejection.html',
        {
            'email': email,
            'church_name': church_name,
            'account_name': account_name,
            'status': 'rejected',
        },
    )


def send_password_reset(email, password, church_name, account_name):
    """Envia a nova senha do login responsável (após reset)."""
    _send_template_email(
        email,
        f'Senha alterada — {APP_NAME}',
        'accounts/emails/password_reset.html',
        {
            'email': email,
            'password': password,
            'church_name': church_name,
            'account_name': account_name,
        },
    )
