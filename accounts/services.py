"""Serviços de notificação por e-mail do app accounts.

Centraliza o envio de e-mails transacionais: credenciais no cadastro,
e notificações de aprovação/rejeição de congregações.
"""
import base64
import logging
import re

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.mail import EmailMultiAlternatives
from django.db import transaction as db_transaction
from django.template.loader import render_to_string

def next_member_card_number(church) -> str:
    """Próxima matrícula sequencial por igreja (ex.: '0007'), sem prefixo.

    Baseia-se no maior número já atribuído para não colidir após exclusões.
    """
    from django.db import models
    from django.db.models.functions import Cast

    from .models import Member

    latest = (
        Member.objects.filter(church=church)
        .exclude(card_number__isnull=True)
        .filter(card_number__regex=r'^\d{1,10}$')
        .annotate(num=Cast('card_number', output_field=models.IntegerField()))
        .order_by('-num')
        .values_list('num', flat=True)
        .first()
    )
    return f'{(latest or 0) + 1:04d}'


logger = logging.getLogger(__name__)

APP_NAME = 'Gestão IDB'
APP_TAGLINE = 'Sistema Integrado de Gestão Eclesial das Igrejas de Deus no Brasil'


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


def normalize_accounting_category(raw) -> str:
    """Normaliza a chave de uma categoria contábil para uso/consulta.

    Ex.: 'Congregação ABC' -> 'CONGREGACAO_ABC'; 'sede/centro' -> 'SEDE_CENTRO'.
    Remove acentos, espaços, barras e hífens (trocados por underscore).
    """
    import unicodedata

    if not raw:
        return ''
    text = unicodedata.normalize('NFKD', str(raw))
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = text.upper()
    text = text.replace(' ', '_').replace('/', '_').replace('-', '_')
    text = re.sub(r'[^A-Z0-9_]+', '_', text)
    text = re.sub(r'_+', '_', text).strip('_')
    return text


def persist_accounting_category(raw) -> str:
    """Garante categoria contábil válida, persistindo a personalizada no catálogo.

    Categorias padrão (DepartmentCategory) não geram registro. Categorias novas
    (ex.: CONGREGACAO_ABC) são gravadas na tabela `AccountingCategory` para uso
    futuro. Retorna a chave normalizada (ou '' se vazia/inválida).
    """
    from finance.models import DepartmentCategory

    from .models import AccountingCategory

    key = normalize_accounting_category(raw)
    if not key:
        return ''
    if key in DepartmentCategory.values:
        return key
    AccountingCategory.objects.get_or_create(
        key=key,
        defaults={'label': str(raw).strip()},
    )
    return key


def create_church_user(church, email, name, role):
    """Cria/vincula o primeiro usuário responsável de uma congregação.

    Reutilizado pela criação direta da Sede. Se o usuário ainda não existe,
    gera uma senha aleatória, cria a conta ativa e envia as credenciais.
    """
    from django.utils.crypto import get_random_string

    from .models import ChurchMembership, User as UserModel

    email = (email or '').strip().lower()
    user = UserModel.objects.filter(email__iexact=email).first()
    if user is None:
        password = get_random_string(length=10)
        user = UserModel.objects.create_user(
            email=email,
            password=password,
            name=(name or '').strip(),
            is_active=True,
        )
        send_registration_credentials(
            email=email,
            password=password,
            church_name=church.name,
            account_name=user.name,
        )
    membership, _created = ChurchMembership.objects.get_or_create(
        user=user,
        church=church,
        defaults={'role': role},
    )
    if not _created and membership.role != role:
        membership.role = role
        membership.save(update_fields=['role'])
    if user.church_id is None:
        user.church = church
        user.is_active = True
        user.save(update_fields=['church', 'is_active'])
    return user, membership


def link_church_user(church, user, role):
    """Vincula um usuário já cadastrado como responsável de uma congregação.

    Usado no fluxo "responsável = usuário existente": cria (ou atualiza) o
    vínculo ChurchMembership e aponta User.church quando o usuário ainda não
    tem igreja ativa — sem criar conta nova nem enviar credenciais.
    """
    from .models import ChurchMembership

    membership, _created = ChurchMembership.objects.get_or_create(
        user=user,
        church=church,
        defaults={'role': role},
    )
    if not _created and membership.role != role:
        membership.role = role
        membership.save(update_fields=['role'])
    if user.church_id is None:
        user.church = church
        user.is_active = True
        user.save(update_fields=['church', 'is_active'])
    return user, membership


def data_url_to_file(data_url, name):
    """Converte um data URL (ex.: data:image/png;base64,...) em UploadedFile.

    Usa SimpleUploadedFile para que um CloudinaryField.pre_save reconheça o
    valor e envie a imagem ao Cloudinary durante o save() do modelo.
    """
    if not data_url or ',' not in data_url:
        return None
    try:
        header, b64 = data_url.split(',', 1)
        content = base64.b64decode(b64)
    except (ValueError, TypeError, base64.binascii.Error):
        return None
    if not content:
        return None
    content_type = (
        header.split(';')[0].replace('data:', '') if 'data:' in header else 'image/png'
    )
    return SimpleUploadedFile(name, content, content_type=content_type)


def cloudinary_url(field):
    """Resolve a URL acessível de um CloudinaryField (ou None)."""
    if not field:
        return None
    try:
        return getattr(field, 'url', None) or (field.name if field.name else None)
    except Exception:  # noqa: BLE001
        return field.name if getattr(field, 'name', None) else None


# ---------------------------------------------------------------------------
# Importação do rol de membros (planilha xlsx/xls/csv)
# ---------------------------------------------------------------------------

