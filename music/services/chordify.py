"""Extração de acordes do Chordify via Selenium.

O Cloudflare bloqueia requests diretos à API (403), mas um browser real
(ChromeDriver) passa na verificação. A coleta tenta primeiro o endpoint de
API (`.../chords?vocabulary=extended_inversions`, que devolve o JSON
estruturado com `chords` em linhas `tempo;nota;inicio;fim`, `derivedKey` e
`derivedBpm`); em caso de bloqueio/falha, cai para a página pública
(`/chords/youtube:...`) extraindo do `__NEXT_DATA__` ou do DOM renderizado.
"""

import json
import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

URL_API = (
    'https://chordify.net/api/v2/songs/youtube:{}/chords'
    '?vocabulary=extended_inversions'
)
URL_CHORDS_PAGE = 'https://chordify.net/chords/youtube:{}'
URL_GUITAR = 'https://chordify.net/img/diagrams/guitar/{}.png'
URL_PIANO = 'https://chordify.net/img/diagrams/piano/{}.png'

# Marcadores típicos do WAF/Cloudflare para detectar bloqueio sem estourar exceção.
_CLOUDFLARE_MARKERS = (
    'just a moment',
    'cf-browser-verification',
    'cf-chl-',
    'checking your browser',
    'attention required!',
    'cf-error',
)

ERROR_BLOCKED = 'chordify_blocked_or_unavailable'

# Quanto tempo esperar o challenge do Cloudflare limpar antes de desistir.
# Mantém o scraping dentro do limite do router/Gunicorn do Heroku (~30s).
CF_CHALLENGE_TIMEOUT = 18.0
CF_CHALLENGE_POLL = 2.0
NEXT_DATA_JS = (
    "return document.getElementById('__NEXT_DATA__')?.textContent || ''"
)
BODY_JSON_JS = "return document.body.innerText || document.body.textContent || ''"

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

# A página renderizada do Chordify costuma exibir os acordes em notação
# solfège (Do-Re-Mi) com caracteres unicode de sub/sobrescrito e pausas.
_SOLFEGE_TO_LETTER = {
    'Do': 'C',
    'Re': 'D',
    'Mi': 'E',
    'Fa': 'F',
    'Sol': 'G',
    'So': 'G',
    'La': 'A',
    'Si': 'B',
}

_UNICODE_TRANS = str.maketrans({
    '♭': 'b',
    '♯': '#',
    '𝄽': 'N',  # pausa de compasso inteiro
    '𝄻': 'N',
    '⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
    '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9',
    'ₘ': 'm',
    'ᵐ': 'm', 'ᵃ': 'a', 'ʲ': 'j', 'ᵘ': 'u', 'ˢ': 's', 'ⁱ': 'i',
    '⁻': '-',
})


def _normalize_chord_symbol(symbol: str) -> str:
    """Converte o símbolo exibido na página para notação em letras/ASCII.

    Ex.: `Re♭` → `Db`, `Doₘ⁷` → `Cm7`, `Si♭ₘ⁷` → `Bbm7`,
    `Mi♭\\n/Do♯` → `Eb/C#` (slash chord quebrado por quebra de linha),
    `𝄽` → `N` (pausa). Notação em letras já limpa passa direto.
    """
    value = str(symbol or '').translate(_UNICODE_TRANS)
    # Slash chords ficam espalhados em duas linhas no DOM (`Mi♭` / `/Do♯`):
    # remove espaços/quebras de linha mantendo a barra.
    value = re.sub(r'\s+', '', value)
    if not value:
        return ''
    is_rest = value in ('N',)
    parts = [p.strip('/') for p in value.split('/')]
    normalized = []
    for part in parts:
        root = part
        suffix = ''
        for prefix, letter in _SOLFEGE_TO_LETTER.items():
            if part.startswith(prefix):
                root = letter
                suffix = part[len(prefix):]
                break
        normalized.append(root + suffix)
    if is_rest:
        return 'N'
    return '/'.join(p for p in normalized if p)


def format_note(note: str) -> str:
    """Converte a notação do Chordify (ex.: `F:maj`, `D:min7`) para forma compacta.

    Ex.: `F:maj` → `F`, `D:min7` → `Dm7`, `C:sus4` → `Csus4`, `Bb:maj` → `Bb`.
    `N` (silêncio) é preservado.
    """
    note = _normalize_chord_symbol(note)
    if note == 'N':
        return 'N'
    for wrong, right in NOTE_MAP.items():
        note = note.replace(wrong, right)
    if not note:
        return ''
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
        note = _normalize_chord_symbol(parts[1])
        if not note or note == 'N':
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
# Helpers de parsing/extração
# ---------------------------------------------------------------------------

