"""Views de infraestrutura do projeto (fora do escopo de negócio)."""
from django.http import JsonResponse


def healthz(request):
    """Healthcheck leve (sem banco/auth) usado por Docker/Heroku."""
    return JsonResponse({'status': 'ok'})
