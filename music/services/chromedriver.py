"""ChromeDriver headless com estratégia adaptável (Heroku/Docker/local).

Prefere `webdriver-manager` no ambiente local; caminhos fixos no Docker/Heroku.
"""

import logging
import os

from django.conf import settings

logger = logging.getLogger(__name__)


def is_heroku() -> bool:
    return bool(os.environ.get('DYNO'))


def get_chrome_binary() -> str | None:
    if is_heroku():
        return '/app/.chrome-for-testing/chrome-linux64/chrome'
    if getattr(settings, 'DOCKER_RUN', False):
        return '/usr/bin/chromium'
    return None


def create_chrome_driver():
    """Cria uma instância do ChromeDriver configurada para o ambiente."""
    from selenium.webdriver.chrome.options import Options  # noqa: PLC0415
    from selenium.webdriver import Chrome  # noqa: PLC0415

    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--window-size=1920,1080')
    chrome_options.add_argument(
        '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
    )
    if is_heroku():
        chrome_options.add_argument('--disable-setuid-sandbox')
        chrome_options.add_argument('--single-process')
        chrome_options.binary_location = '/app/.chrome-for-testing/chrome-linux64/chrome'
        # No Heroku, se o binário do Chrome não foi instalado no build
        # (buildpack "chrome-for-testing" / chromedriver via heroku.yml),
        # falha rápido em vez de tentar baixar o driver em runtime (~150MB).
        if not os.path.exists(chrome_options.binary_location):
            raise FileNotFoundError(
                'Chrome não instalado no dyno Heroku; habilite o buildpack '
                'de Chrome ou deixe as camadas requests/yt-dlp resolverem.'
            )
    elif getattr(settings, 'DOCKER_RUN', False):
        chrome_options.binary_location = '/usr/bin/chromium'

    try:
        return Chrome(options=chrome_options)
    except Exception:
        logger.warning('Falhou Chrome padrão; tentando webdriver-manager...')
        from webdriver_manager.chrome import ChromeDriverManager  # noqa: PLC0415
        from selenium.webdriver.chrome.service import Service  # noqa: PLC0415

        service = Service(ChromeDriverManager().install())
        return Chrome(service=service, options=chrome_options)