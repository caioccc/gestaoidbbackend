from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers

from . import services
from .models import Church

User = get_user_model()


class ChurchSerializer(serializers.ModelSerializer):
    """Serializa a congregação para listagem/consulta."""

    class Meta:
        model = Church
        fields = [
            'id', 'name', 'pastor_name', 'treasurer_name', 'phone',
            'cep', 'street', 'number', 'neighborhood', 'city', 'state',
            'latitude', 'longitude', 'status', 'created_at', 'updated_at',
            'pastoral_prebenda_percent',
        ]
        read_only_fields = ['id', 'status', 'created_at', 'updated_at']


class ChurchProfileSerializer(serializers.ModelSerializer):
    """Consulta e atualização dos dados da congregação (perfil)."""

    responsible_email = serializers.EmailField(
        source='user_account.email', read_only=True
    )

    class Meta:
        model = Church
        fields = [
            'id', 'name', 'pastor_name', 'treasurer_name', 'phone',
            'cep', 'street', 'number', 'neighborhood', 'city', 'state',
            'latitude', 'longitude', 'pastoral_prebenda_percent',
            'responsible_email',
        ]
        read_only_fields = ['id']

    def validate_state(self, value):
        value = (value or '').upper()
        if len(value) != 2:
            raise serializers.ValidationError(
                'A UF deve conter exatamente 2 letras.'
            )
        return value


class RegisterSerializer(serializers.Serializer):
    """Cadastro público da congregação.

    Cria a Church como PENDING e o User como inativo (aguarda moderação).
    """
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=6)
    name = serializers.CharField(max_length=150)
    church_name = serializers.CharField(max_length=200)
    pastor_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    treasurer_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    cep = serializers.CharField(max_length=9, required=False, allow_blank=True)
    street = serializers.CharField(max_length=200, required=False, allow_blank=True)
    number = serializers.CharField(max_length=20, required=False, allow_blank=True)
    neighborhood = serializers.CharField(max_length=100, required=False, allow_blank=True)
    city = serializers.CharField(max_length=100)
    state = serializers.CharField(max_length=2)
    latitude = serializers.FloatField(required=False, allow_null=True)
    longitude = serializers.FloatField(required=False, allow_null=True)

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError(
                'Já existe uma conta cadastrada com este e-mail.'
            )
        return value.lower()

    def validate_state(self, value):
        value = (value or '').upper()
        if len(value) != 2:
            raise serializers.ValidationError(
                'A UF deve conter exatamente 2 letras.'
            )
        return value

    @transaction.atomic
    def create(self, validated_data):
        church_data = {
            'name': validated_data['church_name'],
            'pastor_name': validated_data.get('pastor_name', ''),
            'treasurer_name': validated_data.get('treasurer_name', ''),
            'phone': validated_data.get('phone', ''),
            'cep': validated_data.get('cep', ''),
            'street': validated_data.get('street', ''),
            'number': validated_data.get('number', ''),
            'neighborhood': validated_data.get('neighborhood', ''),
            'city': validated_data['city'],
            'state': validated_data['state'],
            'latitude': validated_data.get('latitude'),
            'longitude': validated_data.get('longitude'),
        }
        church = Church.objects.create(status='PENDING', **church_data)

        user = User.objects.create_user(
            email=validated_data['email'],
            password=validated_data['password'],
            name=validated_data['name'],
            church=church,
            is_active=False,
        )

        # Envia as credenciais (email + senha) no momento do cadastro.
        services.send_registration_credentials(
            email=user.email,
            password=validated_data['password'],
            church_name=church.name,
            account_name=user.name,
        )

        return {'user': user, 'church': church}


class UserSerializer(serializers.ModelSerializer):
    """Payload do usuário para login e sessão."""
    church = ChurchSerializer(read_only=True)

    class Meta:
        model = User
        fields = ['id', 'email', 'name', 'is_staff', 'is_active', 'church']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # A congregação pendente pode não ter dados relevantes ainda.
        return data


class PendingChurchSerializer(serializers.ModelSerializer):
    user = UserSerializer(source='user_account', read_only=True)

    class Meta:
        model = Church
        fields = [
            'id', 'name', 'pastor_name', 'treasurer_name', 'phone',
            'cep', 'street', 'number', 'neighborhood', 'city', 'state',
            'latitude', 'longitude', 'status', 'created_at', 'user',
        ]
