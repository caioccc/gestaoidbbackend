"""Armazenamento de mídia unificado (Cloudinary com fallback local p/ testes).

Todas as mídias do sistema sobem para o Cloudinary:
- Imagens/fotos (logo, foto do membro, comprovantes, materiais): MediaCloudinaryStorage.
- Arquivos brutos (PDF/DOCX/XLSX de atas e documentos): RawMediaCloudinaryStorage.

As pastas seguem o padrão multi-tenant:
    gestao_idb/churches/<church_id>/<kind>/<filename>

Durante a suíte de testes (USE_LOCAL_MEDIA_STORAGE=1, definido pelo settings
quando `manage.py test` roda) os mesmos storages apontam para o filesystem
local, mantendo os testes herméticos (sem rede).
"""

import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible

from cloudinary_storage.storage import MediaCloudinaryStorage, RawMediaCloudinaryStorage


def _pick(cloudinary_cls):
    if os.environ.get('USE_LOCAL_MEDIA_STORAGE', '') == '1':
        return FileSystemStorage(
            location=settings.MEDIA_ROOT,
            base_url=settings.MEDIA_URL,
        )
    return cloudinary_cls()


media_storage = _pick(MediaCloudinaryStorage)
raw_storage = _pick(RawMediaCloudinaryStorage)


@deconstructible
class church_upload_to:
    """upload_to que coloca o arquivo na pasta da igreja do registro.

    Estrutura: gestao_idb/churches/<church_id>/<kind>/<filename>.
    """

    def __init__(self, kind: str):
        self.kind = kind

    def __call__(self, instance, filename):
        church_id = getattr(instance, 'church_id', None)
        if not church_id:
            church = getattr(instance, 'church', None)
            if church is not None:
                church_id = getattr(church, 'pk', None)
        if not church_id:
            member = getattr(instance, 'member', None)
            church_id = getattr(member, 'church_id', None)
        prefix = f'gestao_idb/churches/{church_id}/' if church_id else ''
        return f'{prefix}{self.kind}/{filename}'


def church_logo_upload_to(instance, filename):
    """upload_to do logo (instance é a própria Church)."""
    church_id = getattr(instance, 'pk', None)
    prefix = f'gestao_idb/churches/{church_id}/' if church_id else ''
    return f'{prefix}logos/{filename}'