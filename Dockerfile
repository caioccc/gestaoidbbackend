# ------------------------------------------------------------------
# Eclésia IDB - Backend (Django + DRF + Gunicorn)
# Base: Python 3.11 slim + libs de sistema para WeasyPrint/xhtml2pdf
# ------------------------------------------------------------------
FROM python:3.11-slim

# Impede a criação de __pycache__, desliga output do Python, força UTF-8
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=UTF-8 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

WORKDIR /app

# Bibliotecas do sistema:
#  - pango/cairo/gdk-pixbuf: renderização de PDFs (WeasyPrint)
#  - curl + postgresql-client: healthcheck e ferramentas úteis
#  - build-essential/libpq-dev: compilação de dependências Python (psycopg2, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libcairo2 \
    libxml2 \
    libffi-dev \
    shared-mime-info \
    build-essential \
    libpq-dev \
    postgresql-client \
    chromium \
    chromium-driver \
    fonts-liberation \
    libnss3 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libx11-xcb1 \
    libxss1 \
    libasound2 \
    libgbm1 \
    && rm -rf /var/lib/apt/lists/*

# Sinaliza para o app (music/services/chromedriver.py) que o Chromium do
# sistema está em /usr/bin/chromium, independentemente de o dyno ter DYNO=1
# (o Heroku injeta DYNO mesmo em deploy via Container Registry).
ENV DOCKER_RUN=True

# Instala as dependências Python (copia apenas o requirements p/ aproveitar cache)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copia o código da aplicação
COPY . .

# Coleta os arquivos estáticos (para servir em produção)
RUN mkdir -p /app/staticfiles \
    && python manage.py collectstatic --noinput || true

# Expõe a porta do Django/Gunicorn
EXPOSE 8000

# Comando padrão: roda migrations, cria superusuários iniciais e sobe o Gunicorn.
# No Heroku a porta é injetada via $PORT; local/Docker usa 8000.
CMD ["sh", "-c", "python manage.py migrate --noinput \
    && python manage.py create_initial_superusers \
    && gunicorn core.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 2 --threads 2 --timeout 120"]