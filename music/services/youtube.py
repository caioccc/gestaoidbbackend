"""Busca no YouTube com fallback em cadeia: requests → yt-dlp → Selenium.

Estratégias (cada uma devolve `{'status': 'success', 'source': ..., 'results': [...]}`):

1. **requests + parse de `ytInitialData`** — rápido, sem binários.
2. **yt-dlp** (API Python com `extract_flat`) — quando o requests falha/vazio.
3. **ChromeDriver / Selenium** — último recurso; se o binário não existir, é
   capturado sem derrubar a aplicação.

Se todas falharem → **degradação graciosa**:
`{'status': 'unavailable', 'results': [], 'message': ...}` (HTTP 200/503).
"""

import logging
import re
import urllib.parse
from typing import Any

import requests

logger = logging.getLogger(__name__)

RESULTS_PAGE = 'https://www.youtube.com/results'
_WATCH_PAGE = 'https://www.youtube.com/watch'

_RE_INITIAL_DATA = re.compile(r'var ytInitialData = ({.*?});</script>', re.DOTALL)

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/*,*/*;q=0.8',
    'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8',
    'Referer': 'https://www.youtube.com/',
}

_YT_ID_PATTERNS = [
    r'(?:v=|youtu\.be/)([\w-]{11})',
    r'youtube\.com/embed/([\w-]{11})',
    r'youtube\.com/watch\?v=([\w-]{11})',
]

UNAVAILABLE_MESSAGE = (
    'O serviço de busca automática está temporariamente indisponível. '
    'Você ainda pode colar o link direto do vídeo do YouTube.'
)


def extract_youtube_id(text: str) -> str | None:
    """Extrai o ID de 11 caracteres de uma URL ou texto do YouTube."""
    for pat in _YT_ID_PATTERNS:
        m = re.search(pat, text or '')
        if m:
            return m.group(1)
    return None