EXPECTED_MEMBER_COLUMNS = [
    {'key': 'name', 'label': 'Nome', 'required': True,
     'aliases': ['nome', 'nome do membro', 'membro']},
    {'key': 'phone', 'label': 'Telefone', 'required': False,
     'aliases': ['telefone', 'telefone whatsapp', 'celular', 'whatsapp', 'contato']},
    {'key': 'email', 'label': 'E-mail', 'required': False,
     'aliases': ['e-mail', 'email', 'email do membro']},
    {'key': 'birth_date', 'label': 'Data de Nascimento', 'required': False,
     'aliases': ['nascimento', 'data de nascimento', 'data nascimento', 'nasc']},
    {'key': 'baptism_date', 'label': 'Data de Batismo', 'required': False,
     'aliases': ['batismo', 'data de batismo', 'data batismo']},
    {'key': 'cpf', 'label': 'CPF', 'required': False, 'aliases': ['cpf']},
    {'key': 'rg', 'label': 'RG', 'required': False, 'aliases': ['rg']},
    {'key': 'born_in_city', 'label': 'Naturalidade', 'required': False,
     'aliases': ['naturalidade', 'cidade de nascimento', 'cidade nascimento']},
    {'key': 'born_in_state', 'label': 'UF Nascimento', 'required': False,
     'aliases': ['uf', 'uf nascimento', 'estado nascimento']},
    {'key': 'profession', 'label': 'Profissão', 'required': False,
     'aliases': ['profissao', 'profissão', 'ocupacao', 'ocupação']},
    {'key': 'education_level', 'label': 'Escolaridade', 'required': False,
     'aliases': ['escolaridade']},
    {'key': 'marital_status', 'label': 'Estado Civil', 'required': False,
     'aliases': ['estado civil']},
    {'key': 'marriage_date', 'label': 'Data de Casamento', 'required': False,
     'aliases': ['casamento', 'data de casamento', 'data casamento']},
    {'key': 'father_name', 'label': 'Nome do Pai', 'required': False,
     'aliases': ['nome do pai', 'pai']},
    {'key': 'mother_name', 'label': 'Nome da Mãe', 'required': False,
     'aliases': ['nome da mae', 'nome da mãe', 'mae', 'mãe']},
    {'key': 'church_entry', 'label': 'Forma de Entrada', 'required': False,
     'aliases': ['forma de entrada', 'entrada']},
    {'key': 'status', 'label': 'Status', 'required': False,
     'aliases': ['status', 'situacao', 'situação']},
    {'key': 'street', 'label': 'Rua / Avenida', 'required': False,
     'aliases': ['rua', 'avenida', 'logradouro', 'endereco', 'endereço']},
    {'key': 'number', 'label': 'Número', 'required': False,
     'aliases': ['numero', 'número', 'nº', 'no']},
    {'key': 'complement', 'label': 'Complemento', 'required': False,
     'aliases': ['complemento', 'complement', 'apto', 'bloco']},
    {'key': 'neighborhood', 'label': 'Bairro', 'required': False,
     'aliases': ['bairro']},
    {'key': 'city', 'label': 'Cidade', 'required': False,
     'aliases': ['cidade', 'municipio', 'município']},
    {'key': 'state', 'label': 'UF', 'required': False,
     'aliases': ['uf', 'estado', 'estado (uf)']},
    {'key': 'cep', 'label': 'CEP', 'required': False,
     'aliases': ['cep']},
]

_MEMBER_MATCH_THRESHOLD = 0.65


def _csv_rows_from_text(text: str) -> list:
    """Lê linhas de um CSV escolhendo o delimitador mais frequente.

    Diferente do csv.Sniffer (que falha/erra quando há linhas com poucos
    campos, como ';;'), aqui contamos a ocorrência de cada delimitador nas
    linhas não-vazias e usamos o mais provável.
    """
    import csv  # noqa: PLC0415

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    nonempty_lines = [ln for ln in lines if len([c for c in ln if c in ',;\t|']) > 1]
    candidates = nonempty_lines or lines
    counts = {d: 0 for d in (',', ';', '\t', '|')}
    for ln in candidates:
        for d in counts:
            counts[d] += ln.count(d)
    delimiter = max(counts, key=counts.get) if any(counts.values()) else ','
    # A linha que separa apenas colunas pode ter o mesmo contagem; priorize ;
    # quando houver ';' (padrão brasileiro de exportação).
    if counts.get(';') and counts[';'] >= counts.get(',', 0):
        delimiter = ';'
    rows = list(csv.reader(text.splitlines(), delimiter=delimiter))
    return rows


def _read_member_headers(file) -> list:
    """Lê a linha de cabeçalho de xls/xlsx/csv (membros usam a linha 0)."""
    name = getattr(file, 'name', '') or ''
    ext = name.lower().rsplit('.', 1)[-1] if '.' in name else ''
    if ext == 'csv':
        import io  # noqa: PLC0415
        file.seek(0)
        data = file.read()
        file.seek(0)
        text = None
        for enc in ('utf-8-sig', 'utf-8', 'latin-1'):
            try:
                text = data.decode(enc)
                break
            except (UnicodeDecodeError, UnicodeEncodeError):
                continue
        if text is None:
            text = data.decode('utf-8', errors='replace')
        rows = _csv_rows_from_text(text)
        return rows[0] if rows else []
    try:
        import pandas as pd  # noqa: PLC0415
        df = pd.read_excel(file, header=None)
    except Exception:  # noqa: BLE001
        return []
    if df.shape[0] < 1:
        return []
    return ['' if pd.isna(v) else str(v).strip() for v in df.iloc[0].tolist()]


