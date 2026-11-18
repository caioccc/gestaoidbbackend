import logging
import os
import shutil
from django.conf import settings

logger = logging.getLogger(__name__)


def is_heroku() -> bool:
    return bool(os.environ.get("DYNO"))


def get_chrome_paths() -> tuple[str | None, str | None]:
    """Detecta automaticamente os binários do Chromium e ChromeDriver."""
    localappdata = os.path.expandvars(r"%LOCALAPPDATA%")
    candidates_browser = [
        getattr(settings, "CHROME_BIN", None),
        os.getenv("CHROME_BIN"),
        # Linux (produção / Docker / Heroku)
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/app/.chrome-for-testing/chrome-linux64/chrome",
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
        os.path.join(localappdata, "Google", "Chrome", "Application", "chrome.exe"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
    ]

    candidates_driver = [
        getattr(settings, "CHROMEDRIVER_PATH", None),
        os.getenv("CHROMEDRIVER_PATH"),
        "/usr/bin/chromedriver",
        "/app/.chrome-for-testing/chromedriver-linux64/chromedriver",
        shutil.which("chromedriver"),
        shutil.which("chromedriver.exe"),
    ]

    browser_bin = next((p for p in candidates_browser if p and os.path.exists(p)), None)
    driver_bin = next((p for p in candidates_driver if p and os.path.exists(p)), None)

    return browser_bin, driver_bin


def _apply_stealth(driver):
    """Remove o sinalizador `navigator.webdriver` (detecção de automação).

    Best-effort: falha silenciosamente se o ChromeDriver não suportar
    `execute_cdp_cmd` (versões antigas) — sem quebrar a inicialização.
    """
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": (
                    "Object.defineProperty(navigator, 'webdriver', "
                    "{get: () => undefined});"
                )
            },
        )
    except Exception:  # noqa: BLE001
        logger.debug("navigator.webdriver não pôde ser mascarado (ChromeDriver antigo).")


def _setting(name: str, default: str = '') -> str:
    """Lê a configuração do ambiente (prioridade) ou do Django settings."""
    value = os.getenv(name)
    if value not in (None, ''):
        return value
    value = getattr(settings, name, default)
    return str(value) if value is not None else default


def _bool_setting(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        value = getattr(settings, name, default)
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _build_remote_options():
    """Opções para o ChromeDriver do HOST abrir o Chrome com o perfil real.

    O perfil dedicado fica no Windows (cookies/cf_clearance inclusos). Não
    sobrescrevemos o User-Agent: o `cf_clearance` do Cloudflare é vinculado ao
    UA/IP que resolveu o challenge, então um UA fixo diferente invalidaria a
    sessão do perfil.
    """
    from selenium.webdriver.chrome.options import Options

    options = Options()

    browser_path = _setting('CHROME_HOST_BIN')
    if browser_path:
        options.binary_location = browser_path

    user_data_dir = _setting('CHROME_HOST_USER_DATA_DIR')
    if user_data_dir:
        options.add_argument(f'--user-data-dir={user_data_dir}')

    profile_directory = _setting('CHROME_HOST_PROFILE_DIRECTORY')
    if profile_directory:
        options.add_argument(f'--profile-directory={profile_directory}')

    if _bool_setting('CHROME_HEADLESS'):
        options.add_argument('--headless=new')

    options.add_argument('--no-first-run')
    options.add_argument('--no-default-browser-check')
    options.add_argument('--start-minimized')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option('excludeSwitches', ['enable-automation'])
    options.add_experimental_option('useAutomationExtension', False)
    return options


def _create_remote_driver(remote_url: str):
    """Conecta no ChromeDriver do host (Selenium Remote) e retorna o driver."""
    from selenium import webdriver

    options = _build_remote_options()
    logger.info(
        'ChromeDriver remoto: conectando em %s (bin=%s perfil=%s headless=%s).',
        remote_url,
        _setting('CHROME_HOST_BIN') or '-',
        _setting('CHROME_HOST_PROFILE_DIRECTORY') or '-',
        _bool_setting('CHROME_HEADLESS'),
    )
    driver = webdriver.Remote(command_executor=remote_url, options=options)
    _apply_stealth(driver)
    return driver


def create_chrome_driver():
    """Cria uma instância do ChromeDriver configurada para o ambiente Docker/Heroku."""
    remote_url = _setting('SELENIUM_REMOTE_URL').strip()
    if remote_url:
        return _create_remote_driver(remote_url)

    from selenium.webdriver import Chrome
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    browser_path, driver_path = get_chrome_paths()

    if not browser_path:
        raise FileNotFoundError(
            "Nenhum executável de Chrome/Chromium encontrado no ambiente "
            "(procurou em /usr/bin/chromium, Google Chrome no Windows e PATH). "
            "Instale o Google Chrome/Chromium e tente novamente."
        )

    chrome_options = Options()
    chrome_options.binary_location = browser_path

    # Flags essenciais para rodar headless em containers Linux
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-setuid-sandbox")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--lang=pt-BR")
    # Reduz a detecção de automação (bloqueio do Cloudflare/WAF)
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)

    if driver_path:
        try:
            service = Service(executable_path=driver_path)
            driver = Chrome(service=service, options=chrome_options)
            _apply_stealth(driver)
            return driver
        except Exception as err:
            logger.warning(
                "Falha ao iniciar ChromeDriver do sistema (%s): %s. Tentando webdriver-manager...",
                driver_path,
                err,
            )

    try:
        from webdriver_manager.chrome import ChromeDriverManager

        service = Service(ChromeDriverManager().install())
        driver = Chrome(service=service, options=chrome_options)
        _apply_stealth(driver)
        return driver
    except Exception as err:
        logger.error("Falha ao inicializar ChromeDriver via webdriver-manager: %s", err)
        raise