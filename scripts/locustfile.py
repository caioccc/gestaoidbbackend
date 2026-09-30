import json
import os
import random
from datetime import date

from locust import HttpUser, SequentialTaskSet, between, task
from locust.exception import StopUser
import random

USER_ACCOUNTS = [
    {"email": "professor@gmail.com", "password": "Admin123!"},
    {"email": "fulano@gmail.com", "password": "Admin123!"},
    {"email": "intercessor@gmail.com", "password": "Admin123!"},
    {"email": "lider@gmail.com", "password": "Admin123!"},
    {"email": "musico@gmail.com", "password": "Admin123!"},
    {"email": "pastor@gmail.com", "password": "Admin123!"},
    {"email": "secretario@gmail.com", "password": "Admin123!"},
]


def load_accounts():
    """Carrega contas de teste sem armazenar senhas no repositorio."""
    account = random.choice(USER_ACCOUNTS)
    return [{"email": account["email"], "password": account["password"]}]


USER_ACCOUNTS = load_accounts()
ENABLE_WRITES = os.getenv("LOCUST_ENABLE_WRITES", "0") == "1"
FALLBACK_CHURCH_ID = os.getenv("LOCUST_CHURCH_ID")
TEST_YEAR = os.getenv("LOCUST_YEAR", str(date.today().year))
CALENDAR_HASH = os.getenv("LOCUST_CALENDAR_HASH")

class FluxoCompletoIgreja(SequentialTaskSet):
    def on_start(self):
        """Seleciona uma conta do pool, autentica e identifica a igreja."""
        self.account = random.choice(USER_ACCOUNTS)
        self.token = None

        with self.client.post(
            "/api/accounts/login/",
            json={
                "email": self.account["email"],
                "password": self.account["password"],
            },
            name="[POST] /api/accounts/login/",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"login retornou HTTP {response.status_code}")
                raise StopUser()

            try:
                data = response.json()
            except ValueError:
                response.failure("login retornou JSON invalido")
                raise StopUser()

            self.token = data.get("access")
            if not self.token:
                response.failure("login nao retornou access token")
                raise StopUser()

            self.client.headers.update({"Authorization": f"Bearer {self.token}"})

            church = (data.get("user") or {}).get("church") or {}
            self.church_id = church.get("id") or FALLBACK_CHURCH_ID
            if not self.church_id:
                response.failure("login nao retornou a igreja do usuario")
                raise StopUser()

    @task(3)
    def navegar_dashboard_e_membros(self):
        """Simula consultas de dashboard, membros e cultos."""
        self.client.get(
            f"/api/finance/dashboard/summary/?year={TEST_YEAR}",
            name="[GET] /dashboard/summary",
        )
        self.client.get(
            f"/api/accounts/churches/{self.church_id}/members/",
            name="[GET] /members",
        )
        self.client.get("/api/accounts/cultos/", name="[GET] /cultos")

    @task(2)
    def operacao_escola_biblica_e_atas(self):
        """Simula consultas de turmas de EBD e atas."""
        self.client.get("/api/accounts/sunday-school/classes/", name="[GET] /ebd/classes")
        self.client.get("/api/accounts/minutes/", name="[GET] /atas")

    @task(1)
    def criar_membro_teste(self):
        """Opcionalmente mede a escrita de membros, desativada por padrao."""
        if not ENABLE_WRITES:
            return
        idx = random.randint(1000, 9999)
        payload = {
            "name": f"Membro Teste {idx}",
            "email": f"membro_{idx}@igrejateste.com",
            "phone": "83999999999",
            "status": "active"
        }
        self.client.post(
            f"/api/accounts/churches/{self.church_id}/members/",
            json=payload,
            name="[POST] /members (Criar)",
        )

    @task(1)
    def lancar_financeiro(self):
        """Opcionalmente mede a escrita financeira, desativada por padrao."""
        if not ENABLE_WRITES:
            return
        payload = {
            "service_description": "Oferta / Dizimo Teste de Carga",
            "amount": round(random.uniform(10.0, 500.0), 2),
            "date": date.today().isoformat(),
        }
        self.client.post(
            "/api/finance/entries/",
            json=payload,
            name="[POST] /finance/entries (Criar)",
        )

    @task(2)
    def acessar_paginas_publicas(self):
        """Simula musicas e, quando configurado, o calendario publico."""
        self.client.get("/api/music/songs/", name="[GET] /public/songs")
        if CALENDAR_HASH:
            self.client.get(
                f"/api/finance/public/calendar/{CALENDAR_HASH}/",
                name="[GET] /public/calendar",
            )


class UsuarioVirtualIgreja(HttpUser):
    # Simula o 'Think Time' humano: cada usuário espera entre 2 e 5 segundos antes da próxima ação
    wait_time = between(2, 5)
    tasks = [FluxoCompletoIgreja]