def _seconds_to_str(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f'{h}:{m:02d}:{s:02d}'
    return f'{m}:{s:02d}'


def _response(results: list[dict[str, Any]], source: str) -> dict[str, Any]:
    return {'status': 'success', 'source': source, 'results': results}


def unavailable(reason: str = '') -> dict[str, Any]:
    logger.warning('YouTube search indisponível: %s', reason)
    return {
        'status': 'unavailable',
        'results': [],
        'message': UNAVAILABLE_MESSAGE,
        'reason': reason[:500] if reason else '',
    }


# ---------------------------------------------------------------------------
# Estratégia 1 — requests + parsing de ytInitialData
# ---------------------------------------------------------------------------

def _parse_video_renderer(renderer: dict[str, Any]) -> dict[str, Any] | None:
    vid = renderer.get('videoId', '')
    if not vid:
        return None
    title_runs = renderer.get('title', {}).get('runs')
    title = title_runs[0].get('text', '') if title_runs else ''
    if not title:
        title = renderer.get('title', {}).get('simpleText', '')
    channel_runs = renderer.get('ownerText', {}).get('runs', [])
    channel = channel_runs[0].get('text', '') if channel_runs else ''
    thumb = ''
    thumbs = renderer.get('thumbnail', {}).get('thumbnails', [])
    if thumbs:
        thumb = thumbs[-1].get('url', '')
    duration = renderer.get('lengthText', {}).get('simpleText', '')
    view_count = renderer.get('viewCountText', {}).get('simpleText', '')
    return {
        'title': title,
        'youtube_id': vid,
        'duration': duration,
        'channel_name': channel,
        'view_count': view_count,
        'thumbnail_url': thumb or f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg',
    }


def _walk_video_renderers(node: Any, out: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if not isinstance(node, (dict, list)):
        return out
    if isinstance(node, dict):
        renderer = node.get('videoRenderer')
        if isinstance(renderer, dict):
            parsed = _parse_video_renderer(renderer)
            if parsed and parsed['youtube_id']:
                out.append(parsed)
                if len(out) >= limit:
                    return out
        for value in node.values():
            if len(out) >= limit:
                return out
            _walk_video_renderers(value, out, limit)
    elif isinstance(node, list):
        for item in node:
            if len(out) >= limit:
                return out
            _walk_video_renderers(item, out, limit)
    return out


def _search_requests(query: str, limit: int = 20) -> dict[str, Any]:
    """GET na página de resultados + regex `ytInitialData` (sem binários)."""
    url = f'{RESULTS_PAGE}?search_query={urllib.parse.quote(query)}'
    resp = requests.get(url, headers=_HEADERS, timeout=15)
    resp.raise_for_status()
    match = _RE_INITIAL_DATA.search(resp.text)
    if not match:
        raise RuntimeError('Não foi possível localizar ytInitialData na página')
    data = match.group(1)
    if not data:
        return {'status': 'success', 'source': 'requests', 'results': []}
    payload = data.encode('utf-8')
    try:
        import json  # noqa: PLC0415
        parsed = json.loads(payload)
    except Exception as exc:
        raise RuntimeError(f'JSON ytInitialData inválido: {exc}') from exc
    results: list[dict[str, Any]] = []
    _walk_video_renderers(parsed, results, limit)
    return _response(results, 'requests')


# ---------------------------------------------------------------------------
# Estratégia 2 — yt-dlp (API Python)
# ---------------------------------------------------------------------------

def _search_ytdlp(query: str, limit: int = 20) -> dict[str, Any]:
    import yt_dlp  # noqa: PLC0415

    opts = {
        'quiet': True,
        'skip_download': True,
        'extract_flat': 'in_playlist',
        'default_search': f'ytsearch{limit}',
        'no_warnings': True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
    except Exception as exc:
        raise RuntimeError(f'yt-dlp falhou: {exc}') from exc
    entries = info.get('entries') or []
    results = []
    for e in entries:
        vid = e.get('id', '')
        if not vid:
            continue
        duration = e.get('duration') or 0
        results.append({
            'title': e.get('title', ''),
            'youtube_id': vid,
            'duration': _seconds_to_str(int(duration)) if duration else '',
            'channel_name': e.get('uploader', '') or e.get('channel', ''),
            'view_count': str(e.get('view_count', '')) if e.get('view_count') else '',
            'thumbnail_url': f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg',
        })
    return _response(results, 'ytdlp')


# ---------------------------------------------------------------------------
# Estratégia 3 — ChromeDriver / Selenium (último recurso, protegido)
# ---------------------------------------------------------------------------

def _search_selenium(query: str, limit: int = 20) -> dict[str, Any]:
    try:
        from bs4 import BeautifulSoup  # noqa: PLC0415

        from music.services.chromedriver import create_chrome_driver  # noqa: PLC0415
    except (ImportError, FileNotFoundError) as exc:
        raise RuntimeError(f'Selenium indisponível no ambiente: {exc}') from exc

    driver = None
    try:
        driver = create_chrome_driver()
    except Exception as exc:
        raise RuntimeError(
            f'Não foi possível iniciar o ChromeDriver ({exc}). '
            'Instale o binário do Chrome/Chromium ou confie nas camadas anteriores.'
        ) from exc
    try:
        search_url = f'{RESULTS_PAGE}?search_query={urllib.parse.quote(query)}'
        driver.get(search_url)
        import time  # noqa: PLC0415
        time.sleep(3)
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        results = []
        for item in soup.select('ytd-video-renderer')[:limit]:
            try:
                title_tag = item.select_one('a#video-title')
                title = (
                    title_tag['title']
                    if title_tag and title_tag.has_attr('title')
                    else (title_tag.text.strip() if title_tag else '')
                )
                link = title_tag['href'] if title_tag and title_tag.has_attr('href') else ''
                youtube_id = ''
                if link:
                    parsed = urllib.parse.urlparse(link)
                    qs = urllib.parse.parse_qs(parsed.query)
                    if 'v' in qs:
                        youtube_id = qs['v'][0]
                channel_tag = item.select_one('ytd-channel-name yt-formatted-string a')
                channel_name = channel_tag.text.strip() if channel_tag else ''
                badge = item.select_one(
                    'div.thumbnail-overlay-badge-shape .yt-badge-shape__text'
                )
                duration = badge.text.strip() if badge else ''
                meta = item.select('div#metadata-line span.inline-metadata-item')
                view_count = meta[0].text.strip() if meta else ''
                if youtube_id:
                    results.append({
                        'title': title,
                        'youtube_id': youtube_id,
                        'duration': duration,
                        'channel_name': channel_name,
                        'view_count': view_count,
                        'thumbnail_url': f'https://i.ytimg.com/vi/{youtube_id}/hqdefault.jpg',
                    })
            except Exception:
                continue
        if not results:
            raise RuntimeError('Nenhum resultado Selenium')
        return _response(results, 'selenium')
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# API pública — fallback em cadeia + degradação graciosa
# ---------------------------------------------------------------------------

def youtube_search(query: str, limit: int = 20) -> dict[str, Any]:
    """Busca tentando requests → yt-dlp → Selenium; nunca levanta exceção."""
    strategies = [
        ('requests', _search_requests),
        ('yt-dlp', _search_ytdlp),
        ('selenium', _search_selenium),
    ]
    for name, fn in strategies:
        try:
            data = fn(query, limit)
            if data.get('status') == 'success' and data.get('results'):
                logger.info('YouTube search OK via %s (%d results)', name, len(data['results']))
                return data
        except Exception as exc:
            logger.warning('YouTube search failed via %s: %s', name, exc)
    return unavailable('requests, yt-dlp e selenium falharam; veja os logs')