def _safe_json_loads(text: str) -> Any | None:
    """`json.loads` seguro: valida vazio/prefixo e detecta página de bloqueio.

    Devolve `None` (em vez de estourar `JSONDecodeError`) quando o conteúdo
    não for JSON válido — ex.: página HTML do Cloudflare "Just a moment...".
    """
    body = str(text or '').strip()
    if not body or body[0] not in '{[':
        return None
    lower = body.lower()
    if any(marker in lower for marker in _CLOUDFLARE_MARKERS):
        return None
    try:
        return json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Selenium (protegida contra ausência de binário)
# ---------------------------------------------------------------------------

def _looks_like_blocked_page(source: str) -> bool:
    """Detecta página de verificação do Cloudflare/WAF no HTML."""
    lower = str(source or '').lower()
    return any(marker in lower for marker in _CLOUDFLARE_MARKERS)


def _extract_from_next_data(payload: Any) -> dict[str, Any] | None:
    """Extrai os dados de acordes do objeto `__NEXT_DATA__`.

    O JSON da página pública (`props.pageProps`) não tem o mesmo formato da
    API: percorre recursivamente por chaves `chords` (string), `derivedKey`
    e `derivedBpm` para montar payload compatível com `_normalize_chordify_data`.
    """
    found: dict[str, Any] = {}
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key in ('chords', 'derivedKey', 'derivedBpm', 'youtube_id'):
                if key not in found and isinstance(node.get(key), (str, int, float)):
                    found[key] = node[key]
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    if 'chords' not in found:
        return None
    found['chords'] = str(found.get('chords') or '')
    return found


def _extract_chords_from_dom(driver: Any) -> dict[str, Any] | None:
    """Fallback DOM: agrupa os acordes renderizados (buscas parciais).

    Em páginas sem `__NEXT_DATA__` populado, coleta elementos com atributo
    `data-chord` (ou classe `.chord`) e monta uma string `tempo;nota;0;0`.
    """
    try:
        from selenium.webdriver.common.by import By  # noqa: PLC0415

        nodes = driver.find_elements(By.CSS_SELECTOR, '[data-chord], .chord')
        logger.debug('Chordify[dom] encontrados %d candidatos a acorde no DOM.',
                     len(nodes))
        lines = []
        for node in nodes:
            note = _normalize_chord_symbol(
                node.get_attribute('data-chord') or node.text or ''
            )
            if note and note != 'N':
                lines.append(f'0;{note};0;0')
        if not lines:
            return None
        return {'chords': '\n'.join(lines), 'source': 'selenium_dom'}
    except Exception as exc:  # noqa: BLE001
        logger.debug('Falha ao extrair acordes do DOM: %s', exc)
        return None


def _wait_next_data(driver: Any, timeout: float = CF_CHALLENGE_TIMEOUT) -> str:
    """Aguarda até que `__NEXT_DATA__` apareça no DOM.

    O Cloudflare exibe uma página de verificação ("Just a moment...") que um
    navegador real soluciona sozinho em poucos segundos. Em vez de desistir na
    hora, faz polling até o dado real aparecer (ou o timeout estourar).

    Se os acordes já estiverem renderizados no DOM (`.chord`/`[data-chord]`),
    interrompe o polling antecipadamente — esperar o resto do `__NEXT_DATA__`
    só retardaria o fallback por DOM, que já tem o dado útil.
    """
    started = time.monotonic()
    deadline = started + timeout
    while time.monotonic() < deadline:
        elapsed = time.monotonic() - started
        try:
            value = driver.execute_script(NEXT_DATA_JS) or ''
        except Exception:  # noqa: BLE001
            value = ''
        if value.strip():
            logger.debug(
                'Chordify __NEXT_DATA__ presente após %.1fs (len=%d).',
                elapsed,
                len(value),
            )
            return value
        try:
            from selenium.webdriver.common.by import By  # noqa: PLC0415

            if driver.find_elements(By.CSS_SELECTOR, '[data-chord], .chord'):
                logger.info(
                    'Chordify __NEXT_DATA__ ausente em %.1fs, '
                    'mas acordes já renderizados no DOM → fallback direto.',
                    elapsed,
                )
                return ''
        except Exception:  # noqa: BLE001
            pass
        time.sleep(CF_CHALLENGE_POLL)
    logger.warning(
        'Chordify timeout de %.1fs sem __NEXT_DATA__ nem acordes no DOM.',
        timeout,
    )
    return ''


