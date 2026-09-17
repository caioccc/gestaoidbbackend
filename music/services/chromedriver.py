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
    # DOCKER_RUN é verificado ANTES de is_heroku(): quando o deploy no Heroku
    # é feito via Container Registry (heroku.yml -> build: docker:), o Heroku
    # injeta DYNO=1 mesmo assim, mas o binário instalado é o /usr/bin/chromium
    # do Dockerfile — não o pacote "chrome-for-testing" do buildpack clássico.
    if getattr(settings, 'DOCKER_RUN', False):
        return '/usr/bin/chromium'
    if is_heroku():
        return '/app/.chrome-for-testing/chrome-linux64/chrome'
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
    if getattr(settings, 'DOCKER_RUN', False):
        chrome_options.add_argument('--disable-setuid-sandbox')
        chrome_options.binary_location = '/usr/bin/chromium'
        if not os.path.exists(chrome_options.binary_location):
            raise FileNotFoundError(
                'DOCKER_RUN=True mas /usr/bin/chromium não existe na imagem; '
                'confira se o Dockerfile instala os pacotes chromium/chromium-driver.'
            )
    elif is_heroku():
        chrome_options.add_argument('--disable-setuid-sandbox')
        chrome_options.add_argument('--single-process')
        chrome_options.binary_location = '/app/.chrome-for-testing/chrome-linux64/chrome'
        # Só chega aqui em deploy via buildpack clássico (sem Dockerfile).
        # No seu caso (Container Registry) DOCKER_RUN=True cobre o cenário acima.
        if not os.path.exists(chrome_options.binary_location):
            raise FileNotFoundError(
                'Chrome não instalado no dyno Heroku; habilite o buildpack '
                'de Chrome ou deixe as camadas requests/yt-dlp resolverem.'
            )

    try:
        return Chrome(options=chrome_options)
    except Exception:
        logger.warning('Falhou Chrome padrão; tentando webdriver-manager...')
        from webdriver_manager.chrome import ChromeDriverManager  # noqa: PLC0415
        from selenium.webdriver.chrome.service import Service  # noqa: PLC0415

        service = Service(ChromeDriverManager().install())
        return Chrome(service=service, options=chrome_options)