def _read_member_rows(file, n=3) -> list:
    """Lê as próximas `n` linhas de dados (após o cabeçalho)."""
    name = getattr(file, 'name', '') or ''
    ext = name.lower().rsplit('.', 1)[-1] if '.' in name else ''
    if ext == 'csv':
        import io  # noqa: PLC0415
        file.seek(0)
        data = file.read()
        file.seek(0)
        text = None
        for enc in ('utf-8-sig', 'utf-8', 'latin-1'):
            try:
                text = data.decode(enc)
                break
            except (UnicodeDecodeError, UnicodeEncodeError):
                continue
        if text is None:
            text = data.decode('utf-8', errors='replace')
        rows = _csv_rows_from_text(text)
        return [
            [str(c).strip() for c in row]
            for row in rows[1:1 + n]
        ]
    try:
        import pandas as pd  # noqa: PLC0415
        df = pd.read_excel(file, header=None)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for i in range(1, min(1 + n, df.shape[0])):
        out.append(['' if pd.isna(v) else str(v).strip() for v in df.iloc[i].tolist()])
    return out


def _norm_header(text: str) -> str:
    """Normaliza cabeçalho para casamento por similaridade."""
    import unicodedata  # noqa: PLC0415
    s = unicodedata.normalize('NFKD', str(text).strip().lower())
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9 ]', ' ', s).strip()


def _best_member_match(item, headers, used):
    """Melhor cabeçalho real (não usado) para a coluna esperada."""
    from difflib import SequenceMatcher  # noqa: PLC0415
    best, best_name = _MEMBER_MATCH_THRESHOLD, None
    for h in headers:
        hn = _norm_header(h)
        if not hn or h in used:
            continue
        score = max(SequenceMatcher(None, hn, a).ratio() for a in item['aliases'])
        if score > best:
            best, best_name = score, h
    return best_name


def inspect_members_sheet(file) -> dict:
    """Lê a planilha do rol e devolve cabeçalhos + sugestão de mapeamento."""
    headers = _read_member_headers(file)
    used: set = set()
    suggested: dict = {}
    for item in EXPECTED_MEMBER_COLUMNS:
        match = _best_member_match(item, headers, used)
        if match is not None:
            suggested[item['key']] = match
            used.add(match)
    return {
        'kind': 'members',
        'headers': headers,
        'expected': EXPECTED_MEMBER_COLUMNS,
        'suggested': suggested,
        'rows': _read_member_rows(file),
    }