def _wait_for_json_body(driver: Any, timeout: float = CF_CHALLENGE_TIMEOUT) -> str:
    """Aguarda o browser renderizar o texto do endpoint de API.

    Após `driver.get(api_url)`, o `body.innerText` vira o JSON estruturado
    (ou a página de verificação do Cloudflare, ou um erro legível). Faz
    polling até surgir o primeiro corpus não-vazio; a decisão de usar/ignorar
    fica com o chamador, que já tem o fallback da página pública.
    """
    started = time.monotonic()
    deadline = started + timeout
    last_text = ''
    while time.monotonic() < deadline:
        elapsed = time.monotonic() - started
        try:
            text = str(driver.execute_script(BODY_JSON_JS) or '').strip()
        except Exception:  # noqa: BLE001
            text = ''
        if text and text != last_text:
            last_text = text
            logger.debug(
                'Chordify api body após %.1fs (len=%d, amostra=%r...)',
                elapsed,
                len(text),
                text[:120],
            )
            return text
        time.sleep(CF_CHALLENGE_POLL)
    logger.warning(
        'Chordify api sem body em %.1fs (timeout).',
        timeout,
    )
    return last_text


def _fetch_selenium(youtube_id: str) -> dict[str, Any]:
    started = time.monotonic()
    logger.info('Chordify[inicio] coletando cifras via Selenium para %s...', youtube_id)
    try:
        from music.services.chromedriver import create_chrome_driver  # noqa: PLC0415
    except (ImportError, FileNotFoundError) as exc:
        logger.error('Chordify[erro] Selenium indisponível após %.1fs: %s',
                     time.monotonic() - started, exc)
        raise RuntimeError(f'Selenium indisponível no ambiente: {exc}') from exc

    driver = None
    try:
        t = time.monotonic()
        driver = create_chrome_driver()
        logger.info('Chordify[driver] ChromeDriver criado em %.1fs',
                    time.monotonic() - t)
    except Exception as exc:
        logger.error('Chordify[driver] falha ao iniciar ChromeDriver após %.1fs: %s',
                     time.monotonic() - started, exc)
        raise RuntimeError(
            f'Não foi possível iniciar o ChromeDriver ({exc}). '
            'Preencha o tom manualmente ou tente novamente.'
        ) from exc
    try:
        from selenium.webdriver.common.by import By  # noqa: PLC0415
        from selenium.webdriver.support.ui import WebDriverWait  # noqa: PLC0415
        from selenium.webdriver.support import expected_conditions as EC  # noqa: PLC0415

        # 1) Tenta o endpoint de API (JSON estruturado) num browser real.
        api_url = URL_API.format(youtube_id)
        t = time.monotonic()
        driver.get(api_url)
        logger.info('Chordify[api] GET %s concluído em %.1fs',
                    api_url, time.monotonic() - t)

        t = time.monotonic()
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.TAG_NAME, 'body'))
        )
        logger.info('Chordify[body] <body> presente após %.1fs',
                    time.monotonic() - t)

        t = time.monotonic()
        api_text = _wait_for_json_body(driver, timeout=CF_CHALLENGE_TIMEOUT)
        logger.info('Chordify[api] body resolvido em %.1fs (len=%d)',
                    time.monotonic() - t, len(api_text))

        if not _looks_like_blocked_page(api_text[:500]):
            raw_api: Any = _safe_json_loads(api_text)
            if isinstance(raw_api, dict) and raw_api.get('chords'):
                raw_api.setdefault('youtube_id', youtube_id)
                raw_api['source'] = 'selenium_api'
                logger.info('Chordify[fim] ok via selenium_api em %.1fs',
                            time.monotonic() - started)
                return raw_api
        logger.info('Chordify[api] sem JSON útil; caindo para a página pública.')

        # 2) Fallback: PÁGINA PÚBLICA da música (nunca para a URL crua da API).
        url = URL_CHORDS_PAGE.format(youtube_id)
        t = time.monotonic()
        driver.get(url)
        logger.info('Chordify[navegacao] GET %s concluído em %.1fs',
                    url, time.monotonic() - t)

        t = time.monotonic()
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.TAG_NAME, 'body'))
        )
        logger.info('Chordify[body] <body> presente após %.1fs',
                    time.monotonic() - t)

        # Espere o __NEXT_DATA__ (isso também dá tempo do CF challenge resolver).
        t = time.monotonic()
        next_data = _wait_next_data(driver, timeout=CF_CHALLENGE_TIMEOUT)
        logger.info('Chordify[next_data] resolvido em %.1fs (len=%d)',
                    time.monotonic() - t, len(next_data))

        page_source = str(driver.page_source or '')
        if _looks_like_blocked_page(str(next_data or '') or page_source):
            logger.warning(
                'Chordify[bloqueio] Cloudflare/WAF para %s via selenium '
                'após %.1fs, sem dados coletados.',
                youtube_id,
                time.monotonic() - started,
            )
            raise RuntimeError(
                'Página protegida por verificação do Cloudflare/WAF '
                f'(YouTube ID {youtube_id}).'
            )

        raw_data: Any = _safe_json_loads(next_data) if next_data else None

        if raw_data is None:
            # DOM fallback: página carregou, mas sem __NEXT_DATA__.
            t = time.monotonic()
            data = _extract_chords_from_dom(driver)
            logger.info('Chordify[dom] extração por DOM em %.3fs', time.monotonic() - t)
            if data is None:
                logger.warning(
                    'Chordify[erro] nenhum acorde no DOM para %s após %.1fs.',
                    youtube_id,
                    time.monotonic() - started,
                )
                raise RuntimeError(
                    f'Nenhum dado de acorde encontrado na página para {youtube_id}.'
                )
            data.setdefault('youtube_id', youtube_id)
            logger.info('Chordify[fim] ok via selenium_dom em %.1fs',
                        time.monotonic() - started)
            return data

        t = time.monotonic()
        data = _extract_from_next_data(raw_data)
        if data is None:
            data = _extract_chords_from_dom(driver)
        logger.info('Chordify[extração] NEXT_DATA/DOM em %.3fs', time.monotonic() - t)
        if data is None:
            logger.warning(
                'Chordify[erro] nenhum acorde no NEXT_DATA/DOM para %s após %.1fs.',
                youtube_id,
                time.monotonic() - started,
            )
            raise RuntimeError(
                f'Nenhum dado de acorde encontrado na página para {youtube_id}.'
            )
        data.setdefault('youtube_id', youtube_id)
        logger.info('Chordify[fim] ok em %.1fs', time.monotonic() - started)
        return data
    finally:
        try:
            driver.quit()
        except Exception:  # noqa: BLE001
            pass
        logger.debug('Chordify driver encerrado.')


