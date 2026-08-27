"""Serviços de negócio do módulo financeiro (Eclésia IDB)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

import io
import re

import pandas as pd
from django.db.models import Sum
from django.db import transaction

from .models import (
    DepartmentCategory,
    FinancialEntry,
    FinancialExit,
    MonthlyClosing,
    Tither,
    TitheRecord,
)

# Departamentos que recebem 10% sobre as ofertas na remessa regional.
TENTH_PERCENT_DEPARTMENTS = [
    DepartmentCategory.MULHERES,
    DepartmentCategory.HOMENS,
    DepartmentCategory.JOVENS,
    DepartmentCategory.ESC_BIBLICA,
    DepartmentCategory.INFANTIL,
    DepartmentCategory.ADOLESCENTES,
    DepartmentCategory.MISSOES,
]

# Categoria de dízimo
DIZIMO_CATEGORY = DepartmentCategory.DIZIMO


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
    }


# --------------------------------------------------------------------------- #
# 2. Cálculo da remessa financeira regional (Convenção Paraíba)
# --------------------------------------------------------------------------- #
def _sum_category(church, year: int, month: int, category: str) -> Decimal:
    return FinancialEntry.objects.filter(
        church=church, date__year=year, date__month=month,
        category=category,
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')


def calc_regional_remittance(church, year: int, month: int) -> Dict:
    """Calcula a remessa financeira regional da Convenção Paraíba.

    - 15% sobre o Total de Dízimos:
        * 11% Região
        * 1% Fundo Ministerial
        * 1.5% Distrito
        * 1.5% Evangelismo

    - 10% sobre as Ofertas de cada departamento (Mulheres, Homens, Jovens,
      EBD, Infantil, Adolescentes, Missões).
    """
    total_tithes = _sum_category(church, year, month, DIZIMO_CATEGORY)

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
        total = _sum_category(church, year, month, dep)
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


def build_monthly_balance(church, year: int, month: int) -> Dict:
    """Constrói o balancete mensal (entradas, saídas e saldo)."""
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
def _parse_date(value, line: int, field: str, errors: List[str]) -> Optional[date]:
    """Normaliza data vinda da planilha para objeto date, capturando erros."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, date) and not isinstance(value, pd.Timestamp):
        return value
    try:
        if isinstance(value, pd.Timestamp):
            return value.date()
        if isinstance(value, (int, float)):
            # Excel serial date
            from datetime import datetime, timedelta
            if isinstance(value, float) and value > 1000:
                return (datetime(1899, 12, 30) + timedelta(days=value)).date()
            return date(int(value), 1, 1)
        # Strings no formato dd/mm/yyyy, dd-mm-yyyy ou yyyy-mm-dd
        for fmt in ('%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d'):
            try:
                return pd.to_datetime(value, format=fmt).date()
            except (ValueError, TypeError):
                continue
        parsed = pd.to_datetime(value, dayfirst=True, errors='coerce')
        if pd.isna(parsed):
            raise ValueError
        return parsed.date()
    except Exception:
        errors.append(
            f'Linha {line}: campo "{field}" com data inválida ({value!r}).'
        )
        return None


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


def parse_entries_sheet(file) -> Tuple[List[dict], List[str]]:
    """Lê planilha 'Controle das Entradas' e retorna linhas validadas + erros.

    Colunas esperadas: Data, Culto/Serviço (service_description),
    Categoria (category), Valor (amount).
    """
    df = _rename_columns(pd.read_excel(file))
    errors: List[str] = []
    rows: List[dict] = []

    for idx, row in df.iterrows():
        line = idx + 2  # linha do excel (1 = cabeçalho)
        data = _parse_date(_get(row, 'data'), line, 'Data', errors)
        desc = _get(row, ['culto', 'servico', 'culto_servico', 'culto/servico', 'service_description'])
        categoria = _get(row, ['categoria', 'category', 'departamento'])
        valor = _parse_amount(_get(row, ['valor', 'amount', 'valor_rs', 'total']), line, 'Valor', errors)

        if data is None or not desc or not categoria or valor is None:
            continue
        categoria = _normalize_header(categoria).upper()
        if categoria not in DepartmentCategory.values:
            errors.append(f'Linha {line}: categoria inválida ({categoria}).')
            continue
        if valor <= 0:
            errors.append(f'Linha {line}: valor deve ser maior que 0.')
            continue

        rows.append({
            'date': data,
            'service_description': str(desc).strip(),
            'category': categoria,
            'amount': valor,
        })
    return rows, errors


