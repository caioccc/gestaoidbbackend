"""Conversão de valores monetários e datas para extenso em pt-BR.

Exemplos:
- valor_por_extenso(Decimal('1850.00'))  -> 'um mil, oitocentos e cinquenta reais'
- valor_por_extenso(Decimal('0.03'))     -> 'três centavos'
- data_por_extenso(date(2026, 9, 14))    -> 'quatorze de setembro de dois mil e vinte e seis'

Usado na emissão de Recibos Financeiros e datas formais dos documentos.
"""
from datetime import date

from decimal import Decimal, ROUND_DOWN

_UNIDADES = [
    '', 'um', 'dois', 'três', 'quatro', 'cinco',
    'seis', 'sete', 'oito', 'nove',
]
_DEZENAS = [
    '', 'dez', 'vinte', 'trinta', 'quarenta', 'cinquenta',
    'sessenta', 'setenta', 'oitenta', 'noventa',
]
_CENTENAS = [
    '', 'cento', 'duzentos', 'trezentos', 'quatrocentos',
    'quinhentos', 'seiscentos', 'setecentos', 'oitocentos', 'novecentos',
]
_ESPECIAIS = {
    10: 'dez',
    11: 'onze',
    12: 'doze',
    13: 'treze',
    14: 'quatorze',
    15: 'quinze',
    16: 'dezesseis',
    17: 'dezessete',
    18: 'dezoito',
    19: 'dezenove',
}

# Sufixos por ordem de grandeza (índice i refere-se ao grupo 10**(3*(i+1))).
_SUFFIXES = [
    (10 ** 3, 'mil', 'mil'),
    (10 ** 6, 'milhão', 'milhões'),
    (10 ** 9, 'bilhão', 'bilhões'),
    (10 ** 12, 'trilhão', 'trilhões'),
]

_MESES = {
    1: 'janeiro', 2: 'fevereiro', 3: 'março', 4: 'abril',
    5: 'maio', 6: 'junho', 7: 'julho', 8: 'agosto',
    9: 'setembro', 10: 'outubro', 11: 'novembro', 12: 'dezembro',
}


def _grupo_por_extenso(n: int) -> str:
    """Extenso de um número natural de 1 a 999."""
    if n == 0:
        return ''
    if n == 100:
        return 'cem'
    centena = n // 100
    resto = n % 100
    partes = []
    if centena:
        partes.append(_CENTENAS[centena])
    if resto in _ESPECIAIS:
        partes.append(_ESPECIAIS[resto])
    elif resto:
        dezena = resto // 10
        unidade = resto % 10
        if dezena:
            partes.append(_DEZENAS[dezena])
        if unidade:
            partes.append(_UNIDADES[unidade])
    return ' e '.join(partes)


def _grupos_base_10(n: int):
    """Decompõe n em grupos de 3 algarismos (unidades primeiro)."""
    grupos = []
    resto = n
    while resto:
        grupos.append(resto % 1000)
        resto //= 1000
    return grupos


def inteiro_por_extenso(n: int, connector: str = ', ') -> str:
    """Extenso de um número inteiro (0 a quintilhão).

    Usa `connector` entre os grupos de milhar — vírgula para valores
    (padrão), ' e ' para datas/anos ('dois mil e vinte e seis').
    """
    if n == 0:
        return 'zero'

    partes = []
    for i, grupo in enumerate(_grupos_base_10(n)):
        if grupo == 0:
            continue
        texto = _grupo_por_extenso(grupo)
        if i > 0:
            sufixo = _SUFFIXES[i - 1]
            singular = grupo == 1
            texto = f'{texto} {sufixo[1] if singular else sufixo[2]}'
        partes.append(texto)

    partes.reverse()
    return connector.join(partes)


def _requer_de(n: int) -> bool:
    """Verifica se o grupo mais significativo exige a preposição 'de'.

    Valores a partir de um milhão usam 'de' antes da moeda
    (ex.: 'um milhão de reais'); os milhares não
    (ex.: 'um mil reais').
    """
    grupos = _grupos_base_10(n)
    most_significant = len(grupos) - 1
    return most_significant >= 2


def valor_por_extenso(amount) -> str:
    """Converte um valor em reais (Decimal) para extenso em pt-BR.

    >>> valor_por_extenso(Decimal('1850.00'))
    'um mil, oitocentos e cinquenta reais'
    >>> valor_por_extenso(Decimal('0.03'))
    'três centavos'
    >>> valor_por_extenso(Decimal('12345.67'))
    'doze mil, trezentos e quarenta e cinco reais e sessenta e sete centavos'
    """
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))
    inteiro = int(amount.to_integral_value(rounding=ROUND_DOWN))
    centavos = int(abs(amount - Decimal(inteiro)) * 100)

    if inteiro == 0 and centavos == 0:
        return 'zero reais'

    partes = []
    if inteiro:
        texto = inteiro_por_extenso(inteiro)
        de = ' de' if _requer_de(inteiro) else ''
        partes.append(f'{texto}{de} {"real" if inteiro == 1 else "reais"}')
    if centavos:
        texto = inteiro_por_extenso(centavos)
        partes.append(f'{texto} {"centavo" if centavos == 1 else "centavos"}')
    return ' e '.join(partes)


def data_por_extenso(value) -> str:
    """Data por extenso em pt-BR (ex.: 'quatorze de setembro de dois mil e vinte e seis')."""
    if isinstance(value, str):
        value = date.fromisoformat(value)
    dia = inteiro_por_extenso(value.day)
    mes = _MESES[value.month]
    ano = inteiro_por_extenso(value.year, connector=' e ')
    return f'{dia} de {mes} de {ano}'