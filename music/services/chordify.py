"""Extração de acordes do Chordify com fallback: requests → Selenium.

Reutiliza o formato do projeto original: o endpoint
`https://chordify.net/api/v2/songs/{youtube_id}/chords?vocabulary=extended_inversions`
devolve `chords` (string com linhas `tempo;nota;inicio;fim`) e metadados
(`derivedKey`, `derivedBpm`).
"""

import json
import logging
import re
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

URL_CHORDIFY = (
    'https://chordify.net/api/v2/songs/youtube:{}/chords?vocabulary=extended_inversions'
)
URL_GUITAR = 'https://chordify.net/img/diagrams/guitar/{}.png'
URL_PIANO = 'https://chordify.net/img/diagrams/piano/{}.png'

CHORDIFY_HEADERS = {
    'user-agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
    ),
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/*,*/*;q=0.8',
    'accept-language': 'pt-BR,pt;q=0.9,en;q=0.8,en-US;q=0.7',
    'sec-ch-ua': '"Google Chrome";v="126", "Chromium";v="126", "Not/A)Brand";v="99"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'document',
    'sec-fetch-mode': 'navigate',
    'sec-fetch-site': 'none',
    'origin': 'https://chordify.net',
    'referer': 'https://chordify.net/',
}

# Correções para nomes de notas não convencionais
NOTE_MAP = {
    'Ds': 'Eb',
    'Gs': 'Ab',
    'As': 'Bb',
}

_SUFFIX_MAP = {
    'maj': '',
    'min': 'm',
    'min7': 'm7',
    'maj7': '7',
    'sus4': 'sus4',
    'sus2': 'sus2',
    'dim': 'dim',
    'aug': 'aug',
    '7': '7',
    'm7': 'm7',
    'maj6': '6',
    'min6': 'm6',
    '6': '6',
    '9': '9',
    'add9': 'add9',
    'm9': 'm9',
    'maj9': 'maj9',
    'dim7': 'dim7',
    'm7b5': 'm7b5',
    'sus': 'sus',
    'maj9': 'maj9',
    'min9': 'm9',
}


def format_note(note: str) -> str:
    """Converte a notação do Chordify (ex.: `F:maj`, `D:min7`) para forma compacta.

    Ex.: `F:maj` → `F`, `D:min7` → `Dm7`, `C:sus4` → `Csus4`, `Bb:maj` → `Bb`.
    `N` (silêncio) é preservado.
    """
    note = str(note or '').strip()
    for wrong, right in NOTE_MAP.items():
        note = note.replace(wrong, right)
    if note == 'N':
        return 'N'
    base = note.split(':')[0]
    sufixo = note.split(':')[1] if ':' in note else ''
    for key, value in _SUFFIX_MAP.items():
        if sufixo.startswith(key):
            return base + value + sufixo[len(key):]
    return base + sufixo


def get_image_note(note: str, instrument: str = 'guitar') -> str:
    """URL do diagrama (guitarra ou piano) de um acorde do Chordify."""
    clean = str(note).replace(':', '_').replace('#', 's')
    for wrong, right in NOTE_MAP.items():
        if wrong in clean:
            clean = clean.replace(wrong, right)
    if instrument == 'piano':
        return URL_PIANO.format(clean)
    return URL_GUITAR.format(clean)


def get_notes_formatada(chords_str: str, instrument: str = 'guitar') -> list[dict[str, Any]]:
    """Converte a string bruta de acordes em lista com timing e imagem.

    Cada linha é `tempo;nota;inicio;fim`. Retorna:
    [{'note', 'note_fmt', 'image', 'start', 'end', 'tempo', 'instrument'}]
    """
    result: list[dict[str, Any]] = []
    for line in (chords_str or '').strip().split('\n'):
        parts = line.split(';')
        if len(parts) < 4:
            continue
        tempo = int(parts[0]) if parts[0].isdigit() else None
        note = parts[1]
        if note == 'N':
            continue
        try:
            start = float(parts[2])
            end = float(parts[3])
        except ValueError:
            continue
        result.append({
            'note': note,
            'note_fmt': format_note(note),
            'image': get_image_note(note, instrument),
            'start': round(start, 2),
            'end': round(end, 2),
            'tempo': tempo,
            'instrument': instrument,
        })
    return result


# ---------------------------------------------------------------------------
# Estratégia 1 — requests direto
# ---------------------------------------------------------------------------

