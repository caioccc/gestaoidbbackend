"""Serviços de negócio do módulo financeiro (Eclésia IDB)."""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import re

import pandas as pd
from django.conf import settings
from django.db.models import Sum
from django.db import transaction

from .models import (
    CalendarEvent,
    DepartmentCategory,
    FinancialEntry,
    FinancialExit,
    MonthlyClosing,
    Tither,
    TitheRecord,
)

# Eventos padrão semeados automaticamente para cada congregação ativa.
DEFAULT_CALENDAR_EVENTS = [
    ('Energisa', CalendarEvent.Category.BILL, 10),
    ('Cagepa (água)', CalendarEvent.Category.BILL, 15),
    ('Internet', CalendarEvent.Category.BILL, 20),
    ('Envio do relatório (Convenção Regional)', CalendarEvent.Category.DEADLINE, 5),
]


def seed_default_calendar_events(church) -> int:
    """Cria os eventos padrão recorrentes de uma congregação (idempotente).

    Retorna o número de eventos criados. Já existentes não são duplicados.
    """
    if CalendarEvent.objects.filter(church=church, repeat_monthly=True).exists():
        return 0
    created = 0
    for title, category, day in DEFAULT_CALENDAR_EVENTS:
        CalendarEvent.objects.get_or_create(
            church=church,
            title=title,
            category=category.value,
            repeat_monthly=True,
            day=day,
        )
        created += 1
    return created

# Departamentos que recebem 10% sobre as ofertas na remessa regional.
TENTH_PERCENT_DEPARTMENTS = [
    DepartmentCategory.MULHERES,
    DepartmentCategory.HOMENS,
    DepartmentCategory.JOVENS,
    DepartmentCategory.ESC_BIBLICA,
    DepartmentCategory.INFANTIL,
    DepartmentCategory.ADOLESCENTES,
    DepartmentCategory.MISSOES,
    DepartmentCategory.CASAIS,
]

# Categoria de dízimo
DIZIMO_CATEGORY = DepartmentCategory.DIZIMO

# --------------------------------------------------------------------------- #
# Diretório dos arquivos oficiais de modelo (planilhas IDB)
# --------------------------------------------------------------------------- #
TEMPLATES_DIR: Path = settings.BASE_DIR / 'templates_spreadsheet'

# Nome dos meses (índice 0 = Janeiro) usado na matriz de dizimistas.
MONTH_NAMES = [
    'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
    'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro',
]

# Rótulos legíveis dos arquivos do assistente de importação, usados para
# agrupar erros por planilha em 'errors_by_file'.
IMPORT_FILE_LABELS = {
    'entries': 'Entradas (modelo_entradas.xlsx)',
    'exits': 'Saídas (modelo_saidas.xlsx)',
    'tithers': 'Dizimistas (modelo_membros_dizimistas.xls)',
}

# Rótulos dos modelos de saída usados apenas na conferência (diagnóstico).
CONFERENCIA_LABELS = {
    'caixa': 'Caixa IDB (modelo_caixa.xls)',
    'relatorio': 'Relatório Regional (modelo_relatorio.xls)',
}

# Mapeia cabeçalhos normalizados (strip/lower/sem acento e espaços -> _)
# das planilhas de entradas/saídas para a categoria contábil correspondente.
FUND_COLUMN_MAP = {
    'dizimo': DepartmentCategory.DIZIMO,
    'oferta': DepartmentCategory.OFERTA,
    'ofertas': DepartmentCategory.OFERTA,
    'construcao': DepartmentCategory.CONSTRUCAO,
    'especial': DepartmentCategory.ESPECIAL,
    'especiais': DepartmentCategory.ESPECIAL,
    'visao_corporativa': DepartmentCategory.VISAO_CORPORATIVA,
    'visao': DepartmentCategory.VISAO_CORPORATIVA,
    'missoes': DepartmentCategory.MISSOES,
    'mulheres': DepartmentCategory.MULHERES,
    'homens': DepartmentCategory.HOMENS,
    'jovens': DepartmentCategory.JOVENS,
    'esc_biblica': DepartmentCategory.ESC_BIBLICA,
    'escola_biblica': DepartmentCategory.ESC_BIBLICA,
    'escola_dominical': DepartmentCategory.ESC_BIBLICA,
    'ebd': DepartmentCategory.ESC_BIBLICA,
    'escola_biblica_dominical': DepartmentCategory.ESC_BIBLICA,
    'ebd_dominical': DepartmentCategory.ESC_BIBLICA,
    'infantil': DepartmentCategory.INFANTIL,
    'infantail': DepartmentCategory.INFANTIL,
    'adolescentes': DepartmentCategory.ADOLESCENTES,
    'casais': DepartmentCategory.CASAIS,
    'ministerio_de_casais': DepartmentCategory.CASAIS,
    'minist_de_casais': DepartmentCategory.CASAIS,
    'dep_casais': DepartmentCategory.CASAIS,
    'depto_casais': DepartmentCategory.CASAIS,
}

# Termos que indicam linhas de total/abertura a serem ignoradas nos parsers.
IGNORED_DESCRIPTION_TERMS = ('saldo anterior', 'saldo inicial', 'total', 'sub-total')


