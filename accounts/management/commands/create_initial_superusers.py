from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

User = get_user_model()

class Command(BaseCommand):
    help = "Cria superusuários iniciais para desenvolvimento e moderação de forma idempotente."

    def handle(self, *args, **options):
        initial_users = [
            {
                "email": "admin@gmail.com",
                "password": "admin",
                "name": "Administrador do Sistema",
            },
            {
                "email": "caiomarinho8@gmail.com",
                "password": "Admin123!",
                "name": "Caio Barbosa (Admin/Aprovador)",
            },
            {
                "email": "glayds@idb.org.br",  # Pode ajustar o email desejado para o Glayds
                "password": "Admin123!",
                "name": "Glayds (Aprovador Nacional)",
            },
        ]

        for user_data in initial_users:
            email = user_data["email"]
            password = user_data["password"]
            name = user_data["name"]

            if User.objects.filter(email=email).exists():
                self.stdout.write(
                    self.style.WARNING(f"[INFO] Usuário '{email}' já existe. Pulando criação.")
                )
            else:
                user = User.objects.create_superuser(
                    email=email,
                    password=password,
                    name=name,
                )
                self.stdout.write(
                    self.style.SUCCESS(f"[OK] Superusuário '{email}' criado com sucesso!")
                )