# ---------------------------------------------------------------------------
# Enriquecimento de dados (usado pelos viewsets)
# ---------------------------------------------------------------------------

def chordify_unavailable(reason: str = '') -> dict[str, Any]:
    logger.warning('Chordify indisponível para coleta: %s', reason)
    return {
        'status': 'unavailable',
        'success': False,
        'error': ERROR_BLOCKED,
        'chords': [],
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

    O Cloudflare bloqueia o acesso via `requests`, então a coleta usa apenas o
    Selenium. Em caso de falha, devolve `{'status': 'unavailable', ...}` (sem
    tomar 5xx) para o frontend exibir mensagem amigável e permitir cadastro
    manual com tom informado.
    """
    started = time.monotonic()
    logger.info('Chordify[inicio] buscando cifras para youtube_id=%s instrument=%s...',
                youtube_id, instrument)
    try:
        data = _fetch_selenium(youtube_id)
        result = _normalize_chordify_data(data, instrument)
        elapsed = time.monotonic() - started
        logger.info(
            'Chordify[tempo_busca] youtube_id=%s concluído em %.2fs '
            '| origem=%s acordes=%d tom=%s bpm=%s',
            youtube_id,
            elapsed,
            result.get('source') or data.get('source') or 'next_data',
            len(result.get('chords_formatada') or []),
            result.get('format_key') or '-',
            result.get('derivedBpm') or '-',
        )
        return result
    except Exception as exc:
        elapsed = time.monotonic() - started
        logger.warning(
            'Chordify[tempo_busca] youtube_id=%s falhou em %.2fs: %s',
            youtube_id,
            elapsed,
            exc,
        )
        return chordify_unavailable(str(exc))


def _normalize_chordify_data(data: dict[str, Any], instrument: str) -> dict[str, Any]:
    chords: list[dict[str, Any]] = []
    if isinstance(data.get('chords'), str):
        chords = get_notes_formatada(data['chords'], instrument)
    elif data.get('chords_lyrics'):
        for item in data['chords_lyrics']:
            if not isinstance(item, dict):
                continue
            item.setdefault('image', get_image_note(item.get('note', ''), instrument))
            item.setdefault('note_fmt', format_note(item.get('note', '')))
            item.setdefault('instrument', instrument)
            chords = data['chords_lyrics']

    data['success'] = True
    data.setdefault('chords', [])
    data['chords_formatada'] = chords
    derived_key = data.get('derivedKey') or ''
    if not derived_key and chords:
        derived_key, _ = extract_key_tempo_from_chords(chords)
    data['format_key'] = format_note(derived_key or '')
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