def _fetch_requests(youtube_id: str) -> dict[str, Any]:
    session = requests.Session()
    session.headers.update(CHORDIFY_HEADERS)
    url = URL_CHORDIFY.format(youtube_id)
    resp = session.get(url, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if 'chords' not in data and 'error' in str(data).lower():
        raise ValueError(f'Chordify retornou erro para {youtube_id}')
    return data


# ---------------------------------------------------------------------------
# Estratégia 2 — Selenium (protegida contra ausência de binário)
# ---------------------------------------------------------------------------

def _fetch_selenium(youtube_id: str) -> dict[str, Any]:
    try:
        from music.services.chromedriver import create_chrome_driver  # noqa: PLC0415
    except (ImportError, FileNotFoundError) as exc:
        raise RuntimeError(f'Selenium indisponível no ambiente: {exc}') from exc

    driver = None
    try:
        driver = create_chrome_driver()
    except Exception as exc:
        raise RuntimeError(
            f'Não foi possível iniciar o ChromeDriver ({exc}). '
            'Confie na camada requests ou preencha o tom manualmente.'
        ) from exc
    try:
        url = URL_CHORDIFY.format(youtube_id)
        driver.get(url)
        time.sleep(3)
        body = driver.find_element('tag name', 'body').text
        return json.loads(body)
    finally:
        try:
            driver.quit()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Enriquecimento de dados (usado pelos viewsets)
# ---------------------------------------------------------------------------

def chordify_unavailable(reason: str = '') -> dict[str, Any]:
    logger.warning('Chordify indisponível para coleta: %s', reason)
    return {
        'status': 'unavailable',
        'chords_formatada': [],
        'format_key': '',
        'message': (
            'Não foi possível obter os acordes automaticamente. '
            'Você pode informar o tom manualmente e prosseguir com o cadastro.'
        ),
        'reason': reason[:500] if reason else '',
    }


def enrich_chordify_data(youtube_id: str, instrument: str = 'guitar') -> dict[str, Any]:
    """Busca os dados de um vídeo no Chordify e enriquece com acordes formatados.

    Fallback: requests → Selenium. Se todos falharem, devolve
    `{'status': 'unavailable', ...}` (sem tomar 5xx) para o frontend exibir
    mensagem amigável e permitir cadastro manual com tom informado.
    """
    strategies = [
        ('requests', _fetch_requests),
        ('selenium', _fetch_selenium),
    ]
    errors: list[str] = []
    for name, fn in strategies:
        try:
            data = fn(youtube_id)
            logger.info('Chordify OK para %s via %s', youtube_id, name)
            return _normalize_chordify_data(data, instrument)
        except Exception as exc:
            errors.append(f'{name}: {exc}')
            logger.warning('Chordify falhou para %s via %s: %s', youtube_id, name, exc)
    return chordify_unavailable('\n'.join(errors))


def _normalize_chordify_data(data: dict[str, Any], instrument: str) -> dict[str, Any]:
    chords: list[dict[str, Any]] = []
    if isinstance(data.get('chords'), str):
        chords = get_notes_formatada(data['chords'], instrument)
    elif data.get('chords_lyrics'):
        for item in data['chords_lyrics']:
            item.setdefault('image', get_image_note(item.get('note', ''), instrument))
            item.setdefault('note_fmt', format_note(item.get('note', '')))
            item.setdefault('instrument', instrument)
            chords = data['chords_lyrics']

    data['chords_formatada'] = chords
    data['format_key'] = format_note(data.get('derivedKey', '') or '')
    data.setdefault('derivedBpm', data.get('derivedBpm'))
    data['status'] = 'success'
    data.setdefault('youtube_id', '')
    return data


def extract_key_tempo_from_chords(chords: list[dict[str, Any]]) -> tuple[str, float | None]:
    """Infere tom/BPM a partir dos acordes formatados (fallback dos metadados)."""
    tempo = [c['tempo'] for c in chords if c.get('tempo')]
    bpm = tempo[0] / 1000.0 if tempo else None
    notes = [c['note'] for c in chords]
    key = ''
    if notes:
        # A primeira nota é uma aproximação razoável do tom
        key = format_note(notes[0])
    return key, bpm


def _normalize_int(value: Any) -> int | None:
    m = re.search(r'\d+', str(value or ''))
    return int(m.group()) if m else None