def _norm_value(raw) -> str:
    """Uppercase sem acentos e sem espaços duplicados (para enumerações)."""
    import unicodedata  # noqa: PLC0415
    s = unicodedata.normalize('NFKD', str(raw or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', s).strip().upper()


def _parse_date(raw):
    """Converte dd/mm/aaaa ou aaaa-mm-dd em 'YYYY-MM-DD' (ou None)."""
    import datetime  # noqa: PLC0415
    from django.utils.dateparse import parse_date  # noqa: PLC0415

    raw = str(raw or '').strip()
    if not raw:
        return None
    value = parse_date(raw)
    if value is not None:
        return value.isoformat()
    for fmt in ('%d/%m/%Y', '%d-%m-%Y', '%d/%m/%y', '%d.%m.%Y'):
        try:
            return datetime.datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _canonical_cpf(raw) -> str:
    return re.sub(r'[^0-9]', '', str(raw or ''))


_MEMBER_ENUM_ALIASES = {
    'education_level': {
        'SEM_ESCOLARIDADE': {'SEM ESCOLARIDADE', 'NAO ALFABETIZADO', 'ANALFABETO'},
        'FUNDAMENTAL': {'FUNDAMENTAL', 'ENSINO FUNDAMENTAL', 'FUNDAMENTAL COMPLETO',
                        'ENSINO FUNDAMENTAL COMPLETO', 'PRIMARIO', '1 GRAU'},
        'MEDIO_INCOMPLETO': {'MEDIO INCOMPLETO', 'ENSINO MEDIO INCOMPLETO',
                             'SEGUNDO GRAU INCOMPLETO'},
        'MEDIO': {'MEDIO', 'MEDIO COMPLETO', 'ENSINO MEDIO', 'ENSINO MEDIO COMPLETO',
                  'SEGUNDO GRAU'},
        'SUPERIOR_INCOMPLETO': {'SUPERIOR INCOMPLETO', 'ENSINO SUPERIOR INCOMPLETO',
                                'FACULDADE INCOMPLETA'},
        'SUPERIOR': {'SUPERIOR', 'SUPERIOR COMPLETO', 'ENSINO SUPERIOR',
                     'ENSINO SUPERIOR COMPLETO', 'FACULDADE'},
        'POS_GRADUACAO': {'POS GRADUACAO', 'POS-GRADUACAO', 'MESTRADO', 'DOUTORADO',
                          'ESPECIALIZACAO'},
    },
    'marital_status': {
        'SOLTEIRO': {'SOLTEIRO', 'SOLTEIRA'},
        'CASADO': {'CASADO', 'CASADA'},
        'UNIAO_ESTAVEL': {'UNIAO ESTAVEL', 'UNIAO ESTAVEL CASADA'},
        'SEPARADO': {'SEPARADO', 'SEPARADA'},
        'DIVORCIADO': {'DIVORCIADO', 'DIVORCIADA'},
        'VIUVO': {'VIUVO', 'VIUVA'},
    },
    'church_entry': {
        'ACLAMACAO': {'ACLAMACAO', 'POR ACLAMACAO', 'ACLAMADO', 'ACLAMADA'},
        'BATISMO': {'BATISMO', 'BATIZADO', 'BATIZADA'},
        'RECONCILIACAO': {'RECONCILIACAO', 'RECONCILIADO', 'RECONCILIADA'},
        'TRANSFERENCIA': {'TRANSFERENCIA', 'CARTA DE TRANSFERENCIA', 'TRANSFERIDO',
                          'TRANSFERIDA'},
        'OUTRO': {'OUTRO', 'OUTRA'},
    },
    'status': {
        'ACTIVE': {'ATIVO', 'ATIVA', 'MEMBRO ATIVO'},
        'INACTIVE': {'INATIVO', 'INATIVA'},
    },
}


def _parse_enum(field, raw) -> str:
    """Normaliza um valor livre para o enum do membro (ou '' se não casa)."""
    norm = _norm_value(raw)
    if not norm:
        return ''
    for value, aliases in _MEMBER_ENUM_ALIASES[field].items():
        if norm in aliases or norm == value:
            return value
    return ''


def import_member_rows(church, file, mapping: dict, dry_run: bool = False) -> dict:
    """Importa o rol de membros a partir de uma planilha (xlsx/xls/csv).

    `mapping` casa {chave_esperada: cabeçalho_real}. Em dry_run nada é gravado;
    o resultado traz `imported`, `updated`, `errors` e `errors_by_row`.
    """
    from .models import Member

    headers = _read_member_headers(file)
    index: dict = {}
    for key, header in (mapping or {}).items():
        if header is None:
            continue
        try:
            index[key] = headers.index(header)
        except ValueError:
            index[key] = -1

    def cell(row, key):
        i = index.get(key)
        if i is None or i < 0 or i >= len(row):
            return ''
        return str(row[i]).strip()

    errors: list = []
    rows_by_line: dict = {}
    parsed_rows: list = []
    seen_cpfs: set = set()
    existing_cpfs: set = set(
        Member.objects.filter(church=church)
        .exclude(cpf='')
        .values_list('cpf', flat=True)
    )

    raw_rows = _all_member_rows(file)

    for lineno, row in enumerate(raw_rows, start=2):
        if all(not str(c).strip() for c in row):
            continue
        name = cell(row, 'name')
        if not name:
            errors.append(f'Linha {lineno}: nome é obrigatório.')
            continue
        cpf = _canonical_cpf(cell(row, 'cpf'))
        if cpf:
            if cpf in seen_cpfs or cpf in existing_cpfs:
                errors.append(f'Linha {lineno}: CPF {cpf} já cadastrado.')
                continue
            seen_cpfs.add(cpf)
        birth_date = _parse_date(cell(row, 'birth_date'))
        if cell(row, 'birth_date') and birth_date is None:
            errors.append(f'Linha {lineno}: data de nascimento inválida.')
            continue
        baptism_date = _parse_date(cell(row, 'baptism_date'))
        marriage_date = _parse_date(cell(row, 'marriage_date'))
        data = {
            'name': name[:150],
            'phone': cell(row, 'phone')[:20],
            'email': cell(row, 'email')[:254],
            'cpf': cpf[:14],
            'rg': cell(row, 'rg')[:20],
            'born_in_city': cell(row, 'born_in_city')[:100],
            'born_in_state': cell(row, 'born_in_state')[:2],
            'profession': cell(row, 'profession')[:100],
            'father_name': cell(row, 'father_name')[:150],
            'mother_name': cell(row, 'mother_name')[:150],
            'education_level': _parse_enum('education_level', cell(row, 'education_level')),
            'marital_status': _parse_enum('marital_status', cell(row, 'marital_status')),
            'church_entry': _parse_enum('church_entry', cell(row, 'church_entry')),
            'status': _parse_enum('status', cell(row, 'status')) or Member.Status.ACTIVE,
            'street': cell(row, 'street')[:150],
            'number': cell(row, 'number')[:20],
            'complement': cell(row, 'complement')[:100],
            'neighborhood': cell(row, 'neighborhood')[:100],
            'city': cell(row, 'city')[:100],
            'state': cell(row, 'state')[:2].upper().replace('.', ''),
            'cep': cell(row, 'cep')[:9],
        }
        if data['education_level'] == '' and cell(row, 'education_level'):
            errors.append(f'Linha {lineno}: escolaridade "{cell(row, "education_level")}" inválida.')
            continue
        if data['marital_status'] == '' and cell(row, 'marital_status'):
            errors.append(f'Linha {lineno}: estado civil "{cell(row, "marital_status")}" inválido.')
            continue
        if data['church_entry'] == '' and cell(row, 'church_entry'):
            errors.append(f'Linha {lineno}: forma de entrada "{cell(row, "church_entry")}" inválida.')
            continue
        data['birth_date'] = birth_date
        data['baptism_date'] = baptism_date
        data['marriage_date'] = marriage_date
        data['line'] = lineno
        parsed_rows.append(data)

    result = {
        'imported': 0,
        'updated': 0,
        'skipped': 0,
        'dry_run': dry_run,
        'errors': [f'[Rol de membros] {e}' for e in errors],
        'rows_by_line': rows_by_line,
    }
    if errors and not dry_run:
        result['skipped'] = len(parsed_rows)
        return result

    if not dry_run:
        with db_transaction.atomic():
            for data in parsed_rows:
                line = data.pop('line')
                card = next_member_card_number(church)
                member = Member.objects.create(
                    church=church,
                    card_number=card,
                    **data,
                )
                rows_by_line[line] = ('created', member.id)
                result['imported'] += 1
    else:
        for data in parsed_rows:
            line = data.pop('line')
            rows_by_line[line] = ('created', None)
            result['imported'] += 1

    return result


def _all_member_rows(file) -> list:
    """Lê todas as linhas de dados do arquivo (cabeçalho na linha 0)."""
    name = getattr(file, 'name', '') or ''
    ext = name.lower().rsplit('.', 1)[-1] if '.' in name else ''
    if ext == 'csv':
        import io  # noqa: PLC0415
        file.seek(0)
        data = file.read()
        file.seek(0)
        text = None
        for enc in ('utf-8-sig', 'utf-8', 'latin-1'):
            try:
                text = data.decode(enc)
                break
            except (UnicodeDecodeError, UnicodeEncodeError):
                continue
        if text is None:
            text = data.decode('utf-8', errors='replace')
        rows = _csv_rows_from_text(text)
        return rows[1:] if len(rows) > 1 else []
    try:
        import pandas as pd  # noqa: PLC0415
        df = pd.read_excel(file, header=None)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for i in range(1, df.shape[0]):
        out.append(['' if pd.isna(v) else str(v).strip() for v in df.iloc[i].tolist()])
    return out


# ---------------------------------------------------------------------------
# Alertas (polling) — aniversariantes e validade da carteirinha
# ---------------------------------------------------------------------------

ALERT_CARD_VALIDITY_WINDOW_DAYS = 30
ALERT_UPCOMING_DAYS = 8  # hoje + 7 dias; o dia atual é excluído do bloco "próximos"


def _month_day(date):
    """Par (mês, dia), tratando 29/02 como 28/02 em anos não bissextos."""
    if date.month == 2 and date.day == 29:
        return (2, 28)
    return (date.month, date.day)


def compute_church_alerts(church, today=None, include_loans=False):
    """Alerta computado da igreja ativa: aniversários e validade da carteirinha.

    Devolve lista de dicionários (já serializáveis):
      - birthday_today / birthday_upcoming: um por membro;
      - card_validity_soon / card_validity_expired: um por igreja;
      - loan_return_today / loan_return_soon / loan_return_overdue: um por
        empréstimo aberto (somente quando `include_loans` é True).
    `today` pode ser passado (date) para testes determinísticos.
    """
    import datetime  # noqa: PLC0415
    from django.utils import timezone  # noqa: PLC0415

    from .models import Loan, Member

    if today is None:
        today = timezone.localdate()
    alerts = []

    members = (
        Member.objects.filter(church=church)
        .exclude(birth_date__isnull=True)
        .order_by('name')
    )

    upcoming_days = [today + datetime.timedelta(days=off) for off in range(ALERT_UPCOMING_DAYS)]
    upcoming_md = {_month_day(d) for d in upcoming_days}
    today_md = _month_day(today)
    upcoming_md.discard(today_md)

    for member in members:
        md = _month_day(member.birth_date)
        if md == today_md:
            alerts.append({
                'type': 'birthday_today',
                'member_id': member.id,
                'member_name': member.name,
                'date': today.isoformat(),
            })
        elif md in upcoming_md:
            match_date = next(
                (d for d in upcoming_days if _month_day(d) == md),
                today,
            )
            alerts.append({
                'type': 'birthday_upcoming',
                'member_id': member.id,
                'member_name': member.name,
                'date': match_date.isoformat(),
            })

    expiry = getattr(church, 'card_valid_until', None)
    if expiry is not None:
        if expiry < today:
            alerts.append({
                'type': 'card_validity_expired',
                'date': expiry.isoformat(),
            })
        elif expiry <= today + datetime.timedelta(days=ALERT_CARD_VALIDITY_WINDOW_DAYS):
            alerts.append({
                'type': 'card_validity_soon',
                'date': expiry.isoformat(),
            })

    if include_loans and church is not None:
        open_loans = (
            Loan.objects.filter(church=church, returned_at__isnull=True)
            .select_related('item', 'member')
        )
        for loan in open_loans:
            if loan.expected_return == today:
                alert_type = 'loan_return_today'
            elif loan.expected_return < today:
                alert_type = 'loan_return_overdue'
            elif loan.expected_return <= today + datetime.timedelta(
                days=ALERT_UPCOMING_DAYS - 1,
            ):
                alert_type = 'loan_return_soon'
            else:
                continue
            alerts.append({
                'type': alert_type,
                'loan_id': loan.id,
                'item_id': loan.item_id,
                'item_name': loan.item.name,
                'borrower_name': loan.borrower_display,
                'date': loan.expected_return.isoformat(),
            })

    return alerts


# ---------------------------------------------------------------------------
# Comunicação semiautomática via WhatsApp (funil pastoral)
# ---------------------------------------------------------------------------

DEFAULT_MESSAGE_TEMPLATES = [
    {
        'title': 'Aniversário',
        'category': 'BIRTHDAY',
        'content': (
            '🎉 Feliz aniversário, {{PRIMEIRO_NOME}}! Que Deus abençoe o seu novo ano '
            'de vida com muita paz, saúde e alegria.\n\nUm abraço carinhoso de toda a '
            'família {{IGREJA}}. 🙏'
        ),
    },
    {
        'title': 'Boas-Vindas',
        'category': 'WELCOME',
        'content': (
            '🙏 Que alegria ter você conosco na {{IGREJA}}, {{NOME}}! Ficamos muito '
            'felizes com a sua chegada. Se precisar de alguma ajuda ou quiser conversar, '
            'estamos por aqui. 😊'
        ),
    },
    {
        'title': 'Cuidado / Ausência',
        'category': 'CARE',
        'content': (
            '💙 Oi, {{PRIMEIRO_NOME}}! Sentimos a sua falta por aqui e queremos saber '
            'como você está. Se precisar conversar, orar ou de qualquer ajuda, estamos '
            'bem pertinho. Um abraço da {{IGREJA}}.'
        ),
    },
]


def normalize_whatsapp_phone(phone) -> str | None:
    """Normaliza telefone para o formato do wa.me: só dígitos, com DDI 55.

    Aceita máscaras como `(83) 99800-1234` e devolve `5583998001234` (12–15
    dígitos). Retorna `None` quando o telefone é inválido/vazio.
    """
    digits = re.sub(r'\D', '', phone or '')
    if len(digits) in (10, 11):
        digits = f'55{digits}'
    if len(digits) < 12 or len(digits) > 15:
        return None
    return digits


def render_message_template(content, member, church) -> str:
    """Interpola os tokens `{{NOME}}`, `{{PRIMEIRO_NOME}}`, `{{IGREJA}}` e `{{CIDADE}}`."""
    name = (member.name or '').strip()
    first_name = name.split(' ', 1)[0]
    replacements = {
        '{{NOME}}': name,
        '{{PRIMEIRO_NOME}}': first_name,
        '{{IGREJA}}': church.name if church else '',
        '{{CIDADE}}': getattr(church, 'city', '') or '',
    }
    result = content or ''
    for token, value in replacements.items():
        result = result.replace(token, value)
    return result


def build_whatsapp_url(phone, message) -> str:
    """Monta a URL `https://wa.me/<telefone>?text=...` com o texto codificado.

    A codificação via `quote(message)` preserva acentuação, emojis e quebras de
    linha (`%0A`) aceitos pelo WhatsApp.
    """
    from urllib.parse import quote  # noqa: PLC0415

    digits = normalize_whatsapp_phone(phone)
    if not digits:
        raise ValueError('Telefone de WhatsApp inválido.')
    if not message:
        return f'https://wa.me/{digits}'
    return f'https://wa.me/{digits}?text={quote(message)}'


def get_or_create_default_message_templates(church):
    """Cria os modelos de mensagem padrão (PT) para uma igreja, se não existirem.

    Idempotente: usa `get_or_create` por `(church, category)` e preserva
    quaisquer templates que a Secretaria já tenha criado/customizado.
    """
    from .models import MessageTemplate  # noqa: PLC0415

    created = []
    for data in DEFAULT_MESSAGE_TEMPLATES:
        defaults = {k: v for k, v in data.items() if k != 'category'}
        template, was_created = MessageTemplate.objects.get_or_create(
            church=church,
            category=data['category'],
            defaults=defaults,
        )
        if was_created:
            created.append(template)
    return created


def compute_secretary_actions(church, today=None):
    """Ações recomendadas para o painel da Secretaria.

    Devolve listas de membros acionáveis (com telefone), para o envio
    semiautomático via WhatsApp:
      - birthdays_today: aniversariantes do dia;
      - absent_pending_contact: estágio ABSENT_CARE sem contato há >15 dias;
      - new_visitors: estágios VISITOR/INTEGRATION sem contato há >7 dias;
      - cards_expiring: igreja com carteirinha vencendo em <=30 dias (mesma
        janela dos alertas) — lista os membros ativos com matrícula.
    `today` pode ser passado (date) para testes determinísticos.
    """
    import datetime  # noqa: PLC0415
    from django.utils import timezone  # noqa: PLC0415

    from .models import Member  # noqa: PLC0415

    if today is None:
        today = timezone.localdate()

    def item(member, category_hint):
        return {
            'member_id': member.id,
            'name': member.name,
            'photo': cloudinary_url(getattr(member, 'photo', None)),
            'phone': member.phone,
            'lifecycle_stage': member.lifecycle_stage,
            'category_hint': category_hint,
        }

    members = (
        Member.objects.filter(church=church, status=Member.Status.ACTIVE)
        .exclude(phone__exact='')
        .order_by('name')
    )
    today_md = _month_day(today)

    birthdays_today = [
        item(m, 'BIRTHDAY')
        for m in members
        if m.birth_date is not None and _month_day(m.birth_date) == today_md
    ]

    absent_pending_contact = [
        item(m, 'CARE')
        for m in members.filter(lifecycle_stage=Member.LifecycleStage.ABSENT_CARE)
        if (
            m.last_contact_at is None
            or m.last_contact_at.date() < today - datetime.timedelta(days=15)
        )
    ]

    new_visitors = []
    for m in members.filter(
        lifecycle_stage__in=[
            Member.LifecycleStage.VISITOR,
            Member.LifecycleStage.INTEGRATION,
        ],
    ):
        if (
            m.last_contact_at is None
            or m.last_contact_at.date() < today - datetime.timedelta(days=7)
        ):
            new_visitors.append(item(m, 'WELCOME'))

    cards_expiring = []
    expiry = getattr(church, 'card_valid_until', None)
    if expiry is not None and expiry <= today + datetime.timedelta(
        days=ALERT_CARD_VALIDITY_WINDOW_DAYS
    ):
        cards_expiring = [
            item(m, 'CARD_EXPIRING')
            for m in members.exclude(card_number__isnull=True).exclude(card_number='')
        ]

    return {
        'birthdays_today': birthdays_today,
        'absent_pending_contact': absent_pending_contact,
        'new_visitors': new_visitors,
        'cards_expiring': cards_expiring,
    }


# ---------------------------------------------------------------------------
# Declaração / comprovante de membresia (PDF)
# ---------------------------------------------------------------------------

_MONTH_PT = [
    None, 'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho',
    'agosto', 'setembro', 'outubro', 'novembro', 'dezembro',
]


def member_age(birth_date, on=None):
    """Idade do membro em anos (None quando não há data de nascimento).

    Aceita `on` injetável para testes (data de referência).
    """
    from datetime import date  # noqa: PLC0415

    if birth_date is None:
        return None
    today = on or date.today()
    had = (today.month, today.day) < (birth_date.month, birth_date.day)
    return today.year - birth_date.year - int(had)


def build_membership_declaration_pdf(
    member,
    signer_name='',
    signer_role='Pastor Responsável',
    on=None,
) -> bytes:
    """Gera o PDF da declaração de membresia (xhtml2pdf)."""
    from io import BytesIO

    from django.utils import timezone

    from xhtml2pdf import pisa

    from .models import Member

    today = on or timezone.localdate()
    entry = member.get_church_entry_display() if member.church_entry else ''
    if member.church_entry == Member.ChurchEntry.OUTRO and member.church_entry_other:
        entry = member.church_entry_other

    context = {
        'church': member.church,
        'member': member,
        'entry_label': entry,
        'protocol': f'{member.id:04d}/{today.year}',
        'emitted_date': (
            f'{today.day} de {_MONTH_PT[today.month]} de {today.year}'
        ),
        'signer_name': signer_name,
        'signer_role': signer_role,
    }

    html = render_to_string('accounts/membership_declaration.html', context)
    output = BytesIO()
    status = pisa.CreatePDF(html, dest=output, encoding='utf-8')
    if status.err:
        raise RuntimeError('Erro ao gerar o PDF da declaração de membresia.')
    return output.getvalue()


def build_members_report_pdf(church, members, on=None) -> bytes:
    """Gera o PDF do rol de membros da igreja (xhtml2pdf).

    `members` é uma QuerySet já escopada/filtrada pela view (somente os
    membros que devem constar no relatório).
    """
    from io import BytesIO

    from django.utils import timezone

    from xhtml2pdf import pisa

    today = on or timezone.localdate()
    total = members.count() if hasattr(members, 'count') else len(members)
    context = {
        'church': church,
        'members': members,
        'total': total,
        'generated_date': (
            f'{today.day} de {_MONTH_PT[today.month]} de {today.year}'
        ),
    }

    html = render_to_string('accounts/members_report.html', context)
    output = BytesIO()
    status = pisa.CreatePDF(html, dest=output, encoding='utf-8')
    if status.err:
        raise RuntimeError('Erro ao gerar o PDF do rol de membros.')
    return output.getvalue()


# ---------------------------------------------------------------------------
# Transferência de membresia entre igrejas
# ---------------------------------------------------------------------------

def issue_member_transfer(source_church, member, target_church) -> object:
    """Emita uma transferência de membresia (origem → destino)."""
    from datetime import date  # noqa: PLC0415

    from django.utils import timezone  # noqa: PLC0415

    from rest_framework.exceptions import ValidationError  # noqa: PLC0415

    from .models import Member, MemberTransfer  # noqa: PLC0415

    if member.church_id != source_church.id:
        raise ValidationError(
            {'member_id': 'O membro não pertence à sua igreja ativa.'}
        )
    if member.status != Member.Status.ACTIVE:
        raise ValidationError(
            {'member_id': 'Apenas membros ativos podem ser transferidos.'}
        )
    if target_church.id == source_church.id:
        raise ValidationError(
            {'target_church_id': 'A igreja de destino deve ser diferente da atual.'}
        )
    if target_church.status != 'ACTIVE':
        raise ValidationError(
            {'target_church_id': 'A igreja de destino precisa estar ativa.'}
        )
    pending = MemberTransfer.objects.filter(
        source_member=member, status=MemberTransfer.Status.PENDING,
    ).exists()
    if pending:
        raise ValidationError(
            {'member_id': 'Este membro já possui uma transferência pendente.'}
        )

    today = date.today()
    return MemberTransfer.objects.create(
        source_church=source_church,
        source_member=member,
        target_church=target_church,
        member_name=member.name,
        member_cpf=member.cpf,
        member_rg=member.rg,
        member_birth_date=member.birth_date,
        member_baptism_date=member.baptism_date,
        member_phone=member.phone,
        member_email=member.email,
        member_profession=member.profession,
        member_father_name=member.father_name,
        member_mother_name=member.mother_name,
        member_street=member.street,
        member_number=member.number,
        member_complement=member.complement,
        member_neighborhood=member.neighborhood,
        member_city=member.city,
        member_state=member.state,
        member_cep=member.cep,
        member_notes=member.notes or f'Emitida em {today:%d/%m/%Y}.',
    )


def receive_member_transfer(target_church, transfer, by_user) -> object:
    """Recebe a transferência na igreja de destino: cria o membro no rol e
    inativa o membro de origem."""
    from django.utils import timezone  # noqa: PLC0415

    from rest_framework.exceptions import PermissionDenied, ValidationError  # noqa: PLC0415

    from .models import Member, MemberTransfer  # noqa: PLC0415

    if transfer.target_church_id != target_church.id:
        raise PermissionDenied('Esta transferência não é destinada à sua igreja.')
    if transfer.status != MemberTransfer.Status.PENDING:
        raise ValidationError({'detail': 'Esta transferência não está pendente.'})

    with db_transaction.atomic():
        member = Member.objects.create(
            church=target_church,
            card_number=next_member_card_number(target_church),
            name=transfer.member_name,
            cpf=transfer.member_cpf,
            rg=transfer.member_rg,
            birth_date=transfer.member_birth_date,
            baptism_date=transfer.member_baptism_date,
            phone=transfer.member_phone,
            email=transfer.member_email,
            profession=transfer.member_profession,
            father_name=transfer.member_father_name,
            mother_name=transfer.member_mother_name,
            street=transfer.member_street,
            number=transfer.member_number,
            complement=transfer.member_complement,
            neighborhood=transfer.member_neighborhood,
            city=transfer.member_city,
            state=transfer.member_state,
            cep=transfer.member_cep,
            notes=transfer.member_notes,
            church_entry=Member.ChurchEntry.TRANSFERENCIA,
            status=Member.Status.ACTIVE,
        )
        transfer.status = MemberTransfer.Status.RECEIVED
        transfer.received_at = timezone.now()
        transfer.received_by = by_user
        transfer.save(update_fields=['status', 'received_at', 'received_by'])
        Member.objects.filter(
            pk=transfer.source_member_id,
            church=transfer.source_church,
        ).update(status=Member.Status.INACTIVE)
    return member


def cancel_member_transfer(source_church, transfer, by_user) -> None:
    from django.utils import timezone  # noqa: PLC0415

    from rest_framework.exceptions import PermissionDenied, ValidationError  # noqa: PLC0415

    from .models import MemberTransfer  # noqa: PLC0415

    if transfer.source_church_id != source_church.id:
        raise PermissionDenied('Você não pode cancelar esta transferência.')
    if transfer.status != MemberTransfer.Status.PENDING:
        raise ValidationError({'detail': 'Esta transferência não está pendente.'})
    transfer.status = MemberTransfer.Status.CANCELED
    transfer.canceled_at = timezone.now()
    transfer.canceled_by = by_user
    transfer.save(update_fields=['status', 'canceled_at', 'canceled_by'])


def build_base_pdf_certificate(certificate) -> bytes:
    """Compila o PDF final de um certificado no modo BASE_PDF.

    Lê o documento base enviado pela igreja (Cloudinary Raw) e sobrepõe os
    dados do registro com reportlab, mesclando cada página gerada à base via
    pypdf. Retorna o conteúdo do PDF em bytes (pronto para salvar no storage).
    """
    from io import BytesIO

    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas

    from .models import CertificateTemplate

    template = certificate.template
    if template is None or template.layout_mode != CertificateTemplate.LayoutMode.BASE_PDF:
        raise ValueError('Certificado sem template BASE_PDF.')
    if not template.base_pdf or not template.base_pdf.name:
        raise ValueError('O template não possui documento base em PDF.')

    def _wrap_text(text, max_width, size):
        words = (text or '').split()
        lines = []
        current = ''
        for word in words:
            candidate = f'{current} {word}'.strip()
            if stringWidth(candidate, 'Helvetica', size) <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    source = template.base_pdf
    source.seek(0)
    reader = PdfReader(source)
    if not reader.pages:
        raise ValueError('O documento base não possui páginas.')

    church = certificate.church
    type_display = certificate.get_certificate_type_display()
    event_date = certificate.event_date.strftime('%d/%m/%Y')
    created_date = certificate.created_at.date().strftime('%d/%m/%Y')
    place = ', '.join(
        p for p in (church.city, church.state) if p
    )
    registry_bits = [
        (certificate.registry_number, 'Termo'),
        (certificate.registry_page, 'Folha'),
        (certificate.registry_book, 'Livro'),
    ]
    registry_parts = []
    for value, label in registry_bits:
        if value:
            registry_parts.append(f'{label} {value}')
    registry_line = ' | '.join(registry_parts)

    def _wrap_groups(lines, max_width):
        groups = []
        for line in lines:
            for piece in _wrap_text(line, max_width, 11):
                groups.append((piece, 11))
        return groups

    lines_below = [
        (f'{type_display}', 'Helvetica-Bold', 20, 0.62, 0.80, 0.13),
        ('', '', 10, 0, 0, 0),
        (f'{church.name}', 'Helvetica', 14, 0.12, 0.31, 0.48),
        (f'Rua: {place}' if place else '', 'Helvetica', 11, 0.2, 0.2, 0.2),
        ('', '', 10, 0, 0, 0),
        ('CONCEDE O PRESENTE CERTIFICADO A', 'Helvetica', 11, 0.2, 0.2, 0.2),
        (certificate.recipient_name, 'Helvetica-Bold', 26, 0.12, 0.31, 0.48),
        ('de', 'Helvetica', 11, 0.2, 0.2, 0.2),
        ('', '', 10, 0, 0, 0),
    ]
    context_lines = []
    if certificate.member and certificate.member.card_number:
        context_lines.append(f'Matrícula: {certificate.member.card_number}')
    if certificate.father_name or certificate.mother_name:
        parents = ' e '.join(
            p for p in (certificate.father_name, certificate.mother_name) if p
        )
        context_lines.append(f'Filho(a) de: {parents}')
    if certificate.officiant_name:
        context_lines.append(f'Oficiada pelo Ministro: {certificate.officiant_name}')
    context_lines.append(f'Data: {event_date}')
    if registry_line:
        context_lines.append(f'Registro: {registry_line}')
    if certificate.scripture_verse:
        context_lines.append(certificate.scripture_verse)

    writer = PdfWriter()
    for page in reader.pages:
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        overlay_stream = BytesIO()
        c = canvas.Canvas(overlay_stream, pagesize=(width, height))
        c.translate(0, 0)

        c.setFillColorRGB(1, 1, 1, alpha=0.42)
        block_pad_x = width * 0.06
        block_pad_y = height * 0.18
        c.roundRect(
            block_pad_x, block_pad_y,
            width - 2 * block_pad_x, height - 2 * block_pad_y,
            width * 0.01, stroke=0, fill=1,
        )

        c.setFillColorRGB(0.12, 0.31, 0.48)
        c.setStrokeColorRGB(0.69, 0.55, 0.25)
        c.setLineWidth(0.8)
        c.roundRect(
            block_pad_x, block_pad_y,
            width - 2 * block_pad_x, height - 2 * block_pad_y,
            width * 0.01, stroke=1, fill=0,
        )

        block_height = height - 2 * block_pad_y
        content_height = block_pad_y + block_height - height * 0.06
        for text, font, size, r, g, b in lines_below:
            if not text:
                content_height -= size + 4
                continue
            c.setFont(font, size)
            c.setFillColorRGB(r, g, b)
            c.drawCentredString(width / 2, content_height - size, text)
            content_height -= size + 8

        if context_lines:
            content_height -= 8
            for text, size in _wrap_groups(context_lines, width - 2 * block_pad_x):
                c.setFont('Helvetica', size)
                c.setFillColorRGB(0.2, 0.2, 0.2)
                c.drawCentredString(width / 2, content_height - size, text)
                content_height -= size + 6

        c.setFont('Helvetica', 9)
        c.setFillColorRGB(0.4, 0.4, 0.4)
        c.drawCentredString(
            width / 2,
            5,
            f'Certificado emitido via Gestão IDB em {created_date}',
        )

        c.showPage()
        c.save()
        overlay_stream.seek(0)
        overlay_page = PdfReader(overlay_stream).pages[0]
        page.merge_page(overlay_page)
        writer.add_page(page)

    output = BytesIO()
    writer.write(output)
    return output.getvalue()