def parse_exits_sheet(file) -> Tuple[List[dict], List[str]]:
    """Lê planilha 'Controle de Saídas' e retorna linhas validadas + erros.

    Colunas esperadas: Data, Descrição (description), Categoria (category),
    Valor (amount), e opcionalmente Comprovante (receipt).
    """
    df = _rename_columns(pd.read_excel(file))
    errors: List[str] = []
    rows: List[dict] = []

    for idx, row in df.iterrows():
        line = idx + 2
        data = _parse_date(_get(row, 'data'), line, 'Data', errors)
        desc = _get(row, ['descricao', 'description', 'descrição'])
        categoria = _get(row, ['categoria', 'category', 'departamento'])
        valor = _parse_amount(_get(row, ['valor', 'amount', 'valor_rs', 'total']), line, 'Valor', errors)

        if data is None or not desc or not categoria or valor is None:
            continue
        categoria = _normalize_header(categoria).upper()
        if categoria not in DepartmentCategory.values:
            errors.append(f'Linha {line}: categoria inválida ({categoria}).')
            continue
        if valor <= 0:
            errors.append(f'Linha {line}: valor deve ser maior que 0.')
            continue

        rows.append({
            'date': data,
            'description': str(desc).strip(),
            'category': categoria,
            'amount': valor,
        })
    return rows, errors


def parse_tithers_sheet(file) -> Tuple[List[dict], List[str]]:
    """Lê planilha 'Membros Dizimistas' e retorna linhas validadas + erros.

    Colunas esperadas: Nome (name), Mês (month), Ano (year), Valor (amount).
    """
    df = _rename_columns(pd.read_excel(file))
    errors: List[str] = []
    rows: List[dict] = []

    for idx, row in df.iterrows():
        line = idx + 2
        name = _get(row, ['nome', 'name', 'membro', 'dizimista'])
        month = _get(row, ['mes', 'month', 'mês'])
        year = _get(row, ['ano', 'year'])
        valor = _parse_amount(_get(row, ['valor', 'amount', 'valor_rs', 'total']), line, 'Valor', errors)

        if not name or month is None or year is None or valor is None:
            continue
        try:
            month = int(month)
            year = int(year)
        except (TypeError, ValueError):
            errors.append(f'Linha {line}: mês/ano inválido.')
            continue
        if not (1 <= month <= 12):
            errors.append(f'Linha {line}: mês deve estar entre 1 e 12.')
            continue
        if valor <= 0:
            errors.append(f'Linha {line}: valor deve ser maior que 0.')
            continue

        rows.append({
            'name': str(name).strip(),
            'month': month,
            'year': year,
            'amount': valor,
        })
    return rows, errors


def _get(row, keys):
    """Obtém a primeira coluna correspondente a uma das chaves buscadas."""
    if isinstance(keys, str):
        keys = [keys]
    normalized = {_normalize_header(k): v for k, v in row.to_dict().items()}
    for key in keys:
        nk = _normalize_header(key)
        if nk in normalized:
            return normalized[nk]
    return None


@transaction.atomic
def import_spreadsheets(church, files: Dict[str, object]) -> Dict:
    """Processa em lote as planilhas mensais enviadas (multipart).

    `files` deve conter as chaves: 'entries', 'exits', 'tithers' (arquivos).
    Cada chave é opcional.
    """
    result = {
        'entries_imported': 0,
        'exits_imported': 0,
        'tithers_imported': 0,
        'tithe_records_imported': 0,
        'errors': [],
    }

    entries_file = files.get('entries')
    if entries_file is not None:
        rows, errors = parse_entries_sheet(entries_file)
        result['errors'].extend(errors)
        bulk = [
            FinancialEntry(
                church=church, date=r['date'],
                service_description=r['service_description'],
                category=r['category'], amount=r['amount'],
            )
            for r in rows
        ]
        FinancialEntry.objects.bulk_create(bulk)
        result['entries_imported'] = len(bulk)

    exits_file = files.get('exits')
    if exits_file is not None:
        rows, errors = parse_exits_sheet(exits_file)
        result['errors'].extend(errors)
        bulk = [
            FinancialExit(
                church=church, date=r['date'], description=r['description'],
                category=r['category'], amount=r['amount'],
            )
            for r in rows
        ]
        FinancialExit.objects.bulk_create(bulk)
        result['exits_imported'] = len(bulk)

    tithers_file = files.get('tithers')
    if tithers_file is not None:
        rows, errors = parse_tithers_sheet(tithers_file)
        result['errors'].extend(errors)
        tither_cache: Dict[str, Tither] = {}
        for r in rows:
            name = r['name']
            if name not in tither_cache:
                tither, _ = Tither.objects.get_or_create(
                    church=church, name=name,
                )
                tither_cache[name] = tither
            TitheRecord.objects.update_or_create(
                tither=tither_cache[name],
                year=r['year'],
                month=r['month'],
                defaults={'amount': r['amount']},
            )
            result['tithe_records_imported'] += 1
        result['tithers_imported'] = len(tither_cache)

    return result
