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


def create_chrome_driver():
    """Cria uma instância do ChromeDriver configurada para o ambiente Docker/Heroku."""
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
    chrome_options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )

    if driver_path:
        try:
            service = Service(executable_path=driver_path)
            return Chrome(service=service, options=chrome_options)
        except Exception as err:
            logger.warning(
                "Falha ao iniciar ChromeDriver do sistema (%s): %s. Tentando webdriver-manager...",
                driver_path,
                err,
            )

    try:
        from webdriver_manager.chrome import ChromeDriverManager

        service = Service(ChromeDriverManager().install())
        return Chrome(service=service, options=chrome_options)
    except Exception as err:
        logger.error("Falha ao inicializar ChromeDriver via webdriver-manager: %s", err)
        raise