# --------------------------------------------------------------------------- #
# 1. Auditoria de integridade dos dízimos
# --------------------------------------------------------------------------- #
def reconcile_tithers(church, year: int, month: int) -> Dict:
    """Compara a soma dos registros de dízimo com as entradas de categoria
    DIZIMO no mês, retornando o status de conciliação e a divergência.

    Retorna:
        {
            'status': 'CONCILIADO' | 'DIVERGENTE',
            'total_tithe_records': Decimal,
            'total_entries_dizimo': Decimal,
            'difference': Decimal,
        }
    """
    total_records = (
        TitheRecord.objects.filter(
            tither__church=church, year=year, month=month,
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    )

    total_entries = (
        FinancialEntry.objects.filter(
            church=church,
            date__year=year,
            date__month=month,
            category=DIZIMO_CATEGORY,
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    )

    difference = total_records - total_entries
    # Comparação matemática tolerante a erros de ponto flutuante.
    reconciled = abs(difference) < Decimal('0.01')

    return {
        'year': year,
        'month': month,
        'status': 'CONCILIADO' if reconciled else 'DIVERGENTE',
        'total_tithe_records': total_records,
        'total_entries_dizimo': total_entries,
        'difference': difference,
        # Formato adicional (compatível com auditoria automática de dízimos)
        'is_reconciled': reconciled,
        'tithers_total': total_records,
        'entries_tithe_total': total_entries,
    }


# --------------------------------------------------------------------------- #
# 2. Cálculo da remessa financeira regional (Convenção Paraíba)
# --------------------------------------------------------------------------- #
def _sum_category(church, year: int, month: int, category: str) -> Decimal:
    return FinancialEntry.objects.filter(
        church=church, date__year=year, date__month=month,
        category=category,
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')


def calc_regional_remittance(
    church, year: int, month: int,
    category_totals: Dict[str, Decimal] = None,
) -> Dict:
    """Calcula a remessa financeira regional da Convenção Paraíba.

    - 15% sobre o Total de Dízimos:
        * 11% Região
        * 1% Fundo Ministerial
        * 1.5% Distrito
        * 1.5% Evangelismo

    - 10% sobre as Ofertas de cada departamento (Mulheres, Homens, Jovens,
      EBD, Infantil, Adolescentes, Missões).

    `category_totals` opcional: totais por categoria pré-calculados (ex.: prévia
    de importação). Quando ausente, os totais são lidos do banco.
    """

    def _total_for(cat: str) -> Decimal:
        if category_totals is not None:
            return category_totals.get(cat, Decimal('0.00'))
        return _sum_category(church, year, month, cat)

    total_tithes = _total_for(DIZIMO_CATEGORY)

    tithe_rateio = {
        'total_dizimos': total_tithes,
        'percentual_total': Decimal('0.15'),
        'percentual_regiao': Decimal('0.11'),
        'percentual_fundo_ministerial': Decimal('0.01'),
        'percentual_distrito': Decimal('0.015'),
        'percentual_evangelismo': Decimal('0.015'),
        'regiao': (total_tithes * Decimal('0.11')).quantize(Decimal('0.01')),
        'fundo_ministerial': (total_tithes * Decimal('0.01')).quantize(Decimal('0.01')),
        'distrito': (total_tithes * Decimal('0.015')).quantize(Decimal('0.01')),
        'evangelismo': (total_tithes * Decimal('0.015')).quantize(Decimal('0.01')),
    }
    tithe_rateio['total_remessa_dizimos'] = (
        tithe_rateio['regiao']
        + tithe_rateio['fundo_ministerial']
        + tithe_rateio['distrito']
        + tithe_rateio['evangelismo']
    )

    offers_by_department = {}
    total_offers_remittance = Decimal('0.00')
    for dep in TENTH_PERCENT_DEPARTMENTS:
        total = _total_for(dep)
        remittance = (total * Decimal('0.10')).quantize(Decimal('0.01'))
        total_offers_remittance += remittance
        offers_by_department[dep] = {
            'categoria': dep,
            'total': total,
            'percentual': Decimal('0.10'),
            'remessa': remittance,
        }

    total_remittance = (
        tithe_rateio['total_remessa_dizimos'] + total_offers_remittance
    )

    return {
        'title': 'Remessa Financeira Regional - Convenção Paraíba',
        'year': year,
        'month': month,
        'dizimos': tithe_rateio,
        'ofertas_departamentos': offers_by_department,
        'total_remittance_offers': total_offers_remittance,
        'total_remittance': total_remittance,
    }


def build_monthly_balance(church, year: int, month: int, totals: Dict = None) -> Dict:
    """Constrói o balancete mensal (entradas, saídas e saldo).

    `totals` opcional: valores pré-calculados (ex.: prévia de importação) com as
    chaves 'total_entries'/'total_exits'. Quando ausente, lê do banco.
    """
    if totals is not None:
        total_entries = Decimal(totals['total_entries'])
        total_exits = Decimal(totals['total_exits'])
    else:
        total_entries = (
            FinancialEntry.objects.filter(
                church=church, date__year=year, date__month=month,
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        )
        total_exits = (
            FinancialExit.objects.filter(
                church=church, date__year=year, date__month=month,
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        )
    return {
        'year': year,
        'month': month,
        'total_entries': total_entries,
        'total_exits': total_exits,
        'balance': total_entries - total_exits,
    }


# --------------------------------------------------------------------------- #
# 3. Resumo anual para o dashboard
# --------------------------------------------------------------------------- #
def dashboard_summary(church, year: int) -> Dict:
    """Resumo anual com totais e série temporal dos 12 meses."""
    entries = FinancialEntry.objects.filter(
        church=church, date__year=year,
    )
    exits = FinancialExit.objects.filter(
        church=church, date__year=year,
    )

    total_entries = entries.aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    total_exits = exits.aggregate(t=Sum('amount'))['t'] or Decimal('0.00')

    monthly_entries = {
        row['date__month']: row['t']
        for row in entries.values('date__month').annotate(t=Sum('amount'))
    }
    monthly_exits = {
        row['date__month']: row['t']
        for row in exits.values('date__month').annotate(t=Sum('amount'))
    }

    series = []
    cumulative = Decimal('0.00')
    for month in range(1, 13):
        e = monthly_entries.get(month, Decimal('0.00'))
        x = monthly_exits.get(month, Decimal('0.00'))
        cumulative += e - x
        series.append({
            'month': month,
            'entries': e,
            'exits': x,
            'monthly_balance': e - x,
            'cumulative': cumulative,
        })

    return {
        'year': year,
        'total_entries': total_entries,
        'total_exits': total_exits,
        'balance': total_entries - total_exits,
        'series': series,
    }


# --------------------------------------------------------------------------- #
# 4. Fechamento mensal/trimestre (Caixa IDB)
# --------------------------------------------------------------------------- #
def get_or_create_monthly_closing(church, year: int, month: int) -> Tuple[MonthlyClosing, bool]:
    """Obtém ou cria o fechamento mensal recalculando os totais."""
    closing, created = MonthlyClosing.objects.get_or_create(
        church=church, year=year, month=month,
    )
    balance = build_monthly_balance(church, year, month)
    closing.total_entries = balance['total_entries']
    closing.total_exits = balance['total_exits']
    # Saldo anterior: saldo final do último fechamento anterior a este mês.
    previous = (
        MonthlyClosing.objects.filter(
            church=church,
            year__lt=year,
        ).order_by('-year', '-month').exclude(pk=closing.pk).first()
    )
    if previous is None:
        previous = (
            MonthlyClosing.objects.filter(
                church=church, year=year, month__lt=month,
            ).order_by('-month').exclude(pk=closing.pk).first()
        )
    closing.previous_balance = previous.final_balance if previous else Decimal('0.00')
    closing.calculate_final_balance()
    closing.save()
    return closing, created


# --------------------------------------------------------------------------- #
# 5. Parser & ingestão de planilhas via Pandas
# --------------------------------------------------------------------------- #
def _parse_amount(value, line: int, field: str, errors: List[str]) -> Optional[Decimal]:
    """Normaliza valor monetário, removendo R$, vírgulas e moeda."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        if isinstance(value, (int, float, Decimal)):
            amount = Decimal(str(value))
        else:
            text = str(value).strip().replace('R$', '').replace(' ', '')
            text = text.replace('.', '').replace(',', '.')
            amount = Decimal(text or '0')
        return amount
    except (InvalidOperation, ValueError):
        errors.append(
            f'Linha {line}: campo "{field}" com valor numérico inválido ({value!r}).'
        )
        return None


def _normalize_header(name: str) -> str:
    """Normaliza cabeçalhos de coluna (acentos, espaços, minúsculas)."""
    text = str(name).lower().strip()
    text = re.sub(r'\s+', '_', text)
    accent_map = {
        'ç': 'c', 'ã': 'a', 'á': 'a', 'à': 'a', 'â': 'a',
        'é': 'e', 'ê': 'e', 'í': 'i', 'ó': 'o', 'ô': 'o',
        'õ': 'o', 'ú': 'u', 'ü': 'u', 'í': 'i',
    }
    for k, v in accent_map.items():
        text = text.replace(k, v)
    return text


def _rename_columns(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns=lambda c: _normalize_header(c))


def _to_int_day(value) -> Optional[int]:
    """Converte o valor do dia para inteiro, ou None se inválido/vazio."""
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _month_from_title(file) -> Optional[int]:
    """Extrai o mês (1-12) citado no título (linha 0) da planilha, se houver."""
    try:
        head = pd.read_excel(file, header=None, nrows=1)
    except Exception:  # noqa: BLE001
        return None
    text = ' '.join(str(v) for v in head.iloc[0].tolist() if not pd.isna(v))
    low = text.lower()
    for m_idx, m_name in enumerate(MONTH_NAMES, start=1):
        if m_name.lower() in low:
            return m_idx
    return None


FUND_ORDER = [
    'dizimo', 'oferta', 'construcao', 'especial', 'missoes', 'mulheres',
    'homens', 'jovens', 'esc_biblica', 'infantil',
    'visao_corporativa', 'adolescentes', 'casais',
]

FUND_LABELS = {
    'dizimo': 'Dízimo', 'oferta': 'Oferta', 'construcao': 'Construção',
    'especial': 'Especial', 'missoes': 'Missões', 'mulheres': 'Mulheres',
    'homens': 'Homens', 'jovens': 'Jovens', 'esc_biblica': 'Esc. Bíblica',
    'infantil': 'Infantil', 'visao_corporativa': 'Visão Corporativa',
    'adolescentes': 'Adolescentes', 'casais': 'Casais',
}

_FUND_ALIASES_BY_CAT: Dict[str, List[str]] = {}
for _alias, _cat in FUND_COLUMN_MAP.items():
    _FUND_ALIASES_BY_CAT.setdefault(_cat, []).append(_alias)


def _expected_fund_columns() -> List[Dict]:
    """Colunas de fundo esperadas (label + aliases normalizadas) por categoria."""
    cols: List[Dict] = []
    for key in FUND_ORDER:
        cat = next((c for _k, c in FUND_COLUMN_MAP.items() if _k == key), None)
        if cat is None:
            continue
        cols.append({
            'key': key,
            'label': FUND_LABELS[key],
            'required': False,
            'aliases': _FUND_ALIASES_BY_CAT.get(cat, [key]),
        })
    return cols


EXPECTED_COLUMNS: Dict[str, List[Dict]] = {
    'entries': [
        {'key': 'day', 'label': 'Dia', 'required': True,
         'aliases': ['dia', 'data']},
        {'key': 'description', 'label': 'Histórico do Culto', 'required': True,
         'aliases': ['historico_do_culto', 'historico', 'culto', 'descricao',
                     'descricao_do_culto', 'historico_culto']},
    ] + _expected_fund_columns(),
    'exits': [
        {'key': 'day', 'label': 'Dia', 'required': True,
         'aliases': ['dia', 'data']},
        {'key': 'description', 'label': 'Saída Discriminada', 'required': True,
         'aliases': ['saida_discriminada', 'saida', 'descricao',
                     'descricao_da_saida', 'discriminacao']},
    ] + _expected_fund_columns(),
    'tithers': [
        {'key': 'name', 'label': 'Nome', 'required': True,
         'aliases': ['nome', 'membro', 'nome_do_membro', 'dizimista',
                     'nome_do_dizimista']},
    ] + [
        {'key': m_norm, 'label': m_name, 'required': False, 'aliases': [m_norm]}
        for m_name in MONTH_NAMES
        for m_norm in [_normalize_header(m_name)]
    ],
}

_MATCH_THRESHOLD = 0.65


def _read_raw_headers(file, header_index: int) -> List[str]:
    """Lê a linha de cabeçalho de xls/xlsx/csv e devolve lista de strings."""
    name = getattr(file, 'name', '') or ''
    ext = name.lower().rsplit('.', 1)[-1] if '.' in name else ''
    if ext == 'csv':
        import csv  # noqa: PLC0415
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
        buf = io.StringIO(text)
        try:
            dialect = csv.Sniffer().sniff(buf.read(4096), delimiters=',;\t|')
            buf.seek(0)
        except Exception:  # noqa: BLE001
            dialect = csv.excel
        rows = list(csv.reader(buf, dialect))
        return rows[header_index] if header_index < len(rows) else []
    try:
        df = pd.read_excel(file, header=None)
    except Exception:  # noqa: BLE001
        return []
    if header_index >= df.shape[0]:
        return []
    return ['' if pd.isna(v) else str(v).strip() for v in df.iloc[header_index].tolist()]


def _read_raw_rows(file, header_index: int, n: int = 3) -> List[List[str]]:
    """Lê a linha de cabeçalho e as próximas `n` linhas de dados (xls/xlsx/csv).

    Devolve lista de linhas; cada linha é uma lista de strings alinhada ao
    índice das colunas reais. Usado para prévia das colunas mapeadas.
    """
    name = getattr(file, 'name', '') or ''
    ext = name.lower().rsplit('.', 1)[-1] if '.' in name else ''
    if ext == 'csv':
        import csv  # noqa: PLC0415
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
        buf = io.StringIO(text)
        try:
            dialect = csv.Sniffer().sniff(buf.read(4096), delimiters=',;\t|')
            buf.seek(0)
        except Exception:  # noqa: BLE001
            dialect = csv.excel
        rows = list(csv.reader(buf, dialect))
        start = header_index + 1
        return [
            [str(c).strip() for c in row]
            for row in rows[start:start + n]
        ]
    try:
        df = pd.read_excel(file, header=None)
    except Exception:  # noqa: BLE001
        return []
    start = header_index + 1
    end = min(start + n, df.shape[0])
    out: List[List[str]] = []
    for i in range(start, end):
        out.append(['' if pd.isna(v) else str(v).strip() for v in df.iloc[i].tolist()])
    return out


def _header_similarity(a: str, b: str) -> float:
    from difflib import SequenceMatcher  # noqa: PLC0415
    return SequenceMatcher(None, a, b).ratio()


def _best_match(item: Dict, headers: List[str], used: set) -> Optional[str]:
    """Melhor cabeçalho real (não usado) que casa com a coluna esperada."""
    best, best_name = _MATCH_THRESHOLD, None
    for h in headers:
        hn = _normalize_header(h)
        if not hn or h in used:
            continue
        score = max(_header_similarity(hn, a) for a in item['aliases'])
        if score > best:
            best, best_name = score, h
    return best_name


def inspect_spreadsheet(file, kind: str) -> Dict:
    """Lê a planilha e devolve cabeçalhos reais + sugestão de mapeamento."""
    header_index = 0 if kind == 'tithers' else 1
    headers = _read_raw_headers(file, header_index)
    expected = EXPECTED_COLUMNS[kind]
    used: set = set()
    suggested: Dict[str, str] = {}
    for item in expected:
        match = _best_match(item, headers, used)
        if match is not None:
            suggested[item['key']] = match
            used.add(match)
    return {
        'kind': kind,
        'headers': headers,
        'expected': expected,
        'suggested': suggested,
        'rows': _read_raw_rows(file, header_index),
    }


def _apply_mapping_rename(df, mapping):
    """Aplica o mapeamento (real -> chave canônica) e devolve df + chaves."""
    if not mapping:
        return df, None, None
    rename = {}
    for canonical, real in mapping.items():
        if real:
            rename[_normalize_header(real)] = canonical
    df = df.rename(columns=rename)
    return df, 'day', 'description'


def _parse_fund_sheet(file, header_index, day_key, desc_key, kind, year, month, mapping=None):
    """Lê planilha de entradas/saídas no formato 'Dia + coluna por fundo'.

    - header_index: índice da linha de cabeçalho (0-based).
    - day_key: nome normalizado da coluna de dia (ex.: 'dia').
    - desc_key: nome normalizado da coluna de descrição.
    - kind: 'entry' ou 'exit'.
    """
    errors: List[str] = []
    rows: List[dict] = []
    try:
        df = pd.read_excel(file, header=header_index)
    except Exception as exc:  # noqa: BLE001
        errors.append(f'Erro ao ler planilha de {kind}s: {exc}')
        return rows, errors

    # Detecta o mês mencionado no título (linha 0) e avisa se divergir da
    # competência selecionada — evita o confuso 'dia inválido' da causa raiz.
    if month:
        title_month = _month_from_title(file)
        if title_month and title_month != int(month):
            errors.append(
                f'Aviso: a planilha indica o mês "{MONTH_NAMES[title_month - 1]}" '
                f'no título, mas a competência selecionada é '
                f'"{MONTH_NAMES[int(month) - 1]}/{year}". Confira o período.'
            )

    df = _rename_columns(df)
    df, map_day, map_desc = _apply_mapping_rename(df, mapping)
    if map_day:
        day_key, desc_key = map_day, map_desc
    fund_cols = {
        col: cat for col, cat in FUND_COLUMN_MAP.items() if col in df.columns
    }
    if day_key not in df.columns or desc_key not in df.columns:
        errors.append(
            'Planilha sem colunas esperadas '
            f'({day_key!r} e {desc_key!r}).'
        )
        return rows, errors
    if not fund_cols:
        errors.append('Planilha sem colunas de fundos reconhecidas.')
        return rows, errors

    max_days = calendar.monthrange(int(year), int(month))[1]
    for idx, row in df.iterrows():
        line = idx + header_index + 2  # linha real do Excel
        dia = _to_int_day(row.get(day_key))
        desc = str(row.get(desc_key) or '').strip()
        if dia is None or not desc:
            continue
        if any(term in desc.lower() for term in IGNORED_DESCRIPTION_TERMS):
            continue
        if dia < 1 or dia > max_days:
            errors.append(
                f"Linha {line}: o dia {dia} não existe no mês selecionado "
                f"({int(month):02d}/{int(year)}). O mês possui apenas "
                f"{max_days} dias. Verifique se o arquivo corresponde à "
                'competência correta.'
            )
            continue
        d = date(int(year), int(month), dia)

        for col, categoria in fund_cols.items():
            valor = _parse_amount(row.get(col), line, col, errors)
            if valor is not None and valor > 0:
                if kind == 'entry':
                    rows.append({
                        'date': d,
                        'service_description': desc,
                        'category': categoria,
                        'amount': valor,
                    })
                else:
                    rows.append({
                        'date': d,
                        'description': desc,
                        'category': categoria,
                        'amount': valor,
                    })
    return rows, errors


def parse_entries_sheet(file, year=None, month=None, mapping=None) -> Tuple[List[dict], List[str]]:
    """Lê a planilha oficial 'modelo_entradas.xlsx' e retorna as entradas.

    Cabeçalho na linha 1 (linha 0 = título "Controle de Entradas do Mês de...").
    Colunas: Dia, Histórico do Culto e uma coluna por fundo.
    Desconsidera linhas sem Dia válido ou com "Saldo Anterior"/"Total".
    `mapping` (opcional): {chave esperada: cabeçalho real} para planilhas
    com nomes/layout diferentes.
    """
    return _parse_fund_sheet(
        file, header_index=1, day_key='dia', desc_key='historico_do_culto',
        kind='entry', year=year, month=month, mapping=mapping,
    )


def parse_exits_sheet(file, year=None, month=None, mapping=None) -> Tuple[List[dict], List[str]]:
    """Lê a planilha oficial 'modelo_saidas.xlsx' e retorna as saídas.

    Cabeçalho na linha 1 (linha 0 = título "Controle de Saidas Mês...").
    Colunas: Dia, Saída Discriminada e uma coluna por fundo (com variações
    como 'Dizimo'/'Dízimo' e 'Infantail'/'Infantil').
    `mapping` (opcional): {chave esperada: cabeçalho real}.
    """
    return _parse_fund_sheet(
        file, header_index=1, day_key='dia', desc_key='saida_discriminada',
        kind='exit', year=year, month=month, mapping=mapping,
    )


def parse_tithers_sheet(file, year=None, mapping=None) -> Tuple[List[dict], List[str]]:
    """Lê a planilha oficial 'modelo_membros_dizimistas.xls' (matriz anual).

    Cabeçalho na linha 0: col 1 = nome do membro, col 2..13 = meses
    (Janeiro..Dezembro). Ignora cabeçalhos secundários ("Ano de...",
    "Colocar nesta coluna...", "Total de Entradas..."), linhas em branco e
    "Anônimo" vira um dizimista com is_anonymous=True.

    Retorna linhas no formato:
        {'name', 'is_anonymous', 'month', 'year', 'amount'}
    `mapping` (opcional): {chave esperada: cabeçalho real} — 'name' e meses
    ('janeiro'..'dezembro'); permite layout/ordem diferentes.
    """
    errors: List[str] = []
    rows: List[dict] = []
    try:
        df = pd.read_excel(file, header=None)
    except Exception as exc:  # noqa: BLE001
        errors.append(f'Erro ao ler planilha de dizimistas: {exc}')
        return rows, errors

    month_cols: Dict[int, int] = {}  # índice da coluna -> mês (1-12)
    name_col: Optional[int] = None

    if mapping:
        # Usa o mapeamento fornecido pela etapa de match.
        month_by_header = {
            _normalize_header(real): m_idx
            for m_idx, m_name in enumerate(MONTH_NAMES, start=1)
            if (real := mapping.get(_normalize_header(m_name)))
        }
        name_real = mapping.get('name')
        for col_idx in range(df.shape[1]):
            raw = str(df.iat[0, col_idx] or '').strip()
            norm = _normalize_header(raw)
            if name_real and _normalize_header(name_real) == norm:
                name_col = col_idx
            if norm in month_by_header:
                month_cols[col_idx] = month_by_header[norm]
        if name_col is None:
            name_col = 1
    else:
        # Comportamento legado: nome na coluna 1, meses 2..13.
        if df.shape[0] > 0:
            for col_idx in range(2, min(df.shape[1], 14)):
                raw = str(df.iat[0, col_idx] or '').strip().lower()
                norm = _normalize_header(raw)
                for m_idx, m_name in enumerate(MONTH_NAMES):
                    if _normalize_header(m_name) == norm:
                        month_cols[col_idx] = m_idx + 1
                        break
        name_col = 1

    if not month_cols:
        errors.append('Planilha de dizimistas sem colunas de meses reconhecidas.')
        return rows, errors

    for idx in range(1, df.shape[0]):
        line = idx + 1
        raw_name = str(df.iat[idx, name_col] or '')
        # Corrige encoding legado de .xls/.xlsx (ex.: 'AnÃ³nimo' -> 'Anônimo').
        if isinstance(raw_name, str):
            try:
                raw_name = raw_name.encode('latin-1').decode('utf-8')
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
            raw_name = raw_name.strip()
        if not raw_name or raw_name.lower() == 'nan':
            continue
        norm = _normalize_header(raw_name)
        if any(t in norm for t in ('ano_de', 'colocar_nesta_coluna', 'total_de_entradas', 'total_de')):
            continue

        is_anonymous = (
            re.match(r'^anonim', norm) is not None
            or 'dizimo_vem_sem_nome' in norm
        )
        name = 'Anônimo' if is_anonymous else raw_name

        for col_idx, month in month_cols.items():
            raw = df.iat[idx, col_idx]
            valor = _parse_amount(raw, line, f'mês {month}', errors)
            if valor is None or valor <= 0:
                continue
            rows.append({
                'name': name,
                'is_anonymous': is_anonymous,
                'month': month,
                'year': int(year) if year else None,
                'amount': valor,
            })
    return rows, errors


IMPORT_YEAR_RANGE = range(2000, 2101)


class ImportValidationError(Exception):
    """Planilhas com erros de validação — nada é gravado (tudo-ou-nada)."""

    def __init__(self, result: Dict):
        self.result = result
        super().__init__('Há erros de validação nas planilhas.')


def _preview_totals(entries_rows: List[Dict], exits_rows: List[Dict]):
    """Totais do balancete a partir das linhas parseadas (prévia, sem banco)."""
    total_entries = sum((r['amount'] for r in entries_rows), Decimal('0.00'))
    total_exits = sum((r['amount'] for r in exits_rows), Decimal('0.00'))
    category_entries: Dict[str, Decimal] = {}
    for r in entries_rows:
        category_entries[r['category']] = (
            category_entries.get(r['category'], Decimal('0.00')) + r['amount']
        )
    return {
        'total_entries': total_entries,
        'total_exits': total_exits,
        'balance': total_entries - total_exits,
    }, category_entries


def _import_diagnostics(
    church, year: int, month: int, files: Dict[str, object],
    preview_rows=None, record_file_errors=None,
) -> Dict:
    """Confere os modelos de saída preenchidos (Caixa IDB / Relatório Regional).

    `preview_rows=(entries_rows, exits_rows)` usa totais sintéticos das linhas
    ainda não gravadas (paridade entre a prévia e o resultado final). Sem ele,
    compara com os valores calculados a partir do banco.
    """
    totals = None
    category_totals = None
    if preview_rows is not None:
        totals, category_totals = _preview_totals(*preview_rows)

    diagnostics = {}
    caixa_file = files.get('caixa')
    if caixa_file is not None:
        try:
            parsed = parse_caixa_sheet(caixa_file)
            diagnostics['caixa'] = diagnose_caixa(
                church, year, month, parsed,
                totals=totals, category_entry_totals=category_totals,
            )
        except Exception as exc:  # noqa: BLE001
            if record_file_errors is not None:
                record_file_errors(
                    CONFERENCIA_LABELS['caixa'], 'Caixa IDB',
                    [f'Erro ao ler o arquivo: {exc}'],
                )

    relatorio_file = files.get('relatorio')
    if relatorio_file is not None:
        try:
            parsed = parse_regional_report_sheet(relatorio_file)
            diagnostics['relatorio'] = diagnose_regional(
                church, year, month, parsed,
                totals=totals, category_entry_totals=category_totals,
            )
        except Exception as exc:  # noqa: BLE001
            if record_file_errors is not None:
                record_file_errors(
                    CONFERENCIA_LABELS['relatorio'], 'Relatório Regional',
                    [f'Erro ao ler o arquivo: {exc}'],
                )
    return diagnostics


def month_data_counts(church, year, month) -> Dict:
    """Conta os lançamentos já existentes na competência (month/year).

    Usado para alertar o usuário de que, ao reimportar aquele período, os dados
    atuais serão SUBSTITUÍDOS pelos novos arquivos (delete+insert / update).
    """
    year = int(year)
    month = int(month)
    return {
        'year': year,
        'month': month,
        'entries': FinancialEntry.objects.filter(
            church=church, date__year=year, date__month=month
        ).count(),
        'exits': FinancialExit.objects.filter(
            church=church, date__year=year, date__month=month
        ).count(),
        'tithe_records': TitheRecord.objects.filter(
            tither__church=church, year=year, month=month
        ).count(),
    }


@transaction.atomic
def import_spreadsheets(
    church, files: Dict[str, object], year=None, month=None,
    dry_run: bool = False, mapping: Optional[Dict] = None,
) -> Dict:
    """Processa em lote as planilhas mensais oficiais (multipart).

    `files` pode conter: 'entries', 'exits', 'tithers' (importáveis, opcionais)
    e 'caixa'/'relatorio' (apenas conferência). `year` e `month` são
    OBRIGATÓRIOS — definem a competência dos lançamentos.

    Com `dry_run=True` apenas valida e retorna a prévia (contagens, erros e
    conferências) sem TOQUE no banco. Sem `dry_run` os lançamentos são gravados;
    porém, se houver QUALQUER erro de validação, nada é gravado e
    `ImportValidationError` é levantada (tudo-ou-nada).
    """
    if year is None or month is None:
        raise ValueError('year e month são obrigatórios.')

    year = int(year)
    month = int(month)
    if month < 1 or month > 12 or year not in IMPORT_YEAR_RANGE:
        raise ValueError('Competência inválida (mês 1..12, ano 2000..2100).')

    result = {
        'entries_imported': 0,
        'exits_imported': 0,
        'tithers_imported': 0,
        'tithe_records_imported': 0,
        'diagnostics': {},
        'errors': [],
        'errors_by_file': {},
        'dry_run': bool(dry_run),
    }

    # Contagens atuais da competência, para alertar sobre a substituição.
    existing = month_data_counts(church, year, month)
    result['existing_entries'] = existing['entries']
    result['existing_exits'] = existing['exits']
    result['existing_tithe_records'] = existing['tithe_records']

    def record_file_errors(label: str, tag: str, errs: List[str]) -> None:
        """Agrupa os erros de uma planilha e mantém a lista plana legada."""
        result['errors_by_file'][label] = errs
        result['errors'].extend(f'[{tag}] {e}' for e in errs)

    entries_rows: List[Dict] = []
    exits_rows: List[Dict] = []
    tither_rows: List[Dict] = []
    has_errors = False

    entries_file = files.get('entries')
    if entries_file is not None:
        entries_rows, errors = parse_entries_sheet(
            entries_file, year, month,
            (mapping or {}).get('entries'),
        )
        record_file_errors(IMPORT_FILE_LABELS['entries'], 'Entradas', errors)
        has_errors = has_errors or bool(errors)

    exits_file = files.get('exits')
    if exits_file is not None:
        exits_rows, errors = parse_exits_sheet(
            exits_file, year, month,
            (mapping or {}).get('exits'),
        )
        record_file_errors(IMPORT_FILE_LABELS['exits'], 'Saídas', errors)
        has_errors = has_errors or bool(errors)

    tithers_file = files.get('tithers')
    if tithers_file is not None:
        tither_rows, errors = parse_tithers_sheet(
            tithers_file, year, (mapping or {}).get('tithers'),
        )
        record_file_errors(IMPORT_FILE_LABELS['tithers'], 'Dizimistas', errors)
        has_errors = has_errors or bool(errors)

    tithers_imported = len({r['name'] for r in tither_rows})
    tithe_records_imported = sum(1 for r in tither_rows if r.get('year'))

    if dry_run:
        result['entries_imported'] = len(entries_rows)
        result['exits_imported'] = len(exits_rows)
        result['tithers_imported'] = tithers_imported
        result['tithe_records_imported'] = tithe_records_imported
        result['diagnostics'] = _import_diagnostics(
            church, year, month, files,
            preview_rows=(entries_rows, exits_rows),
            record_file_errors=record_file_errors,
        )
        return result

    if has_errors:
        result['entries_imported'] = len(entries_rows)
        result['exits_imported'] = len(exits_rows)
        result['tithers_imported'] = tithers_imported
        result['tithe_records_imported'] = tithe_records_imported
        raise ImportValidationError(result)

    # ------------------------------------------------------------------ #
    # Commit: gravação efetiva (só chega aqui sem erros).
    # ------------------------------------------------------------------ #
    if entries_file is not None:
        # Substituição segura por competência: remove lançamentos prévios na
        # mesma competência antes de persistir os novos (idempotência).
        FinancialEntry.objects.filter(
            church=church, date__year=year, date__month=month
        ).delete()
        bulk = [
            FinancialEntry(
                church=church, date=r['date'],
                service_description=r['service_description'],
                category=r['category'], amount=r['amount'],
            )
            for r in entries_rows
        ]
        FinancialEntry.objects.bulk_create(bulk)
        result['entries_imported'] = len(bulk)

    if exits_file is not None:
        FinancialExit.objects.filter(
            church=church, date__year=year, date__month=month
        ).delete()
        bulk = [
            FinancialExit(
                church=church, date=r['date'], description=r['description'],
                category=r['category'], amount=r['amount'],
            )
            for r in exits_rows
        ]
        FinancialExit.objects.bulk_create(bulk)
        result['exits_imported'] = len(bulk)

    if tithers_file is not None:
        tither_cache: Dict[str, Tither] = {}
        for r in tither_rows:
            name = r['name']
            if name not in tither_cache:
                tither, _ = Tither.objects.update_or_create(
                    church=church, name=name,
                    defaults={'is_anonymous': r.get('is_anonymous', False)},
                )
                tither_cache[name] = tither
            if r.get('year'):
                TitheRecord.objects.update_or_create(
                    tither=tither_cache[name],
                    year=r['year'],
                    month=r['month'],
                    defaults={'amount': r['amount']},
                )
                result['tithe_records_imported'] += 1
        result['tithers_imported'] = len(tither_cache)

    # Conferência (diagnóstico) pós-gravação, contra o banco.
    result['diagnostics'] = _import_diagnostics(
        church, year, month, files,
        preview_rows=None,
        record_file_errors=record_file_errors,
    )

    return result


# --------------------------------------------------------------------------- #
# 6. Preenchimento dos modelos oficiais (.xls) — Caixa IDB e Relatório Regional
# --------------------------------------------------------------------------- #
def _copy_xls_template(filename: str):
    """Copia um modelo .xls preservando formatação/seleção via xlutils.copy.

    Retorna (cópia_write, planilha_origem). Nota: imagens/logos embutidos não
    são preservados por xlutils; a estrutura, mesclagens e formatação sim.
    """
    from xlrd import open_workbook
    from xlutils.copy import copy

    path = TEMPLATES_DIR / filename
    read_book = open_workbook(str(path), formatting_info=True)
    write_book = copy(read_book)
    sheet = read_book.sheet_by_index(0)
    return write_book, sheet


def _get_previous_balance(church, year: int, month: int) -> Decimal:
    """Saldo final do último fechamento cuja competência é anterior ao mês."""
    prev = MonthlyClosing.objects.filter(
        church=church, year__lt=year,
    ).order_by('-year', '-month').first()
    if prev is None:
        prev = MonthlyClosing.objects.filter(
            church=church, year=year, month__lt=month,
        ).order_by('-month').first()
    return prev.final_balance if prev else Decimal('0.00')


def generate_filled_caixa(church, year: int, month: int) -> bytes:
    """Preenche o modelo 'modelo_caixa.xls' (Balanço Local Consolidado).

    Distribui por fundo (Dízimos, Ofertas, Construção, Missões e departamentos)
    o saldo anterior, entradas, saídas e saldo do mês, e devolve os bytes .xls.
    """
    from xlrd import xldate

    write_book, sheet = _copy_xls_template('modelo_caixa.xls')
    ws = write_book.get_sheet(0)

    # Totais por fundo a partir das entradas/saídas do mês.
    fund_map = {
        'DIZIMOS': DepartmentCategory.DIZIMO,
        'OFERTAS': DepartmentCategory.OFERTA,
        'CONSTRUCAO': DepartmentCategory.CONSTRUCAO,
        'MISSOES': DepartmentCategory.MISSOES,
        'ESPECIAIS': DepartmentCategory.ESPECIAL,
    }

    def sum_cat(category, model):
        total = model.objects.filter(
            church=church, date__year=year, date__month=month,
            category=category,
        ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
        return total

    entries_by_fund = {
        key: sum_cat(cat, FinancialEntry) for key, cat in fund_map.items()
    }
    exits_by_fund = {
        key: sum_cat(cat, FinancialExit) for key, cat in fund_map.items()
    }

    total_entries = sum(entries_by_fund.values(), Decimal('0.00'))
    total_exits = sum(exits_by_fund.values(), Decimal('0.00'))

    # Saldo anterior = saldo final do último fechamento anterior ao mês.
    previous_balance = _get_previous_balance(church, year, month)

    current_balance = previous_balance + total_entries - total_exits

    # Preenche as células de totais do balanço.
    # Linha base do bloco "Item | Discriminação | S.Anterior | Entradas | Saídas | Saldo".
    def write_balance(row, prev_val, ent_val, sai_val):
        if row >= sheet.nrows:
            return
        ws.write(row, 2, float(prev_val) if prev_val else 0.0)  # S.Anterior
        ws.write(row, 3, float(ent_val) if ent_val else 0.0)    # Entradas
        ws.write(row, 4, float(sai_val) if sai_val else 0.0)    # Saídas
        saldo = prev_val + ent_val - sai_val
        ws.write(row, 5, float(saldo))                          # Saldo

    # Fundo de Dízimos (1.00) e Ofertas (2.00) no cabeçalho dos blocos.
    for r in range(sheet.nrows):
        val = sheet.cell_value(r, 0)
        disc = str(sheet.cell_value(r, 1) or '')
        disc_norm = _normalize_header(disc)
        if 'soma' in disc_norm and r + 1 < sheet.nrows:
            write_balance(r, previous_balance, total_entries, total_exits)
        elif 'dizimos' in disc_norm:
            write_balance(r, Decimal('0.00'), entries_by_fund['DIZIMOS'], exits_by_fund['DIZIMOS'])
        elif 'ofertas' in disc_norm:
            write_balance(r, Decimal('0.00'), entries_by_fund['OFERTAS'], exits_by_fund['OFERTAS'])
        elif 'constru' in disc_norm:
            write_balance(r, Decimal('0.00'), entries_by_fund['CONSTRUCAO'], exits_by_fund['CONSTRUCAO'])
        elif 'missoes' in disc_norm:
            write_balance(r, Decimal('0.00'), entries_by_fund['MISSOES'], exits_by_fund['MISSOES'])
        elif isinstance(val, float) and val == 0 and disc_norm == '':
            continue

    out = BytesIO()
    write_book.save(out)
    return out.getvalue()


def generate_filled_regional_report(church, year: int, month: int) -> bytes:
    """Preenche o modelo 'modelo_relatorio.xls' (Relatório Regional Paraíba).

    Preenche a identificação (mês/ano/cidade), o bloco Movimento Financeiro e
    o bloco Remessa Financeira (15% dos dízimos + 10% das ofertas por
    departamento), devolvendo os bytes .xls preenchidos.
    """
    write_book, sheet = _copy_xls_template('modelo_relatorio.xls')
    ws = write_book.get_sheet(0)

    month_name = MONTH_NAMES[month - 1]

    # Identificação
    for r in range(sheet.nrows):
        c0 = str(sheet.cell_value(r, 0) or '')
        c6 = str(sheet.cell_value(r, 6) or '')
        if 'mes' in _normalize_header(c6) and 'ano' in _normalize_header(c6):
            ws.write(r, 6, f'MÊS:  {month_name.upper()}  -  ANO: {year}')
            ws.write(r, 7, '')
        elif 'cidade' in _normalize_header(c6):
            ws.write(r, 6, f'CIDADE: {church.city or ""} / {church.state or ""}')
            ws.write(r, 7, '')

    remittance = calc_regional_remittance(church, year, month)
    dizimos = remittance['dizimos']
    offers = remittance['ofertas_departamentos']

    # Totais de departamentos para o bloco 10% (na ordem col -> categoria)
    dep_rows = {
        'ESCOLA BIBLICA': DepartmentCategory.ESC_BIBLICA,
        'MINISTERIO DA MULHER': DepartmentCategory.MULHERES,
        'MINISTERIO MASCULINO': DepartmentCategory.HOMENS,
        'MINISTERIO JUVENIL': DepartmentCategory.JOVENS,
        'DEPTO INFANTIL': DepartmentCategory.INFANTIL,
        'ADOLES': DepartmentCategory.ADOLESCENTES,
        'MISSOES': DepartmentCategory.MISSOES,
    }

    def find_row(label_substr, col):
        for r in range(sheet.nrows):
            cell = str(sheet.cell_value(r, col) or '').lower()
            norm = _normalize_header(cell)
            if label_substr in norm:
                return r
        return None

    # Movimento geral: DÍZIMOS DIVERSOS <- total de dízimos.
    r_diz = find_row('DIZIMOS DIVERSOS', 0)
    total_dizimos = dizimos['total_dizimos']
    prev_balance = _get_previous_balance(church, year, month)
    if r_diz is not None:
        ws.write(r_diz, 1, float(prev_balance))                 # S.Anterior
        ws.write(r_diz, 2, float(total_dizimos))                # Entradas
        ws.write(r_diz, 3, float(total_dizimos))                # Total

    # Remessa financeira — 15% dos dízimos (linha 'DIZ. DOS DIZ. P/ REGIÃO 15%').
    r_rem = None
    for r in range(sheet.nrows):
        c12 = str(sheet.cell_value(r, 11) or '')
        if '15%' in _normalize_header(c12) or 'diz. dos diz' in _normalize_header(c12):
            r_rem = r
            break
    if r_rem is not None:
        ws.write(r_rem, 11, float(dizimos['total_remessa_dizimos']))
        ws.write(r_rem, 13, float(dizimos['total_remessa_dizimos']))

    # 10% das ofertas por departamento.
    for label, cat in dep_rows.items():
        r = find_row(label, 11)
        if r is None:
            continue
        total = offers.get(cat, {}).get('total', Decimal('0.00'))
        rem = offers.get(cat, {}).get('remessa', Decimal('0.00'))
        ws.write(r, 11, float(rem))
        ws.write(r, 13, total)

    # TOTAL ENVIADO PARA A REGIÃO
    r_total = find_row('TOTAL ENVIADO', 11)
    if r_total is not None:
        ws.write(r_total, 11, float(remittance['total_remittance']))
        ws.write(r_total, 13, float(remittance['total_remittance']))

    out = BytesIO()
    write_book.save(out)
    return out.getvalue()


# Mapeamento de linha (célula C) do Relatório Nacional -> categoria de entrada.
_NATIONAL_ENTRY_ROWS = {
    8: DepartmentCategory.DIZIMO,
    9: DepartmentCategory.OFERTA,
    10: DepartmentCategory.VISAO_CORPORATIVA,
    11: DepartmentCategory.ESPECIAL,
    12: DepartmentCategory.CONSTRUCAO,
    13: DepartmentCategory.ESPECIAL,
    16: DepartmentCategory.ESC_BIBLICA,
    17: DepartmentCategory.MULHERES,
    18: DepartmentCategory.HOMENS,
    19: DepartmentCategory.JOVENS,
    20: DepartmentCategory.INFANTIL,
    21: DepartmentCategory.CASAIS,
    22: DepartmentCategory.MISSOES,
}


def generate_filled_national_report(church, year: int, month: int) -> bytes:
    """Preenche o modelo oficial 'relatorio-nacional.xlsx' (openpyxl).

    Preenche a identificação (G3/G4), as entradas do mês (coluna C) e as
    despesas do mês (coluna J). As fórmulas nativas da planilha
    (=C8*15%, =C16*10%, =SUM(...)) calculam balancete e remessa no Excel.
    """
    import openpyxl

    template_path = TEMPLATES_DIR / 'relatorio-nacional.xlsx'
    wb = openpyxl.load_workbook(template_path, data_only=False)
    ws = wb['REL. JAN']

    month_name = MONTH_NAMES[month - 1]

    # Identificação
    ws['G3'] = f"MÊS: {month_name.upper()} - ANO: {year} - IDB: {church.name}"
    ws['G4'] = (
        f"CIDADE: {church.city.upper()}              "
        f"ESTADO DO: {church.state.upper()}"
    )

    def sum_entries(category) -> Decimal:
        return FinancialEntry.objects.filter(
            church=church, date__year=year, date__month=month,
            category=category,
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    def sum_exits(category) -> Decimal:
        return FinancialExit.objects.filter(
            church=church, date__year=year, date__month=month,
            category=category,
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    # Entradas (coluna C) — uma linha por categoria.
    for row, category in _NATIONAL_ENTRY_ROWS.items():
        ws.cell(row=row, column=3).value = float(sum_entries(category))

    # Despesas (coluna J) — mesma categoria da linha.
    for row, category in _NATIONAL_ENTRY_ROWS.items():
        ws.cell(row=row, column=10).value = float(sum_exits(category))

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# 7. Leitura de conferência (diagnóstico) do Caixa e Relatório preenchidos
# --------------------------------------------------------------------------- #
def _to_decimal(value) -> Optional[Decimal]:
    """Normaliza um valor de célula (.xls) para Decimal, ou None se vazio."""
    if value is None:
        return None
    if isinstance(value, float):
        if pd.isna(value):
            return None
        return Decimal(str(value))
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    text = str(value).strip().replace('R$', '').replace(' ', '')
    if not text:
        return None
    text = text.replace('.', '').replace(',', '.')
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _num(value, default=Decimal('0.00')):
    val = _to_decimal(value)
    return val if val is not None else default


# Mapeia o cabeçalho de seção do Caixa IDB para a categoria contábil.
CAIXA_SECTION_MAP = {
    'fundo_de_dizimos': DepartmentCategory.DIZIMO,
    'ofertas_dos_cultos': DepartmentCategory.OFERTA,
    'fundo_de_missoes': DepartmentCategory.MISSOES,
    'construcao': DepartmentCategory.CONSTRUCAO,
    'ministerio_masculino': DepartmentCategory.HOMENS,
    'escola_biblica': DepartmentCategory.ESC_BIBLICA,
    'ministerio_feminino': DepartmentCategory.MULHERES,
    'ministerio_juvenil': DepartmentCategory.JOVENS,
    'dep_infantil': DepartmentCategory.INFANTIL,
    'dep._infantil': DepartmentCategory.INFANTIL,
    'entradas_especiais': DepartmentCategory.ESPECIAL,
    'visao_corporativa': DepartmentCategory.VISAO_CORPORATIVA,
    'ministerio_de_casais': DepartmentCategory.CASAIS,
    'depto_casais': DepartmentCategory.CASAIS,
}


def parse_caixa_sheet(file) -> Dict:
    """Lê o Caixa IDB preenchido e retorna totais por seção + Total Geral.

    Cada seção possui cabeçalho (Item/Discriminação/S.Anterior/Entradas/Saídas/
    Saldo), itens 'X.XX' e uma linha 'Soma'. A última linha é 'Total Geral'.
    Retorna:
        {
            'sections': [{'label', 'category'|None, 'previous', 'entries',
                          'exits', 'balance'}],
            'total': {'previous', 'entries', 'exits', 'balance'},
        }
    """
    from xlrd import open_workbook

    book = open_workbook(file_contents=file.read())
    sheet = book.sheet_by_index(0)

    sections = []
    current = None

    def fresh(row_prev, row_ent, row_exi, row_bal):
        return {
            'previous': _num(row_prev),
            'entries': _num(row_ent),
            'exits': _num(row_exi),
            'balance': _num(row_bal),
        }

    total = fresh(0, 0, 0, 0)

    for r in range(sheet.nrows):
        c0 = str(sheet.cell_value(r, 0) or '').strip()
        c1 = str(sheet.cell_value(r, 1) or '').strip()
        norm = _normalize_header(c1)
        if norm in CAIXA_SECTION_MAP:
            # Cabeçalho de seção (ex.: col0='1.00', col1='Fundo de Dízimos').
            current = {
                'label': c1,
                'category': CAIXA_SECTION_MAP[norm],
                'previous': Decimal('0.00'),
                'entries': Decimal('0.00'),
                'exits': Decimal('0.00'),
                'balance': Decimal('0.00'),
            }
            sections.append(current)
        elif _normalize_header(c0) == 'soma' and current is not None:
            current['previous'] = _num(sheet.cell_value(r, 2))
            current['entries'] = _num(sheet.cell_value(r, 3))
            current['exits'] = _num(sheet.cell_value(r, 4))
            current['balance'] = _num(sheet.cell_value(r, 5))
        elif _normalize_header(c0) == 'total_geral':
            total = fresh(sheet.cell_value(r, 2), sheet.cell_value(r, 3),
                          sheet.cell_value(r, 4), sheet.cell_value(r, 5))

    return {'sections': sections, 'total': total}


def parse_regional_report_sheet(file) -> Dict:
    """Lê o Relatório Regional preenchido (Movimento + Remessa Financeira).

    Retorna:
        {
            'movement': {'sub_total_entries', 'total_general_entries',
                         'total_general_balance'},
            'remittance': {'rows': {...}, 'total'},
        }
    """
    from xlrd import open_workbook

    book = open_workbook(file_contents=file.read())
    sheet = book.sheet_by_index(0)

    def numeric_cell(row, cols):
        """Retorna o primeiro valor numérico dentre as colunas dadas."""
        for col in cols:
            raw = sheet.cell_value(row, col)
            if isinstance(raw, (float, int)) and not (isinstance(raw, float) and pd.isna(raw)):
                return Decimal(str(raw))
        return Decimal('0.00')

    remittance_rows = {}
    total_remittance = None
    total_general_entries = None
    total_general_balance = None
    sub_total_entries = None

    for r in range(sheet.nrows):
        c0 = str(sheet.cell_value(r, 0) or '').strip()
        c11 = str(sheet.cell_value(r, 11) or '').strip()

        norm0 = _normalize_header(c0)
        norm11 = _normalize_header(c11)

        # Remessa financeira (coluna 11)
        if norm11 == 'total_enviado_para_regiao':
            total_remittance = numeric_cell(r, [11, 13, 14])
        elif '10%_oferta' in norm11:
            label = c11.strip()
            remittance_rows[label] = {
                'value': numeric_cell(r, [11, 13]),
                'total': numeric_cell(r, [13, 11]),
            }

        # Movimento financeiro (coluna 0)
        if norm0 == 'total_geral':
            total_general_entries = numeric_cell(r, [2, 3])
            total_general_balance = numeric_cell(r, [1, 10])
        elif norm0 == 'sub_total':
            sub_total_entries = numeric_cell(r, [2, 3])

    return {
        'movement': {
            'sub_total_entries': sub_total_entries,
            'total_general_entries': total_general_entries,
            'total_general_balance': total_general_balance,
        },
        'remittance': {
            'rows': remittance_rows,
            'total': total_remittance,
        },
    }


def _diff_item(label, provided, expected):
    provided = _num(provided)
    expected = _num(expected)
    diff = provided - expected
    ok = abs(diff) < Decimal('0.01')
    return {
        'label': label,
        'provided': provided,
        'expected': expected,
        'difference': diff,
        'status': 'OK' if ok else 'DIVERGENTE',
    }


def diagnose_caixa(
    church, year, month, parsed,
    totals: Dict = None, category_entry_totals: Dict[str, Decimal] = None,
) -> List[Dict]:
    """Compara o Caixa IDB preenchido com os valores calculados pelo banco.

    Na prévia de importação, `totals` e `category_entry_totals` vêm das linhas
    parseadas (ainda não gravadas), garantindo paridade com o resultado final.
    """
    balance = totals if totals is not None else build_monthly_balance(church, year, month)
    diagnostics = []

    diagnostics.append(_diff_item(
        'Total Geral (Entradas)', parsed['total']['entries'],
        balance['total_entries'],
    ))
    diagnostics.append(_diff_item(
        'Total Geral (Saídas)', parsed['total']['exits'],
        balance['total_exits'],
    ))
    diagnostics.append(_diff_item(
        'Total Geral (Saldo)', parsed['total']['balance'],
        balance['balance'],
    ))

    for section in parsed['sections']:
        cat = section.get('category')
        if cat is None:
            continue
        if category_entry_totals is not None:
            expected_entries = category_entry_totals.get(cat, Decimal('0.00'))
        else:
            expected_entries = _sum_category(church, year, month, cat)
        diagnostics.append(_diff_item(
            f"Seção {section['label']} (Entradas)", section['entries'],
            expected_entries,
        ))
    return diagnostics


def diagnose_regional(
    church, year, month, parsed,
    totals: Dict = None, category_entry_totals: Dict[str, Decimal] = None,
) -> List[Dict]:
    """Compara o Relatório Regional preenchido com o cálculo oficial do banco."""
    remittance = calc_regional_remittance(
        church, year, month, category_totals=category_entry_totals,
    )
    balance = totals if totals is not None else build_monthly_balance(church, year, month)
    diagnostics = []

    diagnostics.append(_diff_item(
        'Movimento - TOTAL GERAL (Entradas)',
        parsed['movement']['total_general_entries'],
        balance['total_entries'],
    ))
    diagnostics.append(_diff_item(
        'Remessa - TOTAL ENVIADO PARA REGIÃO',
        parsed['remittance']['total'],
        remittance['total_remittance'],
    ))
    return diagnostics


# --------------------------------------------------------------------------- #
# 7. Premissas contábeis IDB — natureza das despesas, DRE por natureza,
#    repetição de dizimistas (~90%) e validação Tesouraria/Liderança.
# --------------------------------------------------------------------------- #
import unicodedata  # noqa: E402

NATURE_LABELS = {
    'FIXA_VARIAVEL': 'Despesas Fixas Variáveis',
    'FIXA_DEFINIDA': 'Despesas Fixas Definidas',
    'PREBENDA': 'Prebenda Pastoral',
    'OUTRAS': 'Outras Despesas',
}

# Tolerância (R$) para a divergência da prebenda e limiar de repetição de
# dizimistas (%).
PREBENDA_TOLERANCE = Decimal('1.00')
TITHER_REPEAT_THRESHOLD = Decimal('90.00')

# Classificação automática por palavras-chave (texto normalizado: minúsculo e
# sem acentos). A ordem importa: prebenda primeiro.
NATURE_KEYWORDS = {
    'PREBENDA': ['prebenda', 'pastor', 'pastoral'],
    'FIXA_VARIAVEL': [
        'agua', 'energia', 'energisa', 'saesa', 'eletrica', 'eletricidade',
        'luz', 'internet', 'fibra', 'wifi',
    ],
    'FIXA_DEFINIDA': [
        'zeladoria', 'secretaria', 'missao', 'missionaria', 'missionario',
        'ajuda de custo',
    ],
}


def _normalize_text(value: str) -> str:
    """Minúsculas, sem acentos (usado na classificação por palavras-chave)."""
    text = unicodedata.normalize('NFD', (value or '').lower())
    return ''.join(c for c in text if unicodedata.category(c) != 'Mn')


def classify_expense_nature(description: str) -> str:
    """Classifica a natureza de uma despesa pela descrição lançada."""
    norm = _normalize_text(description)
    for nature, keywords in NATURE_KEYWORDS.items():
        if any(kw in norm for kw in keywords):
            return nature
    return 'OUTRAS'


def _expenses_by_nature(
    church=None, year: int = None, month: int = None,
    exits_rows: Optional[List[Dict]] = None,
) -> List[Dict]:
    """Despesas agrupadas por natureza (Fixas Variáveis / Definidas / Prebenda)."""
    nature_totals: Dict[str, Decimal] = {n: Decimal('0.00') for n in NATURE_LABELS}
    if exits_rows is not None:
        for r in exits_rows:
            nature_totals[classify_expense_nature(r['description'])] += Decimal(r['amount'])
    else:
        qs = FinancialExit.objects.filter(church=church, date__year=year)
        if month:
            qs = qs.filter(date__month=month)
        for description, amount in qs.values_list('description', 'amount'):
            nature_totals[classify_expense_nature(description)] += Decimal(amount)
    return [
        {'nature': n, 'label': NATURE_LABELS[n], 'value': str(v)}
        for n, v in nature_totals.items()
    ]


def build_dre_summary(
    church, year: int, month: int = None,
    entries_rows: Optional[List[Dict]] = None,
    exits_rows: Optional[List[Dict]] = None,
) -> Dict:
    """DRE mensal/anual: receitas por departamento e despesas por natureza.

    Aceita `entries_rows`/`exits_rows` (linhas parseadas) para prévia de
    importação; caso contrário, calcula a partir do banco.
    """
    if entries_rows is not None:
        total_revenue = sum((Decimal(r['amount']) for r in entries_rows), Decimal('0.00'))
        rev_by_cat: Dict[str, Decimal] = {}
        for r in entries_rows:
            rev_by_cat[r['category']] = (
                rev_by_cat.get(r['category'], Decimal('0.00')) + Decimal(r['amount'])
            )
    else:
        qs = FinancialEntry.objects.filter(church=church, date__year=year)
        if month:
            qs = qs.filter(date__month=month)
        total_revenue = Decimal('0.00')
        rev_by_cat = {}
        for category, amount in qs.values_list('category', 'amount'):
            amount = Decimal(amount)
            total_revenue += amount
            rev_by_cat[category] = rev_by_cat.get(category, Decimal('0.00')) + amount

    label_display = dict(DepartmentCategory.choices)
    revenue_by_category = [
        {
            'category': cat,
            'label': label_display.get(cat, cat),
            'value': str(value),
        }
        for cat, value in sorted(
            rev_by_cat.items(), key=lambda kv: kv[1], reverse=True,
        )
    ]

    expenses_by_nature = _expenses_by_nature(
        church, year, month, exits_rows=exits_rows,
    )
    total_expenses = sum(
        (Decimal(item['value']) for item in expenses_by_nature), Decimal('0.00'),
    )

    return {
        'year': year,
        'month': month,
        'total_revenue': str(total_revenue),
        'total_expenses': str(total_expenses),
        'net_result': str(total_revenue - total_expenses),
        'revenue_by_category': revenue_by_category,
        'expenses_by_nature': expenses_by_nature,
    }


def audit_tither_repeat(church, year: int, month: int) -> Dict:
    """Auditoria de repetição de dizimistas entre meses (~90%).

    A base é o mês imediatamente anterior; repetidos são os que também
    dízimaram (valor > 0) no mês atual.
    """
    prev_month = month - 1
    prev_year = year
    if prev_month == 0:
        prev_month = 12
        prev_year = year - 1

    def _tither_ids(y: int, m: int) -> set:
        return set(
            TitheRecord.objects.filter(
                tither__church=church, year=y, month=m, amount__gt=0,
            ).values_list('tither_id', flat=True)
        )

    base = _tither_ids(prev_year, prev_month)
    current = _tither_ids(year, month)
    repeated = base & current

    if base:
        percent = (
            Decimal(len(repeated)) * Decimal('100') / Decimal(len(base))
        ).quantize(Decimal('0.01'))
        missing_ids = base - current
        missing = [
            {'id': t.id, 'name': str(t)}
            for t in Tither.objects.filter(id__in=missing_ids).order_by('name')
        ]
    else:
        percent = None
        missing = []

    return {
        'year': year,
        'month': month,
        'base_year': prev_year,
        'base_month': prev_month,
        'base_count': len(base),
        'current_count': len(current),
        'repeated_count': len(repeated),
        'repeat_percent': str(percent) if percent is not None else None,
        'threshold': str(TITHER_REPEAT_THRESHOLD),
        'ok': percent is not None and percent >= TITHER_REPEAT_THRESHOLD,
        'missing_members': missing,
    }


def prebenda_check(church, year: int, month: int) -> Dict:
    """Compara a prebenda registrada com o percentual configurado da igreja."""
    balance = build_monthly_balance(church, year, month)
    percent = church.pastoral_prebenda_percent or Decimal('10.00')
    expected = (balance['total_entries'] * percent / Decimal('100')).quantize(Decimal('0.01'))

    recorded = Decimal('0.00')
    exits_qs = FinancialExit.objects.filter(
        church=church, date__year=year, date__month=month,
    )
    for description, amount in exits_qs.values_list('description', 'amount'):
        if classify_expense_nature(description) == 'PREBENDA':
            recorded += Decimal(amount)

    difference = (expected - recorded).quantize(Decimal('0.01'))
    return {
        'percent': str(percent),
        'expected': str(expected),
        'recorded': str(recorded),
        'difference': str(difference),
        'tolerance': str(PREBENDA_TOLERANCE),
        'ok': abs(difference) <= PREBENDA_TOLERANCE,
    }


def build_validation_checks(church, year: int, month: int) -> Dict:
    """Checklist mensal da rotina contábil IDB (Tesouraria/Liderança)."""
    closing = get_or_create_monthly_closing(church, year, month)[0]
    return {
        'closing': {
            'is_closed': closing.is_closed,
            'previous_balance': str(closing.previous_balance),
            'total_entries': str(closing.total_entries),
            'total_exits': str(closing.total_exits),
            'final_balance': str(closing.final_balance),
        },
        'prebenda': prebenda_check(church, year, month),
        'tither_repeat': audit_tither_repeat(church, year, month),
        'nature_summary': _expenses_by_nature(church, year, month),
    }


# --------------------------------------------------------------------------- #
# 8. Export de planilhas preenchidas no modelo (Entradas/Saídas) e Fechamento
# --------------------------------------------------------------------------- #

# Colunas nativas (1-based) dos modelos de entradas/saídas. As demais
# categorias (Visão Corporativa, Adolescentes, Casais) são escritas em
# colunas extras anexadas a partir da coluna M, com cabeçalho reimportável.
_MODEL_FUND_COLUMNS = {
    DepartmentCategory.DIZIMO: 3,          # C
    DepartmentCategory.OFERTA: 4,          # D
    DepartmentCategory.CONSTRUCAO: 5,      # E
    DepartmentCategory.ESPECIAL: 6,        # F
    DepartmentCategory.MISSOES: 7,         # G
    DepartmentCategory.MULHERES: 8,        # H
    DepartmentCategory.HOMENS: 9,          # I
    DepartmentCategory.JOVENS: 10,         # J
    DepartmentCategory.ESC_BIBLICA: 11,    # K
    DepartmentCategory.INFANTIL: 12,       # L
}

# Categorias sem coluna nativa -> são exportadas em colunas extras (1-based,
# em sequência a partir de L) com cabeçalho reconhecível pelo importador.
_MODEL_EXTRA_COLUMNS = [
    (DepartmentCategory.VISAO_CORPORATIVA, 'Visão Corporativa'),
    (DepartmentCategory.ADOLESCENTES, 'Adolescentes'),
    (DepartmentCategory.CASAIS, 'Casais'),
]

# Linha onde começam os dados (0 a linha de cabeçalho é 2).
_MODEL_DATA_START = {
    'entries': 5,
    'exits': 3,
}


def _write_model_sheet(church, year: int, month: int, kind: str) -> bytes:
    """Preenche o modelo de Entradas ou Saídas com os lançamentos do mês.

    Agrupa os lançamentos por (dia, descrição), somando os valores por
    categoria. Escreve uma linha por grupo, colocando cada categoria na sua
    coluna de fundo (nativa ou extra). Retorna os bytes .xlsx.
    """
    import openpyxl

    filename = 'modelo_entradas.xlsx' if kind == 'entries' else 'modelo_saidas.xlsx'
    is_entry = kind == 'entries'
    model = FinancialEntry if is_entry else FinancialExit
    desc_field = 'service_description' if is_entry else 'description'
    data_start = _MODEL_DATA_START[kind]

    records = model.objects.filter(
        church=church, date__year=year, date__month=month,
    ).order_by('date', 'id')

    # Agregação por (dia, descrição) somando por categoria.
    groups: Dict[Tuple[int, str], Dict[str, Decimal]] = {}
    label_holder: Dict[Tuple[int, str], str] = {}
    for rec in records:
        key = (rec.date.day, str(getattr(rec, desc_field) or '').strip())
        per_cat = groups.setdefault(key, {})
        label_holder.setdefault(key, getattr(rec, desc_field))
        per_cat[rec.category] = per_cat.get(rec.category, Decimal('0.00')) + rec.amount

    template_path = TEMPLATES_DIR / filename
    wb = openpyxl.load_workbook(template_path)
    ws = wb.active

    # Cabeçalho das colunas extras (reimportável: o importador normaliza os
    # cabeçalhos e mapeia 'visao', 'adolescentes', 'casais' via FUND_COLUMN_MAP).
    extra_col = 13  # coluna M
    for extra_col_idx, (category, header) in enumerate(_MODEL_EXTRA_COLUMNS):
        ws.cell(row=2, column=extra_col + extra_col_idx).value = header

    sorted_keys = sorted(groups.keys(), key=lambda k: (k[0], label_holder[k]))
    for i, key in enumerate(sorted_keys):
        row = data_start + i
        dia, _desc = key
        ws.cell(row=row, column=1).value = dia
        ws.cell(row=row, column=2).value = label_holder[key]
        per_cat = groups[key]
        for category, amount in per_cat.items():
            col = _MODEL_FUND_COLUMNS.get(category)
            if col is None:
                # Localiza a coluna extra correspondente (1-based).
                for extra_col_idx, (extra_cat, _h) in enumerate(_MODEL_EXTRA_COLUMNS):
                    if extra_cat == category:
                        col = 13 + extra_col_idx
                        break
            if col is not None:
                ws.cell(row=row, column=col).value = float(amount)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def generate_filled_entries(church, year: int, month: int) -> bytes:
    """Preenche 'modelo_entradas.xlsx' com as entradas do mês (agrupadas)."""
    return _write_model_sheet(church, year, month, 'entries')


def generate_filled_exits(church, year: int, month: int) -> bytes:
    """Preenche 'modelo_saidas.xlsx' com as saídas do mês (agrupadas)."""
    return _write_model_sheet(church, year, month, 'exits')


def _closing_fund_totals(church, year: int, month: int):
    """Totais mensais de entradas/saídas por categoria (para o export)."""
    cats = [c for c in DepartmentCategory if True]  # todas as categorias
    totals = []
    for cat in cats:
        entries = FinancialEntry.objects.filter(
            church=church, date__year=year, date__month=month,
            category=cat,
        ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
        exits = FinancialExit.objects.filter(
            church=church, date__year=year, date__month=month,
            category=cat,
        ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
        totals.append({
            'category': cat,
            'label': cat.label,
            'entries': entries,
            'exits': exits,
        })
    return totals


def generate_closings_export(church, year: int, mode: str, month: int = None) -> bytes:
    """Export customizado do Fechamento Mensal (.xlsx via openpyxl).

    mode='annual' -> uma coluna/linha por mês (12 meses) com saldos e status.
    mode='period' -> detalhe da competência (month) com entradas/saídas por fundo.
    """
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Fechamento'
    header_fill = PatternFill('solid', fgColor='1971C2')
    header_font = Font(color='FFFFFF', bold=True)

    if mode == 'period':
        if month is None or not (1 <= month <= 12):
            raise ValueError('month é obrigatório para mode=period')
        closing, _ = get_or_create_monthly_closing(church, year, month)

        ws['A1'] = f'Fechamento Mensal - {MONTH_NAMES[month - 1]}/{year} - {church.name}'
        ws['A1'].font = Font(bold=True, size=14)
        rows = [
            ('Saldo Anterior', closing.previous_balance),
            ('Total de Entradas', closing.total_entries),
            ('Total de Saídas', closing.total_exits),
            ('Saldo Final', closing.final_balance),
            ('Status', 'Fechado' if closing.is_closed else 'Aberto'),
        ]
        for i, (label, value) in enumerate(rows, start=3):
            ws.cell(row=i, column=1, value=label).font = Font(bold=True)
            ws.cell(row=i, column=2, value=value)

        ws.cell(row=10, column=1, value='Categoria').font = header_font
        ws.cell(row=10, column=1).fill = header_fill
        ws.cell(row=10, column=2, value='Entradas').font = header_font
        ws.cell(row=10, column=2).fill = header_fill
        ws.cell(row=10, column=3, value='Saídas').font = header_font
        ws.cell(row=10, column=3).fill = header_fill
        row = 11
        for item in _closing_fund_totals(church, year, month):
            ws.cell(row=row, column=1, value=item['label'])
            ws.cell(row=row, column=2, value=float(item['entries']))
            ws.cell(row=row, column=3, value=float(item['exits']))
            row += 1
    else:
        # annual
        ws['A1'] = f'Fechamento Mensal Anual - {year} - {church.name}'
        ws['A1'].font = Font(bold=True, size=14)
        headers = ['Mês', 'Saldo Anterior', 'Total Entradas', 'Total Saídas', 'Saldo Final', 'Status']
        for col, h in enumerate(headers, start=1):
            cell = ws.cell(row=3, column=col, value=h)
            cell.font = header_font
            cell.fill = header_fill
        for month_idx in range(1, 13):
            closing, _ = get_or_create_monthly_closing(church, year, month_idx)
            row = 3 + month_idx
            ws.cell(row=row, column=1, value=MONTH_NAMES[month_idx - 1])
            ws.cell(row=row, column=2, value=closing.previous_balance)
            ws.cell(row=row, column=3, value=closing.total_entries)
            ws.cell(row=row, column=4, value=closing.total_exits)
            ws.cell(row=row, column=5, value=closing.final_balance)
            ws.cell(row=row, column=6, value='Fechado' if closing.is_closed else 'Aberto')

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
