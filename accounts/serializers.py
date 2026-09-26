import os
import re
from datetime import date
from decimal import Decimal
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import serializers

from . import services
from .models import (
    AccountingCategory,
    CertificateTemplate,
    Church,
    ChurchMembership,
    ChurchMinutes,
    ChurchPublicLink,
    EcclesiasticalCertificate,
    Loan,
    MaterialItem,
    Member,
    MemberDocument,
    MemberRelative,
    MemberSubmission,
    MemberTransfer,
    MessageTemplate,
    MinistryArea,
    StorageLocation,
    WorshipService,
    GrowthGroup,
    PastoralVisit,
    PrayerRequest,
    SundaySchoolClass,
    SundaySchoolEnrollment,
    SundaySchoolSession,
    SundaySchoolAttendance,
)

User = get_user_model()


class ChurchLogoField(serializers.Field):
    """Campo de logo da igreja: aceita data URL (base64) e devolve a URL."""

    def to_internal_value(self, data):
        if data in (None, '', False):
            return None
        uploaded = services.data_url_to_file(data, 'church_logo.png')
        if uploaded is None:
            raise serializers.ValidationError('Logo inválido.')
        return uploaded

    def to_representation(self, value):
        return services.cloudinary_url(value)


class ChurchSerializer(serializers.ModelSerializer):
    """Serializa a igreja para listagem/consulta."""

    church_type_display = serializers.CharField(
        source='get_church_type_display', read_only=True,
    )
    responsible_user_id = serializers.IntegerField(
        source='responsible_user.id', read_only=True, allow_null=True,
    )
    logo = ChurchLogoField(required=False, allow_null=True)

    class Meta:
        model = Church
        fields = [
            'id', 'name', 'church_type', 'church_type_display',
            'logo',
            'parent_church', 'is_approved', 'accounting_category',
            'responsible_user_id',
            'pastor_name', 'treasurer_name', 'phone',
            'cep', 'street', 'number', 'neighborhood', 'city', 'state',
            'latitude', 'longitude', 'status', 'created_at', 'updated_at',
            'pastoral_prebenda_percent',
        ]
        read_only_fields = [
            'id', 'status', 'is_approved', 'created_at', 'updated_at',
        ]


class ChurchCreateSerializer(ChurchSerializer):
    """Criação direta de congregação pela Sede (ou ADMIN).

    A Categoria Contábil é obrigatória e a congregação já nasce aprovada,
    sem passar pela fila de /approvals. Opcionalmente vincula o responsável
    local (Pastor(a)/Tesoureiro(a)/Secretário(a)).
    """

    responsible_user = serializers.DictField(
        required=False, write_only=True,
        help_text='{email, name, role} — responsável local (opcional).',
    )

    class Meta(ChurchSerializer.Meta):
        fields = ChurchSerializer.Meta.fields + ['responsible_user']

    def validate(self, attrs):
        church_type = attrs.get('church_type')
        is_congregation = (
            church_type == Church.ChurchType.CONGREGATION
            or church_type is None
        )
        if is_congregation and not attrs.get('accounting_category'):
            raise serializers.ValidationError({
                'accounting_category': 'Informe a categoria contábil do repasse.',
            })
        return attrs

    def validate_accounting_category(self, value):
        normalized = services.persist_accounting_category(value)
        if not normalized:
            raise serializers.ValidationError(
                'Informe a categoria contábil do repasse.'
            )
        return normalized

    def validate_responsible_user(self, value):
        user_id = value.get('user_id')
        email = (value.get('email') or '').strip().lower()
        name = (value.get('name') or '').strip()
        role = (value.get('role') or '').strip().upper()
        valid_roles = (
            ChurchMembership.Role.PASTOR,
            ChurchMembership.Role.TESOUREIRO,
            ChurchMembership.Role.SECRETARIA,
        )
        if role and role not in valid_roles:
            raise serializers.ValidationError(
                'O papel deve ser PASTOR, TESOUREIRO ou SECRETARIA.'
            )
        if user_id:
            user = User.objects.filter(pk=user_id).first()
            if user is None:
                raise serializers.ValidationError(
                    'Usuário responsável não encontrado.'
                )
            if not role:
                raise serializers.ValidationError(
                    'Informe o papel do usuário responsável.'
                )
            return {
                'user_id': user.id,
                'role': role,
                'name': user.name,
            }
        if not email:
            raise serializers.ValidationError(
                'Informe o e-mail do primeiro usuário.'
            )
        if role not in valid_roles:
            raise serializers.ValidationError(
                'O papel deve ser PASTOR, TESOUREIRO ou SECRETARIA.'
            )
        return {'email': email, 'name': name, 'role': role}


class ChurchUpdateSerializer(ChurchSerializer):
    """Edição de congregação pela Sede: dados gerais + reclassificação contábil.

    Bloqueia a alteração de church_type/parent_church/status após a criação
    (a congregação pertence à mesma Sede) e persiste nova categoria contábil.
    Aceita a troca opcional do usuário responsável ({user_id, role}).
    """

    responsible_user = serializers.DictField(
        required=False, write_only=True,
        help_text='{user_id, role} — novo usuário responsável (opcional).',
    )

    class Meta(ChurchSerializer.Meta):
        fields = ChurchSerializer.Meta.fields + ['responsible_user']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ('church_type', 'parent_church', 'is_approved', 'status'):
            self.fields[field_name].read_only = True

    def validate_responsible_user(self, value):
        user_id = value.get('user_id')
        role = (value.get('role') or '').strip().upper()
        valid_roles = (
            ChurchMembership.Role.PASTOR,
            ChurchMembership.Role.TESOUREIRO,
            ChurchMembership.Role.SECRETARIA,
        )
        if not user_id:
            raise serializers.ValidationError(
                'Informe o usuário responsável (user_id).'
            )
        user = User.objects.filter(pk=user_id).first()
        if user is None:
            raise serializers.ValidationError(
                'Usuário responsável não encontrado.'
            )
        if role not in valid_roles:
            raise serializers.ValidationError(
                'O papel deve ser PASTOR, TESOUREIRO ou SECRETARIA.'
            )
        return {'user_id': user.id, 'role': role}

    def validate_accounting_category(self, value):
        normalized = services.persist_accounting_category(value)
        if not normalized:
            raise serializers.ValidationError(
                'Informe a categoria contábil do repasse.'
            )
        return normalized


class ChurchProfileSerializer(serializers.ModelSerializer):
    """Consulta e atualização dos dados da congregação (perfil)."""

    responsible_email = serializers.EmailField(
        source='responsible_user.email', read_only=True
    )
    church_type_display = serializers.CharField(
        source='get_church_type_display', read_only=True,
    )
    logo = ChurchLogoField(required=False, allow_null=True)

    class Meta:
        model = Church
        fields = [
            'id', 'name', 'church_type', 'church_type_display',
            'parent_church', 'is_approved', 'accounting_category',
            'logo',
            'pastor_name', 'treasurer_name', 'phone',
            'cep', 'street', 'number', 'neighborhood', 'city', 'state',
            'latitude', 'longitude', 'pastoral_prebenda_percent',
            'card_primary_color', 'card_secondary_color', 'card_valid_until',
            'card_front_phrase', 'card_back_phrase', 'card_theme',
            'responsible_email',
        ]
        read_only_fields = ['id', 'church_type', 'parent_church', 'is_approved']

    def validate_state(self, value):
        value = (value or '').upper()
        if len(value) != 2:
            raise serializers.ValidationError(
                'A UF deve conter exatamente 2 letras.'
            )
        return value

    def _validate_hex_color(self, value):
        value = (value or '').strip().upper()
        if value and not re.fullmatch(r'#[0-9A-F]{6}', value):
            raise serializers.ValidationError(
                'Cor inválida. Use o formato #RRGGBB.'
            )
        return value

    def validate_card_primary_color(self, value):
        return self._validate_hex_color(value)

    def validate_card_secondary_color(self, value):
        return self._validate_hex_color(value)

    def validate_card_valid_until(self, value):
        if value is not None and value.year > date.today().year + 10:
            raise serializers.ValidationError(
                'A validade máximo permitida é de 10 anos.'
            )
        return value


class RegisterSerializer(serializers.Serializer):
    """Cadastro público de uma igreja (auto-cadastro com aprovação).

    - Sede (INDEPENDENT): PENDING + is_approved=False, aguarda aprovação de um
      administrador da IDB.
    - Congregação (CONGREGATION): PENDING + is_approved=False, aguarda a
      aprovação da Igreja Sede (parent_church obrigatória).
    Cria também o User ativo e o ChurchMembership com o papel escolhido.
    """
    ROLE_CHOICES = [
        ChurchMembership.Role.PASTOR,
        ChurchMembership.Role.TESOUREIRO,
    ]
    CHURCH_TYPE_CHOICES = [
        Church.ChurchType.INDEPENDENT,
        Church.ChurchType.CONGREGATION,
    ]

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=6)
    name = serializers.CharField(max_length=150)
    church_name = serializers.CharField(max_length=200)
    church_type = serializers.ChoiceField(choices=CHURCH_TYPE_CHOICES, required=True)
    parent_church = serializers.IntegerField(required=False, allow_null=True)
    role = serializers.ChoiceField(choices=ROLE_CHOICES, required=True)
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

    def validate_parent_church(self, value):
        if value is None:
            return None
        church = Church.objects.filter(pk=value).first()
        if church is None or not church.is_sede():
            raise serializers.ValidationError(
                'A igreja Sede informada é inválida.'
            )
        if church.status != 'ACTIVE':
            raise serializers.ValidationError(
                'A igreja Sede informada não está ativa.'
            )
        return church

    def validate(self, attrs):
        church_type = attrs.get('church_type')
        parent_church = attrs.get('parent_church')
        if church_type == Church.ChurchType.CONGREGATION:
            if parent_church is None:
                raise serializers.ValidationError({
                    'parent_church': [
                        'Selecione a Igreja Sede à qual a congregação pertencerá.'
                    ]
                })
        else:
            attrs['parent_church'] = None
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        parent_church = validated_data.get('parent_church')
        role = validated_data['role']
        church_type = validated_data['church_type']

        church = Church.objects.create(
            status='PENDING',
            is_approved=False,
            church_type=church_type,
            parent_church=parent_church,
            name=validated_data['church_name'],
            pastor_name=validated_data.get('pastor_name', ''),
            treasurer_name=validated_data.get('treasurer_name', ''),
            phone=validated_data.get('phone', ''),
            cep=validated_data.get('cep', ''),
            street=validated_data.get('street', ''),
            number=validated_data.get('number', ''),
            neighborhood=validated_data.get('neighborhood', ''),
            city=validated_data['city'],
            state=validated_data['state'],
            latitude=validated_data.get('latitude'),
            longitude=validated_data.get('longitude'),
        )

        user = User.objects.create_user(
            email=validated_data['email'],
            password=validated_data['password'],
            name=validated_data['name'],
            church=church,
            # Ativo desde o cadastro: o bloqueio de acesso até a aprovação é
            # feito no login, pela aprovação da igreja (is_approved=False -> 403).
            is_active=True,
        )
        ChurchMembership.objects.create(
            user=user, church=church, role=role,
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
    role = serializers.CharField(source='active_role', read_only=True)
    role_display = serializers.SerializerMethodField()
    can_manage_churches = serializers.BooleanField(read_only=True)
    can_approve_congregations = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'email', 'name', 'is_staff', 'is_active', 'church', 'role',
            'role_display', 'can_manage_churches', 'can_approve_congregations',
        ]

    def get_role_display(self, instance):
        role = instance.active_role
        if not role:
            return 'Admin' if (instance.is_staff or instance.is_superuser) else ''
        choices = dict(ChurchMembership.Role.choices)
        return choices.get(role, role)


class PendingChurchSerializer(serializers.ModelSerializer):
    user = UserSerializer(source='responsible_user', read_only=True)
    church_type_display = serializers.CharField(
        source='get_church_type_display', read_only=True,
    )
    parent_church_name = serializers.SerializerMethodField()

    class Meta:
        model = Church
        fields = [
            'id', 'name', 'church_type', 'church_type_display',
            'parent_church', 'parent_church_name',
            'is_approved', 'accounting_category',
            'pastor_name', 'treasurer_name', 'phone',
            'cep', 'street', 'number', 'neighborhood', 'city', 'state',
            'latitude', 'longitude', 'status', 'created_at', 'user',
        ]

    def get_parent_church_name(self, obj):
        if obj.parent_church_id is None:
            return None
        return obj.parent_church.name


class PendingCongregationSerializer(PendingChurchSerializer):
    """Solicitação de vínculo (congregação pendente) com contato do solicitante."""

    requester_role = serializers.SerializerMethodField()

    class Meta(PendingChurchSerializer.Meta):
        fields = PendingChurchSerializer.Meta.fields + ['requester_role']

    def get_requester_role(self, obj):
        user = obj.responsible_user
        if user is None:
            return None
        membership = user.church_memberships.filter(church=obj).first()
        if membership is None:
            return None
        choices = dict(ChurchMembership.Role.choices)
        return {
            'role': membership.role,
            'role_display': choices.get(membership.role, membership.role),
        }


class ChurchMembershipSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source='user.id', read_only=True)
    user_email = serializers.EmailField(source='user.email', read_only=True)
    user_name = serializers.CharField(source='user.name', read_only=True)
    user_is_active = serializers.BooleanField(source='user.is_active', read_only=True)
    role_display = serializers.CharField(source='get_role_display', read_only=True)

    class Meta:
        model = ChurchMembership
        fields = [
            'id', 'user_id', 'user_email', 'user_name', 'user_is_active',
            'role', 'role_display', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']


def is_strong_password(password) -> bool:
    """Mínimo 8 caracteres, ao menos 1 número e 1 caractere especial."""
    return (
        isinstance(password, str)
        and len(password) >= 8
        and any(c.isdigit() for c in password)
        and any(not c.isalnum() for c in password)
    )


class AddChurchUserSerializer(serializers.Serializer):
    """Vincula/cria um usuário em uma igreja com um papel e senha inicial."""
    email = serializers.EmailField()
    name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    role = serializers.ChoiceField(choices=ChurchMembership.Role.choices)
    password = serializers.CharField(write_only=True)
    password2 = serializers.CharField(write_only=True)

    def validate_password(self, value):
        if not is_strong_password(value):
            raise serializers.ValidationError(
                'A senha deve ter ao menos 8 caracteres e conter ao menos 1 '
                'número e 1 caractere especial.'
            )
        return value

    def validate(self, attrs):
        if attrs.get('password') != attrs.get('password2'):
            raise serializers.ValidationError({
                'password2': ['As senhas não coincidem.'],
            })
        return attrs


class MemberPhotoField(serializers.Field):
    """Campo de foto de membro: aceita data URL (base64) e devolve a URL."""

    def to_internal_value(self, data):
        if data in (None, '', False):
            return None
        uploaded = services.data_url_to_file(data, 'member_photo.png')
        if uploaded is None:
            raise serializers.ValidationError('Imagem de perfil inválida.')
        return uploaded

    def to_representation(self, value):
        return services.cloudinary_url(value)


class MinistryAreaSerializer(serializers.ModelSerializer):
    class Meta:
        model = MinistryArea
        fields = ['id', 'church', 'name', 'created_at']
        read_only_fields = ['id', 'church', 'created_at']

    def validate(self, attrs):
        name = (attrs.get('name') or '').strip()
        if not name:
            raise serializers.ValidationError({'name': ['Informe o nome da área.']})
        attrs['name'] = name
        church = self.context.get('church')
        if church is None:
            request = self.context.get('request')
            church = getattr(getattr(request, 'user', None), 'church', None)
        if church is not None:
            existing = MinistryArea.objects.filter(
                church=church, name__iexact=name,
            ).exclude(pk=self.instance.pk if self.instance else None)
            if existing.exists():
                raise serializers.ValidationError(
                    {'name': ['Já existe uma área de atuação com este nome.']}
                )
        return attrs


class StorageLocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = StorageLocation
        fields = ['id', 'church', 'name', 'created_at']
        read_only_fields = ['id', 'church', 'created_at']

    def validate(self, attrs):
        name = (attrs.get('name') or '').strip()
        if not name:
            raise serializers.ValidationError({'name': ['Informe o nome do local.']})
        attrs['name'] = name
        church = self.context.get('church')
        if church is None:
            request = self.context.get('request')
            church = getattr(getattr(request, 'user', None), 'church', None)
        if church is not None:
            existing = StorageLocation.objects.filter(
                church=church, name__iexact=name,
            ).exclude(pk=self.instance.pk if self.instance else None)
            if existing.exists():
                raise serializers.ValidationError(
                    {'name': ['Já existe um local com este nome.']}
                )
        return attrs


class MaterialItemSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(
        source='location.name', read_only=True, default=None,
    )
    location = serializers.PrimaryKeyRelatedField(
        queryset=StorageLocation.objects.all(),
        required=True,
        error_messages={
            'required': 'Selecione o local do material.',
            'null': 'Selecione o local do material.',
        },
    )
    current_loan = serializers.SerializerMethodField()
    photo = serializers.FileField(required=False, allow_null=True)
    manual = serializers.FileField(required=False, allow_null=True)

    class Meta:
        model = MaterialItem
        fields = [
            'id', 'church', 'name', 'description', 'location', 'location_name',
            'photo', 'manual',
            'current_loan', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'church', 'created_at', 'updated_at']

    def get_current_loan(self, obj):
        loan = obj.current_open_loan()
        if loan is None:
            return None
        return {
            'loan_id': loan.id,
            'borrower_display': loan.borrower_display,
            'borrowed_at': loan.borrowed_at.isoformat(),
            'expected_return': loan.expected_return.isoformat(),
        }

    def validate(self, attrs):
        name = (attrs.get('name') or '').strip()
        if not name:
            raise serializers.ValidationError({'name': ['Informe o nome do material.']})
        attrs['name'] = name
        if 'description' in attrs:
            attrs['description'] = (attrs.get('description') or '').strip()
        church = self.context.get('church')
        if church is None:
            request = self.context.get('request')
            church = getattr(getattr(request, 'user', None), 'church', None)
        location = attrs.get('location')
        if (
            location is not None
            and church is not None
            and location.church_id != church.id
        ):
            raise serializers.ValidationError(
                {'location': ['O local selecionado não pertence a esta igreja.']}
            )
        return attrs


class LoanSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source='item.name', read_only=True)
    member_name = serializers.CharField(
        source='member.name', read_only=True, default=None,
    )
    member_phone = serializers.CharField(
        source='member.phone', read_only=True, default=None,
    )
    borrower_display = serializers.CharField(read_only=True)
    contact_phone = serializers.SerializerMethodField()
    whatsapp_url = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Loan
        fields = [
            'id', 'church', 'item', 'item_name', 'member', 'member_name',
            'borrower_name', 'borrower_phone', 'borrower_display',
            'member_phone', 'contact_phone', 'whatsapp_url',
            'borrowed_at', 'expected_return', 'returned_at', 'status', 'notes',
            'created_by_name', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'church', 'returned_at', 'status', 'created_by_name',
            'created_at', 'updated_at',
        ]

    def get_status(self, obj):
        return obj.status_on()

    def get_created_by_name(self, obj):
        return obj.created_by.name if obj.created_by_id else None

    def get_contact_phone(self, obj):
        phone = (obj.member.phone if obj.member_id else obj.borrower_phone or '').strip()
        return phone or None

    def get_whatsapp_url(self, obj):
        phone = self.get_contact_phone(obj)
        if not phone:
            return None
        digits = re.sub(r'\D', '', phone)
        if len(digits) in (10, 11):
            digits = f'55{digits}'
        if len(digits) < 12 or len(digits) > 15:
            return None
        message = 'Olá! Falamos da igreja sobre o material emprestado.'
        return f'https://wa.me/{digits}?text={quote(message)}'

    def _context_church(self):
        church = self.context.get('church')
        if church is None:
            request = self.context.get('request')
            church = getattr(getattr(request, 'user', None), 'church', None)
        return church

    def validate(self, attrs):
        instance = self.instance
        church = self._context_church()

        item = attrs.get('item', getattr(instance, 'item', None) if instance else None)
        member = (
            attrs.get('member')
            if 'member' in attrs
            else (getattr(instance, 'member', None) if instance else None)
        )

        if item is None:
            raise serializers.ValidationError({'item': ['Informe o material emprestado.']})
        if church is not None and item.church_id != church.id:
            raise serializers.ValidationError(
                {'item': ['O material não pertence a esta igreja.']}
            )
        if member is not None and church is not None and member.church_id != church.id:
            raise serializers.ValidationError(
                {'member': ['O membro não pertence a esta igreja.']}
            )

        if instance is None:
            borrower = (attrs.get('borrower_name') or '').strip()
            if attrs.get('member') is None and not borrower:
                raise serializers.ValidationError(
                    {'borrower_name': ['Informe o tomador (membro ou nome) do empréstimo.']}
                )
            attrs['borrower_name'] = borrower
        elif 'borrower_name' in attrs:
            attrs['borrower_name'] = (attrs.get('borrower_name') or '').strip()

        borrowed_at = attrs.get(
            'borrowed_at', getattr(instance, 'borrowed_at', None) if instance else None,
        )
        expected_return = attrs.get(
            'expected_return',
            getattr(instance, 'expected_return', None) if instance else None,
        )
        if borrowed_at and expected_return and expected_return < borrowed_at:
            raise serializers.ValidationError(
                {'expected_return': ['A devolução prevista deve ser maior ou igual à data do empréstimo.']}
            )

        conflict = Loan.objects.filter(
            church=church, item=item, returned_at__isnull=True,
        ).exclude(pk=instance.pk if instance else None)
        if conflict.exists():
            raise serializers.ValidationError(
                {'item': ['Este material já está emprestado e ainda não foi devolvido.']}
            )
        return attrs


class MemberRelativeSerializer(serializers.ModelSerializer):
    class Meta:
        model = MemberRelative
        fields = ['id', 'name', 'kinship', 'birth_date', 'phone']
        read_only_fields = ['id']

    def validate_name(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('Informe o nome do parente.')
        return value


class MemberSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    lifecycle_stage_display = serializers.CharField(
        source='get_lifecycle_stage_display', read_only=True,
    )
    church_entry_display = serializers.SerializerMethodField()
    marital_status_display = serializers.CharField(
        source='get_marital_status_display', read_only=True,
    )
    education_level_display = serializers.CharField(
        source='get_education_level_display', read_only=True,
    )
    card_number = serializers.CharField(read_only=True)
    photo = MemberPhotoField(required=False, allow_null=True)
    ministry_areas = serializers.PrimaryKeyRelatedField(
        queryset=MinistryArea.objects.all(),
        many=True,
        required=False,
    )
    ministry_areas_display = serializers.SerializerMethodField()
    relatives = MemberRelativeSerializer(many=True, required=False)

    class Meta:
        model = Member
        fields = [
            'id', 'church', 'name', 'phone', 'whatsapp_public', 'email', 'birth_date',
            'baptism_date', 'cpf', 'rg', 'born_in_city', 'born_in_state',
            'profession', 'education_level', 'education_level_display',
            'marital_status', 'marital_status_display', 'marriage_date',
            'father_name', 'mother_name', 'card_number',
            'church_entry', 'church_entry_display', 'church_entry_other',
            'ministry_areas', 'ministry_areas_display', 'photo',
            'relatives',
            'status', 'status_display', 'notes',
            'lifecycle_stage', 'lifecycle_stage_display', 'last_contact_at',
            'street', 'number', 'complement', 'neighborhood', 'city', 'state', 'cep',
            'public_hash',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'church', 'created_at', 'updated_at', 'card_number',
            'public_hash', 'last_contact_at',
        ]

    def get_ministry_areas_display(self, obj):
        return MinistryAreaSerializer(obj.ministry_areas.all(), many=True).data

    def get_church_entry_display(self, obj):
        if (
            obj.church_entry == Member.ChurchEntry.OUTRO
            and obj.church_entry_other
        ):
            return obj.church_entry_other
        return obj.get_church_entry_display() or ''

    def validate_ministry_areas(self, value):
        if not value:
            return value
        church = getattr(self.instance, 'church', None)
        if church is None:
            church = self.context.get('church')
        if church is None:
            request = self.context.get('request')
            church = getattr(getattr(request, 'user', None), 'church', None)
        if church is not None and any(a.church_id != church.id for a in value):
            raise serializers.ValidationError(
                'Uma das áreas de atuação pertence a outra igreja.'
            )
        return value

    def validate_born_in_state(self, value):
        value = (value or '').upper().strip()
        if value and len(value) != 2:
            raise serializers.ValidationError('A UF deve conter 2 letras.')
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        church_entry = attrs.get('church_entry')
        if church_entry is None and self.instance:
            church_entry = self.instance.church_entry
        other = (attrs.get('church_entry_other') or '').strip()
        if church_entry == Member.ChurchEntry.OUTRO:
            if not other:
                raise serializers.ValidationError(
                    {'church_entry_other': 'Informe a outra forma de entrada.'}
                )
            attrs['church_entry_other'] = other
        else:
            attrs['church_entry_other'] = ''
        return attrs

    def _sync_relatives(self, instance, relatives):
        """Sincroniza a lista de parentes: cria, atualiza (por id) e remove os ausentes."""
        incoming_ids = set()
        seen = set()
        for data in relatives:
            rel_id = data.pop('id', None)
            rel_id = int(rel_id) if rel_id else None
            if rel_id is not None and rel_id in seen:
                continue
            seen.add(rel_id)
            if rel_id is not None:
                rel = MemberRelative.objects.filter(pk=rel_id, member=instance).first()
                if rel is None:
                    continue
                for field in ('name', 'kinship', 'birth_date', 'phone'):
                    if field in data:
                        setattr(rel, field, data[field])
                rel.save()
                incoming_ids.add(rel.id)
            else:
                rel = MemberRelative.objects.create(member=instance, **data)
                incoming_ids.add(rel.id)
        instance.relatives.exclude(pk__in=incoming_ids).delete()

    def create(self, validated_data):
        relatives = validated_data.pop('relatives', [])
        member = super().create(validated_data)
        with transaction.atomic():
            if relatives:
                self._sync_relatives(member, list(relatives))
            if not member.card_number:
                member.card_number = services.next_member_card_number(member.church)
                member.save(update_fields=['card_number'])
        return member

    def update(self, instance, validated_data):
        relatives = validated_data.pop('relatives', None)
        member = super().update(instance, validated_data)
        if relatives is not None:
            self._sync_relatives(member, list(relatives))
        return member


class MessageTemplateSerializer(serializers.ModelSerializer):
    category_display = serializers.CharField(
        source='get_category_display', read_only=True,
    )

    class Meta:
        model = MessageTemplate
        fields = [
            'id', 'church', 'title', 'category', 'category_display',
            'content', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'church', 'created_at', 'updated_at']

    def create(self, validated_data):
        church = self.context.get('church')
        request = self.context.get('request')
        if church is None:
            church = getattr(getattr(request, 'user', None), 'church', None)
        validated_data['church'] = church
        if not validated_data.get('title'):
            validated_data['title'] = dict(MessageTemplate.Category.choices).get(
                validated_data.get('category', ''), 'Personalizado'
            )
        validated_data['created_by'] = getattr(request, 'user', None)
        return super().create(validated_data)


class MemberTransferSerializer(serializers.ModelSerializer):
    """Transferência de membresia entre igrejas (saída/entrada, somente leitura)."""

    source_church_name = serializers.CharField(
        source='source_church.name', read_only=True,
    )
    target_church_name = serializers.CharField(
        source='target_church.name', read_only=True,
    )
    status_display = serializers.CharField(
        source='get_status_display', read_only=True,
    )

    class Meta:
        model = MemberTransfer
        fields = [
            'id', 'source_church', 'source_church_name', 'source_member',
            'target_church', 'target_church_name',
            'status', 'status_display',
            'issued_at', 'received_at',
            'member_name', 'member_cpf', 'member_rg',
            'member_birth_date', 'member_baptism_date',
            'member_phone', 'member_email', 'member_profession',
            'member_father_name', 'member_mother_name',
            'member_street', 'member_number', 'member_complement',
            'member_neighborhood', 'member_city', 'member_state', 'member_cep',
            'member_notes',
        ]
        read_only_fields = fields


class MemberDocumentSerializer(serializers.ModelSerializer):
    """Documento anexado ao membro (saída; upload é tratado pela view)."""

    doc_type_display = serializers.CharField(
        source='get_doc_type_display', read_only=True,
    )
    file_name = serializers.SerializerMethodField()
    uploaded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = MemberDocument
        fields = [
            'id', 'member', 'doc_type', 'doc_type_display', 'file_name',
            'notes', 'uploaded_by', 'uploaded_by_name', 'uploaded_at',
        ]
        read_only_fields = fields

    def get_file_name(self, obj):
        return os.path.basename(obj.file.name or '')

    def get_uploaded_by_name(self, obj):
        return obj.uploaded_by.name if obj.uploaded_by else ''


class WorshipServiceSerializer(serializers.ModelSerializer):
    """Registro de culto da igreja ativa."""

    service_type_display = serializers.CharField(
        source='get_service_type_display', read_only=True,
    )

    class Meta:
        model = WorshipService
        fields = [
            'id', 'church', 'date', 'time', 'service_type', 'service_type_display',
            'presider', 'preacher', 'theme', 'scripture',
            'attendees', 'visitors', 'conversions', 'offering',
            'notes', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'church', 'created_at', 'updated_at']

    def validate(self, attrs):
        for field in ('presider', 'preacher', 'theme', 'scripture', 'notes'):
            if field in attrs and isinstance(attrs.get(field), str):
                attrs[field] = (attrs.get(field) or '').strip()
        for field in ('attendees', 'visitors', 'conversions'):
            value = attrs.get(field)
            if value is not None and value < 0:
                raise serializers.ValidationError(
                    {field: ['O valor não pode ser negativo.']}
                )
        offering = attrs.get('offering')
        if offering is not None and offering < 0:
            raise serializers.ValidationError(
                {'offering': ['O valor não pode ser negativo.']}
            )
        return attrs


class GrowthGroupSerializer(serializers.ModelSerializer):
    """Grupo de Crescimento da igreja ativa.

    Endereço segue o padrão do sistema (como `Member`/`Church`): campos
    estruturados `cep/street/number/complement/neighborhood/city/state`,
    preenchidos a partir do CEP (ViaCEP) no frontend; `address` é somente
    leitura e devolve o endereço completo formatado para exibição no mapa/tabela.

    Validações:
    - Dia de encontro não pode ser dia de culto oficial (quinta/sábado/domingo);
    - Líder é obrigatório e anfitrião opcional (membros da igreja);
    - Logradouro e cidade obrigatórios (padrão de endereço do sistema);
    - Latitude e longitude devem vir juntas.
    """

    category_display = serializers.CharField(
        source='get_category_display', read_only=True,
    )
    leader_name = serializers.CharField(source='leader.name', read_only=True)
    host_name = serializers.CharField(source='host.name', read_only=True)
    weekday_display = serializers.CharField(source='get_weekday_display', read_only=True)
    address = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = GrowthGroup
        fields = [
            'id', 'church', 'name', 'leader', 'leader_name', 'host', 'host_name',
            'weekday', 'weekday_display', 'time',
            'cep', 'street', 'number', 'complement', 'neighborhood', 'city', 'state',
            'address', 'radius_meters', 'category', 'category_display', 'is_full',
            'latitude', 'longitude', 'is_active',
            'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'church', 'created_by', 'created_at', 'updated_at']

    def get_address(self, obj):
        return obj.full_address

    def validate_name(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('Informe o nome do GC.')
        return value

    def _clean_str(self, value):
        return (value or '').strip()

    def validate_cep(self, value):
        return self._clean_str(value).replace(' ', '')

    def validate_weekday(self, value):
        if value in GrowthGroup.FORBIDDEN_WEEKDAYS:
            raise serializers.ValidationError(
                'Reuniões de GC não podem ocorrer em dias de culto oficial '
                '(quinta-feira, sábado e domingo).'
            )
        return value

    def validate(self, attrs):
        if attrs.get('street') is not None:
            attrs['street'] = self._clean_str(attrs['street'])
        if attrs.get('number') is not None:
            attrs['number'] = self._clean_str(attrs['number'])
        if attrs.get('complement') is not None:
            attrs['complement'] = self._clean_str(attrs['complement'])
        if attrs.get('neighborhood') is not None:
            attrs['neighborhood'] = self._clean_str(attrs['neighborhood'])
        if attrs.get('city') is not None:
            attrs['city'] = self._clean_str(attrs['city'])
        if attrs.get('state') is not None:
            attrs['state'] = self._clean_str(attrs['state']).upper()

        # Em edição parcial, leia os valores atuais da instância.
        street = attrs.get('street')
        if street is None and self.instance is not None:
            street = self.instance.street
        city = attrs.get('city')
        if city is None and self.instance is not None:
            city = self.instance.city
        if not (street or '').strip() or not (city or '').strip():
            raise serializers.ValidationError(
                {'city': ['Informe o endereço do GC (logradouro e cidade).']}
            )

        lat = attrs.get('latitude')
        if lat is None and self.instance is not None:
            lat = self.instance.latitude
        lng = attrs.get('longitude')
        if lng is None and self.instance is not None:
            lng = self.instance.longitude
        if (lat is None) != (lng is None):
            raise serializers.ValidationError(
                {'latitude': ['Informe latitude e longitude juntas.']}
            )
        return attrs

    def create(self, validated_data):
        church = self.context.get('church')
        if church is not None:
            validated_data['church'] = church
        return super().create(validated_data)


class GrowthGroupPublicSerializer(serializers.ModelSerializer):
    """Grupo de Crescimento exibido no card público do mapa de GCs.

    Somente leitura: expõe dados de contato do líder e endereço para o
    frontend público, sem dados internos (criador, timestamps, ativo).
    """

    category_display = serializers.CharField(
        source='get_category_display', read_only=True,
    )
    weekday_display = serializers.CharField(
        source='get_weekday_display', read_only=True,
    )
    leader_name = serializers.CharField(source='leader.name', read_only=True, default='')
    host_name = serializers.CharField(source='host.name', read_only=True, default='')
    leader_phone = serializers.CharField(
        source='leader.phone', read_only=True, default='',
    )
    full_address = serializers.CharField(read_only=True)
    whatsapp_url = serializers.SerializerMethodField()
    maps_url = serializers.SerializerMethodField()

    class Meta:
        model = GrowthGroup
        fields = [
            'id', 'name', 'category', 'category_display', 'weekday',
            'weekday_display', 'time',
            'leader_name', 'host_name', 'leader_phone',
            'neighborhood', 'city', 'state', 'full_address',
            'latitude', 'longitude', 'radius_meters', 'is_full',
            'whatsapp_url', 'maps_url',
        ]
        read_only_fields = fields

    def get_whatsapp_url(self, obj):
        phone = obj.leader.phone if obj.leader else ''
        digits = re.sub(r'\D', '', phone or '')
        if len(digits) in (10, 11):
            digits = f'55{digits}'
        if len(digits) < 12 or len(digits) > 15:
            return None
        message = (
            f'Olá! Encontrei o GC {obj.name} no mapa da igreja '
            'e gostaria de participar.'
        )
        return f'https://wa.me/{digits}?text={quote(message)}'

    def get_maps_url(self, obj):
        if obj.latitude is None or obj.longitude is None:
            return None
        return (
            f'https://www.google.com/maps/dir/?api=1'
            f'&destination={obj.latitude},{obj.longitude}'
        )


class ChurchMinutesSerializer(serializers.ModelSerializer):
    """Ata da igreja ativa, com PDF opcional e hash público."""

    meeting_type_display = serializers.CharField(
        source='get_meeting_type_display', read_only=True,
    )
    pdf_name = serializers.SerializerMethodField()
    remove_pdf = serializers.BooleanField(required=False, write_only=True, default=False)

    class Meta:
        model = ChurchMinutes
        fields = [
            'id', 'church', 'title', 'meeting_type', 'meeting_type_display',
            'meeting_date', 'location', 'recorder', 'participants', 'content',
            'pdf', 'pdf_name', 'public_hash', 'remove_pdf',
            'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'church', 'public_hash', 'created_by', 'created_at', 'updated_at',
        ]

    def get_pdf_name(self, obj):
        return os.path.basename(obj.pdf.name or '') if obj.pdf else None

    def create(self, validated_data):
        validated_data.pop('remove_pdf', None)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        remove_pdf = validated_data.pop('remove_pdf', False)
        instance = super().update(instance, validated_data)
        if remove_pdf:
            if instance.pdf and instance.pdf.name:
                instance.pdf.delete(save=False)
            instance.pdf = None
            instance.save(update_fields=['pdf', 'updated_at'])
        return instance

    def validate(self, attrs):
        for field in ('title', 'location', 'recorder'):
            if field in attrs and isinstance(attrs.get(field), str):
                attrs[field] = (attrs.get(field) or '').strip()
        for field in ('participants', 'content'):
            if field in attrs and isinstance(attrs.get(field), str):
                attrs[field] = (attrs.get(field) or '').strip()
        title = attrs.get('title')
        if title is not None and not title:
            raise serializers.ValidationError(
                {'title': ['Informe o título da ata.']}
            )
        content = attrs.get('content')
        if content is not None and not content:
            raise serializers.ValidationError(
                {'content': ['Informe o texto da ata.']}
            )
        return attrs


class PublicMemberCardSerializer(serializers.Serializer):
    """Dados públicos do cartão de membro (frente e verso), SEM endereço."""

    name = serializers.CharField()
    card_number = serializers.SerializerMethodField()
    photo = serializers.SerializerMethodField()
    birth_date = serializers.DateField(format='%d/%m/%Y', required=False, allow_null=True)
    status = serializers.CharField()
    church_name = serializers.CharField(source='church.name')
    church_city = serializers.CharField(source='church.city')
    church_state = serializers.CharField(source='church.state')
    church_phone = serializers.CharField(source='church.phone')
    card_primary_color = serializers.CharField(source='church.card_primary_color')
    card_secondary_color = serializers.CharField(source='church.card_secondary_color')
    card_valid_until = serializers.DateField(
        format='%d/%m/%Y', source='church.card_valid_until', required=False, allow_null=True,
    )
    card_front_phrase = serializers.CharField(source='church.card_front_phrase')
    card_back_phrase = serializers.CharField(source='church.card_back_phrase')

    def get_card_number(self, obj):
        return obj.card_number or ''

    def get_photo(self, obj):
        if obj.photo and getattr(obj.photo, 'url', None):
            try:
                return obj.photo.url
            except Exception:
                return None
        return None


class PublicMemberProfileSerializer(serializers.Serializer):
    """Perfil público do membro (https://.../perfil/{hash}).

    Sem endereço/CPF/telefone interno. O WhatsApp só aparece quando o
    próprio membro opta por exibi-lo (``whatsapp_public``).
    """

    name = serializers.CharField()
    photo = serializers.SerializerMethodField()
    birth_date = serializers.DateField(format='%d/%m/%Y', required=False, allow_null=True)
    status = serializers.CharField()
    status_label = serializers.SerializerMethodField()
    member_since = serializers.SerializerMethodField()
    ministry_areas = serializers.SerializerMethodField()
    role_title = serializers.SerializerMethodField()
    whatsapp = serializers.SerializerMethodField()
    church = serializers.SerializerMethodField()
    card_theme = serializers.CharField(source='church.card_theme')
    valid_until = serializers.DateField(
        format='%d/%m/%Y', source='church.card_valid_until',
        required=False, allow_null=True,
    )

    def get_photo(self, obj):
        if obj.photo and getattr(obj.photo, 'url', None):
            try:
                return obj.photo.url
            except Exception:
                return None
        return None

    def get_status_label(self, obj):
        return obj.get_status_display()

    def get_member_since(self, obj):
        return None

    def get_ministry_areas(self, obj):
        return [area.name for area in obj.ministry_areas.order_by('id')]

    def get_role_title(self, obj):
        return obj.profession or None

    def get_whatsapp(self, obj):
        if not obj.whatsapp_public or not obj.phone:
            return None
        digits = re.sub(r'\D', '', obj.phone)
        if len(digits) in (10, 11):
            return f'+55{digits}'
        return None

    def get_church(self, obj):
        church = obj.church
        logo = None
        if church.logo and getattr(church.logo, 'url', None):
            try:
                logo = church.logo.url
            except Exception:
                logo = None
        return {
            'name': church.name,
            'city': church.city,
            'state': church.state,
            'logo': logo,
        }


class PublicMemberFormSerializer(serializers.Serializer):
    """Metadados públicos do formulário (link do cartão ou genérico da igreja).

    - `type = "member"`   → o hash é de um cartão de membro (atualização).
    - `type = "candidate"` → o hash é o formulário genérico da igreja (novo candidato).
    """

    type = serializers.CharField()
    church_name = serializers.CharField()
    church_city = serializers.CharField()
    church_state = serializers.CharField()
    church_phone = serializers.CharField()
    member_name = serializers.CharField(read_only=True, default=None)
    card_number = serializers.CharField(read_only=True, default=None)


PUBLIC_SUBMISSION_FIELDS = (
    'name', 'phone', 'email', 'birth_date', 'cpf', 'rg',
    'born_in_city', 'born_in_state', 'profession', 'education_level',
    'marital_status', 'marriage_date', 'father_name', 'mother_name',
    'church_entry', 'church_entry_other', 'street', 'number', 'complement',
    'neighborhood', 'city', 'state', 'cep', 'notes', 'photo', 'relatives',
)


def _valid_cpf(value: str) -> bool:
    """Valida CPF (11 dígitos + dígitos verificadores)."""
    digits = [int(c) for c in value if c.isdigit()]
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    for length in (9, 10):
        total = sum(d * (length + 1 - i) for i, d in enumerate(digits[:length]))
        check = (total * 10) % 11
        if check == 10:
            check = 0
        if check != digits[length]:
            return False
    return True


class PublicSubmissionSerializer(serializers.Serializer):
    """Valida a submissão pública do formulário de membro.

    Os dados chegam em `data` (dict) e são armazenados para revisão.
    Campos de escritura seguem as mesmas regras do cadastro interno.
    """

    data = serializers.DictField()

    def validate(self, attrs):
        attrs = super().validate(attrs)
        data = attrs.get('data') or {}

        name = (data.get('name') or '').strip()
        if not name:
            raise serializers.ValidationError(
                {'data': ['O nome é obrigatório.']}
            )
        data['name'] = name

        email = (data.get('email') or '').strip()
        if email and not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
            raise serializers.ValidationError(
                {'data': ['E-mail inválido.']}
            )
        data['email'] = email

        phone = (data.get('phone') or '').strip()
        phone_digits = re.sub(r'\D', '', phone)
        if phone and len(phone_digits) < 10:
            raise serializers.ValidationError(
                {'data': ['Telefone inválido (mínimo 10 dígitos).']}
            )
        data['phone'] = phone

        cpf = (data.get('cpf') or '').strip()
        if cpf:
            if not _valid_cpf(cpf):
                raise serializers.ValidationError(
                    {'data': ['CPF inválido.']}
                )
        data['cpf'] = cpf

        cep = (data.get('cep') or '').strip()
        if cep and len(re.sub(r'\D', '', cep)) != 8:
            raise serializers.ValidationError(
                {'data': ['CEP inválido (8 dígitos).']}
            )
        data['cep'] = cep

        photo = (data.get('photo') or '').strip()
        if photo:
            data_url = photo.split(',', 1)
            if not photo.startswith('data:image/') or len(data_url) != 2:
                raise serializers.ValidationError(
                    {'data': ['Foto inválida (use uma imagem em base64).']}
                )
            if len(data_url[1]) > 2_800_000:
                raise serializers.ValidationError(
                    {'data': ['Foto muito grande (limite ~2MB).']}
                )
        data['photo'] = photo

        marital_status = data.get('marital_status')
        if marital_status:
            valid = list(Member.MaritalStatus.values)
            if marital_status not in valid:
                raise serializers.ValidationError(
                    {'data': [f'Estado civil inválido: {marital_status}.']}
                )

        education_level = data.get('education_level')
        if education_level:
            valid = list(Member.EducationLevel.values)
            if education_level not in valid:
                raise serializers.ValidationError(
                    {'data': [f'Escolaridade inválida: {education_level}.']}
                )

        church_entry = data.get('church_entry')
        if church_entry:
            valid = list(Member.ChurchEntry.values)
            if church_entry not in valid:
                raise serializers.ValidationError(
                    {'data': [f'Forma de entrada inválida: {church_entry}.']}
                )
            other = (data.get('church_entry_other') or '').strip()
            if church_entry == Member.ChurchEntry.OUTRO and not other:
                raise serializers.ValidationError(
                    {'data': ['Informe a outra forma de entrada.']}
                )
            if church_entry != Member.ChurchEntry.OUTRO:
                data['church_entry_other'] = ''
        state = (data.get('state') or '').upper().strip()
        if state and len(state) != 2:
            raise serializers.ValidationError(
                {'data': ['A UF deve conter 2 letras.']}
            )
        data['state'] = state

        relatives = data.get('relatives')
        clean_relatives = []
        if relatives is not None:
            if not isinstance(relatives, list):
                raise serializers.ValidationError(
                    {'data': ['Parentes devem ser uma lista.']}
                )
            valid_kinships = list(MemberRelative.Kinship.values)
            for rel in relatives:
                rel_name = (rel.get('name') or '').strip()
                kinship = (rel.get('kinship') or '').strip().upper()
                if not rel_name:
                    raise serializers.ValidationError(
                        {'data': ['Todo parente precisa de nome.']}
                    )
                if kinship not in valid_kinships:
                    raise serializers.ValidationError(
                        {'data': [f'Grau de parentesco inválido: {kinship}.']}
                    )
                clean_relatives.append({
                    'name': rel_name,
                    'kinship': kinship,
                    'birth_date': (rel.get('birth_date') or '') or None,
                    'phone': (rel.get('phone') or '').strip(),
                })
        data['relatives'] = clean_relatives

        data = {k: (data.get(k) or '') for k in PUBLIC_SUBMISSION_FIELDS}
        if notes := data.get('notes'):
            data['notes'] = notes
        if photo := data.get('photo'):
            data['photo'] = photo
        if data.get('relatives'):
            data['relatives'] = clean_relatives
        attrs['data'] = data
        return attrs


class MemberSubmissionSerializer(serializers.ModelSerializer):
    """Submissão pendente de revisão (Secretaria/Pastor)."""

    member_name = serializers.SerializerMethodField()
    member_card_number = serializers.SerializerMethodField()
    reviewed_by_name = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = MemberSubmission
        fields = [
            'id', 'church', 'member', 'member_name', 'member_card_number',
            'source_hash', 'data', 'status', 'status_display',
            'reviewed_by', 'reviewed_by_name', 'notes',
            'created_at', 'updated_at', 'reviewed_at',
        ]
        read_only_fields = fields

    def get_member_name(self, obj):
        return obj.member.name if obj.member else None

    def get_member_card_number(self, obj):
        return obj.member.card_number if obj.member else None

    def get_reviewed_by_name(self, obj):
        if not obj.reviewed_by:
            return None
        return obj.reviewed_by.name or obj.reviewed_by.email


DEFAULT_LINK_ICONS = {
    ChurchPublicLink.LinkType.CUSTOM: 'link',
    ChurchPublicLink.LinkType.PIX: 'qrcode',
    ChurchPublicLink.LinkType.WHATSAPP: 'brand-whatsapp',
    ChurchPublicLink.LinkType.YOUTUBE: 'brand-youtube',
    ChurchPublicLink.LinkType.MAPS: 'map-pin',
    ChurchPublicLink.LinkType.INSTAGRAM: 'brand-instagram',
    ChurchPublicLink.LinkType.CALENDAR: 'calendar',
    ChurchPublicLink.LinkType.MEMBERSHIP: 'user-plus',
    ChurchPublicLink.LinkType.PRAYER: 'pray',
    ChurchPublicLink.LinkType.GROWTH_GROUPS: 'users',
}


class ChurchPublicLinkSerializer(serializers.ModelSerializer):
    """CRUD de links no painel (escopo da igreja ativa)."""

    link_type_display = serializers.CharField(
        source='get_link_type_display', read_only=True,
    )

    class Meta:
        model = ChurchPublicLink
        fields = [
            'id', 'church', 'title', 'url', 'link_type', 'link_type_display',
            'pix_key', 'pix_type', 'pix_amount_mode', 'pix_fixed_amount',
            'pix_grid_amounts', 'pix_open_amount', 'whatsapp_number',
            'address_cep', 'address_street', 'address_number',
            'address_neighborhood', 'address_city', 'address_state',
            'icon_key', 'order', 'is_active', 'highlight', 'click_count',
            'created_at',
        ]
        read_only_fields = ['church', 'click_count', 'created_at', 'order']

    def _context_church(self):
        church = self.context.get('church')
        if church is not None:
            return church
        request = self.context.get('request')
        return request.user.church if request else None

    def _clear_other(self, attrs):
        """Mantém apenas os campos relevantes ao tipo do link."""
        for f in (
            'whatsapp_number', 'address_cep', 'address_street', 'address_number',
            'address_neighborhood', 'address_city', 'address_state',
        ):
            attrs[f] = attrs.get(f, '') or ''
        for f in (
            'pix_key', 'pix_type', 'pix_fixed_amount', 'pix_grid_amounts',
        ):
            attrs[f] = None
        attrs['pix_amount_mode'] = 'OPEN'
        attrs['pix_open_amount'] = False

    def validate(self, attrs):
        link_type = attrs.get('link_type') or getattr(self.instance, 'link_type', None)
        url = attrs.get('url', getattr(self.instance, 'url', ''))

        if link_type == ChurchPublicLink.LinkType.PIX:
            pix_key = attrs.get('pix_key', getattr(self.instance, 'pix_key', None))
            if not pix_key:
                raise serializers.ValidationError(
                    {'pix_key': 'Informe a chave PIX para este link.'}
                )
            attrs['url'] = url or ''
            if not attrs.get('pix_type'):
                attrs['pix_type'] = getattr(self.instance, 'pix_type', None)

            mode = attrs.get(
                'pix_amount_mode',
                getattr(self.instance, 'pix_amount_mode', 'OPEN'),
            ) or 'OPEN'
            if mode not in ('OPEN', 'FIXED', 'GRID'):
                mode = 'OPEN'
            attrs['pix_amount_mode'] = mode

            attrs['whatsapp_number'] = ''
            for f in ('address_cep', 'address_street', 'address_number',
                      'address_neighborhood', 'address_city', 'address_state'):
                attrs[f] = ''

            if mode == 'FIXED':
                amount = attrs.get(
                    'pix_fixed_amount',
                    getattr(self.instance, 'pix_fixed_amount', None),
                )
                if amount is None:
                    amount = 0
                try:
                    amount_dec = Decimal(str(amount)).quantize(Decimal('0.01'))
                except Exception:
                    amount_dec = Decimal('0')
                if amount_dec <= 0:
                    raise serializers.ValidationError(
                        {'pix_fixed_amount': 'Informe um valor fixo válido.'}
                    )
                attrs['pix_fixed_amount'] = float(amount_dec)
                attrs['pix_grid_amounts'] = None
                attrs['pix_open_amount'] = False
            elif mode == 'GRID':
                grid = attrs.get(
                    'pix_grid_amounts',
                    getattr(self.instance, 'pix_grid_amounts', None),
                )
                if not grid:
                    grid = [30, 50, 100, 200]
                try:
                    values = [Decimal(str(v)) for v in grid]
                except Exception:
                    raise serializers.ValidationError(
                        {'pix_grid_amounts': 'Informe valores válidos.'}
                    )
                if not values or any(v <= 0 for v in values):
                    raise serializers.ValidationError(
                        {'pix_grid_amounts': 'Informe ao menos um valor positivo.'}
                    )
                values = values[:10]
                attrs['pix_grid_amounts'] = [
                    float(v.quantize(Decimal('0.01'))) for v in values
                ]
                attrs['pix_fixed_amount'] = None
                if 'pix_open_amount' not in attrs:
                    attrs['pix_open_amount'] = getattr(
                        self.instance, 'pix_open_amount', True
                    )
            else:  # OPEN
                attrs['pix_fixed_amount'] = None
                attrs['pix_grid_amounts'] = None
                attrs['pix_open_amount'] = False
        elif link_type == ChurchPublicLink.LinkType.WHATSAPP:
            number = attrs.get(
                'whatsapp_number',
                getattr(self.instance, 'whatsapp_number', ''),
            )
            digits = re.sub(r'\D', '', number or '')
            if len(digits) in (10, 11):
                digits = f'55{digits}'
            if len(digits) < 12 or len(digits) > 15:
                raise serializers.ValidationError(
                    {'whatsapp_number': 'Número de WhatsApp inválido.'}
                )
            attrs['whatsapp_number'] = digits
            attrs['url'] = f'https://wa.me/{digits}'
            for f in ('pix_key', 'pix_type', 'pix_fixed_amount', 'pix_grid_amounts'):
                attrs[f] = None
            attrs['pix_amount_mode'] = 'OPEN'
            attrs['pix_open_amount'] = False
            for f in ('address_cep', 'address_street', 'address_number',
                      'address_neighborhood', 'address_city', 'address_state'):
                attrs[f] = ''
        elif link_type == ChurchPublicLink.LinkType.MAPS:
            street = (attrs.get('address_street', getattr(self.instance, 'address_street', '')) or '').strip()
            num = (attrs.get('address_number', getattr(self.instance, 'address_number', '')) or '').strip()
            city = (attrs.get('address_city', getattr(self.instance, 'address_city', '')) or '').strip()
            if not street or not num or not city:
                raise serializers.ValidationError(
                    {'address_number': 'Informe CEP, logradouro e número para o mapa.'}
                )
            for f in ('address_cep', 'address_street', 'address_number',
                      'address_neighborhood', 'address_city', 'address_state'):
                attrs[f] = (attrs.get(f, getattr(self.instance, f, '')) or '').strip()
            parts = [
                attrs['address_street'], attrs['address_number'],
                attrs['address_neighborhood'], attrs['address_city'],
                attrs['address_state'],
            ]
            query = ', '.join(p for p in parts if p)
            attrs['url'] = f'https://www.google.com/maps/search/?api=1&query={quote(query)}'
            for f in ('pix_key', 'pix_type', 'pix_fixed_amount', 'pix_grid_amounts'):
                attrs[f] = None
            attrs['pix_amount_mode'] = 'OPEN'
            attrs['pix_open_amount'] = False
            attrs['whatsapp_number'] = ''
        elif link_type in (
            ChurchPublicLink.LinkType.CALENDAR,
            ChurchPublicLink.LinkType.MEMBERSHIP,
            ChurchPublicLink.LinkType.PRAYER,
            ChurchPublicLink.LinkType.GROWTH_GROUPS,
        ):
            # Sistema: a URL é resolvida a partir dos hashes públicos da igreja.
            attrs['url'] = ''
            attrs['pix_key'] = None
            attrs['pix_type'] = None
            self._clear_other(attrs)
        else:
            if not url:
                raise serializers.ValidationError(
                    {'url': 'Informe a URL do link.'}
                )
            if not url.startswith(('http://', 'https://')):
                raise serializers.ValidationError(
                    {'url': 'URL inválida. Use http:// ou https://.'}
                )
            attrs['pix_key'] = None
            attrs['pix_type'] = None
            self._clear_other(attrs)

        if not attrs.get('icon_key'):
            default_icon = DEFAULT_LINK_ICONS.get(link_type)
            if default_icon:
                attrs['icon_key'] = default_icon
        return attrs


class ChurchLinksConfigSerializer(serializers.ModelSerializer):
    """Configuração global da página de links da igreja ativa."""

    slug = serializers.SlugField(required=False, allow_blank=True)

    class Meta:
        model = Church
        fields = [
            'slug', 'public_links_enabled', 'theme_color',
            'default_pix_key', 'default_pix_type',
        ]

    def validate_theme_color(self, value):
        value = (value or '').strip().upper()
        if value and not re.fullmatch(r'#[0-9A-F]{6}', value):
            raise serializers.ValidationError(
                'Cor inválida. Use o formato #RRGGBB.'
            )
        return value

    def validate_slug(self, value):
        value = (value or '').strip()
        if not value:
            return value
        value = slugify(value)[:100]

        def taken(candidate):
            qs = Church.objects.filter(slug=candidate)
            if self.instance is not None:
                qs = qs.exclude(pk=self.instance.pk)
            return qs.exists()

        if not taken(value):
            return value
        base = value
        suffix = 2
        while taken(f'{base[:100 - len(f"-{suffix}")]}-{suffix}'):
            suffix += 1
        return f'{base[:100 - len(f"-{suffix}")]}-{suffix}'


class PublicChurchPublicLinkSerializer(serializers.ModelSerializer):
    """Link exibido na página pública, com URL resolvida para tipos do sistema."""

    link_type_display = serializers.CharField(
        source='get_link_type_display', read_only=True,
    )
    url = serializers.SerializerMethodField()
    address = serializers.SerializerMethodField()

    class Meta:
        model = ChurchPublicLink
        fields = [
            'id', 'title', 'url', 'address', 'link_type', 'link_type_display',
            'icon_key', 'highlight', 'click_count',
            'pix_key', 'pix_type', 'pix_amount_mode', 'pix_fixed_amount',
            'pix_grid_amounts', 'pix_open_amount',
        ]

    def get_address(self, obj):
        if obj.link_type != ChurchPublicLink.LinkType.MAPS:
            return ''
        parts = [
            obj.address_street, obj.address_number, obj.address_neighborhood,
            obj.address_city, obj.address_state,
        ]
        return ', '.join(p for p in parts if p)

    def get_url(self, obj):
        if obj.link_type == ChurchPublicLink.LinkType.CALENDAR:
            if obj.church.calendar_public_hash:
                return f'{settings.FRONTEND_URL}/calendario/{obj.church.calendar_public_hash}'
            return ''
        if obj.link_type == ChurchPublicLink.LinkType.MEMBERSHIP:
            if obj.church.member_form_hash:
                return f'{settings.FRONTEND_URL}/formulario/{obj.church.member_form_hash}'
            return ''
        if obj.link_type == ChurchPublicLink.LinkType.GROWTH_GROUPS:
            return f'{settings.FRONTEND_URL}/gc/{obj.church.slug}'
        return obj.url


class PublicChurchLinksSerializer(serializers.Serializer):
    """Payload completo da página pública `/p/{slug}`."""

    church = serializers.SerializerMethodField()
    system_links = serializers.SerializerMethodField()
    links = serializers.SerializerMethodField()

    def get_church(self, obj):
        church = obj['church']
        return {
            'name': church.name,
            'city': church.city,
            'state': church.state,
            'neighborhood': church.neighborhood,
            'logo': services.cloudinary_url(church.logo),
            'theme_color': church.theme_color,
        }

    def get_system_links(self, obj):
        church = obj['church']
        system_types = (
            ChurchPublicLink.LinkType.CALENDAR,
            ChurchPublicLink.LinkType.MEMBERSHIP,
            ChurchPublicLink.LinkType.PRAYER,
            ChurchPublicLink.LinkType.GROWTH_GROUPS,
        )
        stored = set(
            church.public_links.filter(link_type__in=system_types)
            .values_list('link_type', flat=True)
        )
        system = []
        if church.calendar_public_hash and ChurchPublicLink.LinkType.CALENDAR not in stored:
            system.append({
                'link_type': ChurchPublicLink.LinkType.CALENDAR,
                'title': 'Agenda de Cultos',
                'url': f'{settings.FRONTEND_URL}/calendario/{church.calendar_public_hash}',
                'icon_key': 'calendar',
                'highlight': False,
            })
        if church.member_form_hash and ChurchPublicLink.LinkType.MEMBERSHIP not in stored:
            system.append({
                'link_type': ChurchPublicLink.LinkType.MEMBERSHIP,
                'title': 'Ficha de Membro / Cadastro',
                'url': f'{settings.FRONTEND_URL}/formulario/{church.member_form_hash}',
                'icon_key': 'user-plus',
                'highlight': False,
            })
        if ChurchPublicLink.LinkType.PRAYER not in stored:
            system.append({
                'link_type': ChurchPublicLink.LinkType.PRAYER,
                'title': 'Pedido de Oração',
                'url': '',
                'icon_key': 'pray',
                'highlight': False,
            })
        if ChurchPublicLink.LinkType.GROWTH_GROUPS not in stored:
            system.append({
                'link_type': ChurchPublicLink.LinkType.GROWTH_GROUPS,
                'title': 'Grupos de Crescimento',
                'url': f'{settings.FRONTEND_URL}/gc/{church.slug}',
                'icon_key': 'users',
                'highlight': False,
            })
        return system

    def get_links(self, obj):
        return PublicChurchPublicLinkSerializer(
            obj['links'], many=True, context=self.context,
        ).data


class CertificateTemplateSerializer(serializers.ModelSerializer):
    """Modelo de certificado da igreja, com mídias opcionais no Cloudinary."""

    certificate_type_display = serializers.CharField(
        source='get_certificate_type_display', read_only=True,
    )
    layout_mode_display = serializers.CharField(
        source='get_layout_mode_display', read_only=True,
    )
    background_image_url = serializers.SerializerMethodField()
    background_image_name = serializers.SerializerMethodField()
    base_pdf_name = serializers.SerializerMethodField()
    remove_background_image = serializers.BooleanField(
        required=False, write_only=True, default=False
    )
    remove_base_pdf = serializers.BooleanField(
        required=False, write_only=True, default=False
    )

    class Meta:
        model = CertificateTemplate
        fields = [
            'id', 'church', 'name', 'certificate_type', 'certificate_type_display',
            'layout_mode', 'layout_mode_display', 'fields_layout', 'background_image',
            'background_image_url', 'background_image_name',
            'base_pdf', 'base_pdf_name', 'default_verse', 'is_active', 'created_at',
            'remove_background_image', 'remove_base_pdf',
        ]
        read_only_fields = ['id', 'church', 'created_at']

    def get_background_image_url(self, obj):
        return services.cloudinary_url(obj.background_image)

    def get_background_image_name(self, obj):
        return os.path.basename(obj.background_image.name or '') if obj.background_image else None

    def get_base_pdf_name(self, obj):
        return os.path.basename(obj.base_pdf.name or '') if obj.base_pdf else None

    def validate_fields_layout(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError(
                'O layout dos campos deve ser um objeto JSON.'
            )
        return value

    def validate(self, attrs):
        for field in ('name',):
            value = attrs.get(field)
            if isinstance(value, str):
                attrs[field] = (value or '').strip()
        name = attrs.get('name')
        if name is not None and not name:
            raise serializers.ValidationError({'name': ['Informe o nome do modelo.']})
        layout_mode = attrs.get('layout_mode')
        background_image = attrs.get('background_image')
        base_pdf = attrs.get('base_pdf')
        if layout_mode == CertificateTemplate.LayoutMode.CUSTOM_IMAGE and not background_image:
            raise serializers.ValidationError(
                {'background_image': ['Envie uma imagem de fundo para o modo de moldura.']}
            )
        if layout_mode == CertificateTemplate.LayoutMode.BASE_PDF and not base_pdf:
            raise serializers.ValidationError(
                {'base_pdf': ['Envie o documento em PDF para o modo de base.']}
            )
        if base_pdf is not None and not getattr(base_pdf, 'name', '').lower().endswith('.pdf'):
            raise serializers.ValidationError(
                {'base_pdf': ['O arquivo base deve ser um PDF válido (.pdf).']}
            )
        return attrs

    def create(self, validated_data):
        validated_data.pop('remove_background_image', None)
        validated_data.pop('remove_base_pdf', None)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        remove_background_image = validated_data.pop('remove_background_image', False)
        remove_base_pdf = validated_data.pop('remove_base_pdf', False)
        instance = super().update(instance, validated_data)
        if remove_background_image:
            if instance.background_image and instance.background_image.name:
                instance.background_image.delete(save=False)
            instance.background_image = None
        if remove_base_pdf:
            if instance.base_pdf and instance.base_pdf.name:
                instance.base_pdf.delete(save=False)
            instance.base_pdf = None
        if remove_background_image or remove_base_pdf:
            instance.save()
        return instance


class EcclesiasticalCertificateSerializer(serializers.ModelSerializer):
    """Registro de emissão de certificado eclesial com PDF final no Cloudinary."""

    certificate_type_display = serializers.CharField(
        source='get_certificate_type_display', read_only=True,
    )
    template_name = serializers.CharField(
        source='template.name', read_only=True, default=None,
    )
    member_name = serializers.CharField(
        source='member.name', read_only=True, default=None,
    )
    generated_pdf_name = serializers.SerializerMethodField()

    class Meta:
        model = EcclesiasticalCertificate
        fields = [
            'id', 'church', 'template', 'template_name', 'certificate_type',
            'certificate_type_display', 'recipient_name', 'member', 'member_name',
            'event_date', 'officiant_name', 'father_name', 'mother_name',
            'scripture_verse', 'registry_book', 'registry_page', 'registry_number',
            'generated_pdf', 'generated_pdf_name', 'created_by', 'created_at',
        ]
        read_only_fields = [
            'id', 'church', 'generated_pdf', 'created_by', 'created_at',
        ]

    def get_generated_pdf_name(self, obj):
        return os.path.basename(obj.generated_pdf.name or '') if obj.generated_pdf else None

    def validate(self, attrs):
        for field in ('recipient_name', 'officiant_name', 'father_name', 'mother_name'):
            value = attrs.get(field)
            if isinstance(value, str):
                attrs[field] = (value or '').strip()
        for field in ('scripture_verse', 'registry_book', 'registry_page', 'registry_number'):
            value = attrs.get(field)
            if isinstance(value, str):
                attrs[field] = (value or '').strip()
        recipient = attrs.get('recipient_name')
        if recipient is not None and not recipient:
            raise serializers.ValidationError(
                {'recipient_name': ['Informe o nome do destinatário.']}
            )
        if attrs.get('event_date') is None:
            raise serializers.ValidationError(
                {'event_date': ['Informe a data do evento.']}
            )
        if attrs.get('officiant_name') in (None, ''):
            raise serializers.ValidationError(
                {'officiant_name': ['Informe o ministro que celebrará o ato.']}
            )
        return attrs


class PrayerRequestSerializer(serializers.ModelSerializer):
    """Pedido de Oração — admin triage (listagem, edição de status/notas/atribuição)."""

    category_display = serializers.CharField(source='get_category_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    preferred_period_display = serializers.CharField(source='get_preferred_period_display', read_only=True)
    assigned_to_name = serializers.SerializerMethodField()
    whatsapp_url = serializers.SerializerMethodField()
    elapsed_days = serializers.SerializerMethodField()

    class Meta:
        model = PrayerRequest
        fields = [
            'id', 'church', 'requester_name', 'requester_phone', 'is_anonymous',
            'category', 'category_display', 'description', 'wants_visit',
            'cep', 'street', 'number', 'complement', 'neighborhood', 'city', 'state',
            'preferred_period', 'preferred_period_display',
            'status', 'status_display', 'assigned_to', 'assigned_to_name',
            'pastoral_notes', 'whatsapp_url', 'elapsed_days', 'created_at',
        ]
        read_only_fields = [
            'id', 'church', 'category_display', 'status_display',
            'preferred_period_display', 'assigned_to_name', 'whatsapp_url',
            'elapsed_days', 'created_at',
        ]

    def get_assigned_to_name(self, obj):
        return obj.assigned_to.name if obj.assigned_to_id else ''

    def get_whatsapp_url(self, obj):
        phone = (obj.requester_phone or '').strip()
        if not phone:
            return None
        digits = re.sub(r'\D', '', phone)
        if len(digits) in (10, 11):
            digits = f'55{digits}'
        if len(digits) < 12 or len(digits) > 15:
            return None
        message = 'Olá! Aqui é da equipe pastoral. Como podemos orar por você?'
        return f'https://wa.me/{digits}?text={quote(message)}'

    def get_elapsed_days(self, obj):
        if not obj.created_at:
            return 0
        delta = timezone.now() - obj.created_at
        return delta.days

    def validate(self, attrs):
        user = getattr(self.context.get('request'), 'user', None)
        if user and hasattr(user, 'church') and user.church_id is not None:
            attrs['church'] = user.church
        return attrs


class PublicPrayerRequestSerializer(serializers.ModelSerializer):
    """Submissão pública do agregador de links (/p/<slug>): sem autenticação."""

    class Meta:
        model = PrayerRequest
        fields = [
            'id', 'church', 'requester_name', 'requester_phone', 'is_anonymous',
            'category', 'description', 'wants_visit', 'cep', 'street', 'number',
            'complement', 'neighborhood', 'city', 'state', 'preferred_period',
            'status', 'created_at',
        ]
        read_only_fields = ['id', 'church', 'status', 'created_at']
        extra_kwargs = {
            'description': {'max_length': 2000, 'error_messages': {'required': 'Descreva o motivo do pedido de oração.'}},
        }

    def validate_requester_name(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('Informe seu nome.')
        if len(value) < 2:
            raise serializers.ValidationError('Informe um nome válido.')
        return value

    def validate_description(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('Descreva o motivo do pedido de oração.')
        return value

    def validate_category(self, value):
        if value not in PrayerRequest.Category.values:
            raise serializers.ValidationError('Categoria inválida.')
        return value

    def validate_preferred_period(self, value):
        if value not in PrayerRequest.PreferredPeriod.values:
            raise serializers.ValidationError('Período inválido.')
        return value

    def validate(self, attrs):
        """Quando o solicitante pede visita pastoral, o endereço completo é
        obrigatório — oplemento é a única exceção, por ser opcional na
        prática. Sem isso a equipe pastoral não consegue agendar a visita."""
        if not attrs.get('wants_visit'):
            return attrs

        required = {
            'cep': 'Informe o CEP para a visita pastoral.',
            'street': 'Informe o logradouro para a visita pastoral.',
            'number': 'Informe o número para a visita pastoral.',
            'neighborhood': 'Informe o bairro para a visita pastoral.',
            'city': 'Informe a cidade para a visita pastoral.',
            'state': 'Informe o estado (UF) para a visita pastoral.',
        }
        errors = {}
        for field, message in required.items():
            if not (attrs.get(field) or '').strip():
                errors[field] = [message]

        cep_digits = (attrs.get('cep') or '').replace(' ', '').replace('-', '')
        if len(cep_digits) != 8:
            errors.setdefault('cep', []).append('Informe um CEP válido com 8 dígitos.')

        state = (attrs.get('state') or '').strip().upper()
        if state and len(state) != 2:
            errors.setdefault('state', []).append('Informe a UF com 2 letras.')

        if attrs.get('preferred_period') == PrayerRequest.PreferredPeriod.ANY:
            errors.setdefault('preferred_period', []).append(
                'Escolha o melhor período para a visita pastoral.'
            )

        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class PastoralVisitSerializer(serializers.ModelSerializer):
    """Visita pastoral (planejamento/execução no mapa de visitação).

    Pode estar vinculada a um membro cadastrado ou a um ponto avulso. As
    coordenadas são salvas apenas na visita; o membro não é alterado.
    """

    visit_type_display = serializers.CharField(source='get_visit_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    member_name = serializers.SerializerMethodField()
    member_phone = serializers.SerializerMethodField()
    member_whatsapp_url = serializers.SerializerMethodField()
    maps_url = serializers.SerializerMethodField()
    full_address = serializers.SerializerMethodField()
    prayer_request = serializers.PrimaryKeyRelatedField(
        queryset=PrayerRequest.objects.all(),
        required=False,
        allow_null=True,
    )
    prayer_request_requester_name = serializers.SerializerMethodField()

    class Meta:
        model = PastoralVisit
        fields = [
            'id', 'church', 'member', 'member_name', 'member_phone',
            'member_whatsapp_url', 'target_name', 'target_phone', 'visit_type',
            'visit_type_display', 'status', 'status_display', 'competence_year',
            'competence_month', 'scheduled_date', 'completed_at', 'visited_by',
            'notes', 'needs_followup', 'cep', 'street', 'number', 'neighborhood',
            'city', 'state', 'full_address', 'latitude', 'longitude', 'maps_url',
            'prayer_request', 'prayer_request_requester_name',
            'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'church', 'member_name', 'member_phone', 'member_whatsapp_url',
            'status_display', 'visit_type_display', 'completed_at', 'maps_url',
            'full_address', 'prayer_request_requester_name',
            'created_by', 'created_at', 'updated_at',
        ]

    def get_prayer_request_requester_name(self, obj):
        if not obj.prayer_request_id:
            return ''
        pr = obj.prayer_request
        return 'Anônimo (Sigilo)' if pr.is_anonymous else pr.requester_name

    def get_member_name(self, obj):
        if obj.member_id:
            return obj.member.name
        return obj.target_name

    def get_member_phone(self, obj):
        if obj.member_id:
            return obj.member.phone
        return obj.target_phone

    def get_member_whatsapp_url(self, obj):
        phone = obj.member.phone if obj.member_id else obj.target_phone
        digits = re.sub(r'\D', '', phone or '')
        if len(digits) in (10, 11):
            digits = f'55{digits}'
        if len(digits) < 12 or len(digits) > 15:
            return None
        message = 'Olá! Passaremos para uma visita pastoral. Um abraço.'
        return f'https://wa.me/{digits}?text={quote(message)}'

    def get_maps_url(self, obj):
        if obj.latitude is None or obj.longitude is None:
            return None
        return (
            f'https://www.google.com/maps/search/?api=1'
            f'&query={obj.latitude},{obj.longitude}'
        )

    def get_full_address(self, obj):
        parts = [
            obj.street,
            obj.number,
            obj.neighborhood,
            obj.city,
            obj.state,
        ]
        return ', '.join(p.strip() for p in parts if (p or '').strip())

    def validate(self, attrs):
        member = attrs.get('member')
        target_name = (attrs.get('target_name') or '').strip()
        if member is None and not target_name:
            raise serializers.ValidationError(
                {'target_name': ['Informe o membro cadastrado ou o nome do ponto avulso.']}
            )
        if target_name:
            attrs['target_name'] = target_name
        prayer_request = attrs.get('prayer_request')
        if prayer_request is not None:
            request = self.context.get('request')
            user = getattr(request, 'user', None)
            if user and hasattr(user, 'church') and user.church_id is not None:
                if prayer_request.church_id != user.church_id:
                    raise serializers.ValidationError(
                        {'prayer_request': ['Este pedido não pertence à sua igreja.']}
                    )
        return attrs


class SundaySchoolClassSerializer(serializers.ModelSerializer):
    """Classe de EBD da igreja ativa (Secretaria/Pastor)."""

    category_display = serializers.CharField(source='get_category_display', read_only=True)
    enrollment_count = serializers.SerializerMethodField()

    class Meta:
        model = SundaySchoolClass
        fields = [
            'id', 'church', 'name', 'category', 'category_display',
            'teacher_name', 'co_teacher_name', 'room_location', 'is_active',
            'enrollment_count', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'church', 'category_display', 'enrollment_count', 'created_at', 'updated_at',
        ]

    def get_enrollment_count(self, obj):
        return obj.enrollments.filter(is_active=True).count()

    def validate(self, attrs):
        user = getattr(self.context.get('request'), 'user', None)
        if user and hasattr(user, 'church') and user.church_id is not None:
            attrs['church'] = user.church
        if 'name' in attrs:
            name = (attrs.get('name') or '').strip()
            if not name:
                raise serializers.ValidationError({'name': ['Informe o nome da classe.']})
            attrs['name'] = name
            if user and user.church_id and SundaySchoolClass.objects.filter(
                church_id=user.church_id, name__iexact=name,
            ).exclude(pk=getattr(self.instance, 'pk', None)).exists():
                raise serializers.ValidationError({'name': ['Já existe uma classe com este nome.']})
        if 'teacher_name' in attrs:
            teacher = (attrs.get('teacher_name') or '').strip()
            if not teacher:
                raise serializers.ValidationError({'teacher_name': ['Informe o(a) professor(a).']})
            attrs['teacher_name'] = teacher
        return attrs


class SundaySchoolEnrollmentSerializer(serializers.ModelSerializer):
    """Aluno matriculado em uma classe de EBD."""

    member_name = serializers.SerializerMethodField()
    whatsapp_url = serializers.SerializerMethodField()

    class Meta:
        model = SundaySchoolEnrollment
        fields = [
            'id', 'sunday_school_class', 'member', 'member_name',
            'student_name', 'phone', 'whatsapp_url', 'is_active', 'joined_at',
        ]
        read_only_fields = ['id', 'member_name', 'whatsapp_url', 'joined_at']

    def get_member_name(self, obj):
        return obj.member.name if obj.member_id else ''

    def get_whatsapp_url(self, obj):
        phone = (obj.phone or '').strip()
        if not phone:
            return None
        digits = re.sub(r'\D', '', phone)
        if len(digits) in (10, 11):
            digits = f'55{digits}'
        if len(digits) < 12 or len(digits) > 15:
            return None
        return f'https://wa.me/{digits}'

    def validate(self, attrs):
        user = getattr(self.context.get('request'), 'user', None)
        member = attrs.get('member')
        if member is not None and user and user.church_id is not None:
            if member.church_id != user.church_id:
                raise serializers.ValidationError(
                    {'member': ['Este membro não pertence à sua igreja.']}
                )
        student_name = (attrs.get('student_name') or '').strip()
        if not student_name:
            raise serializers.ValidationError({'student_name': ['Informe o nome do aluno.']})
        attrs['student_name'] = student_name
        attrs.setdefault('is_active', True)
        return attrs


class SundaySchoolAttendanceSerializer(serializers.ModelSerializer):
    """Presença individual na folha de chamada (com link de WhatsApp)."""

    student_name = serializers.CharField(source='enrollment.student_name', read_only=True)
    whatsapp_url = serializers.SerializerMethodField()

    class Meta:
        model = SundaySchoolAttendance
        fields = [
            'id', 'session', 'enrollment', 'student_name', 'is_present',
            'brought_bible', 'brought_magazine', 'whatsapp_url',
        ]
        read_only_fields = ['id', 'session', 'student_name', 'whatsapp_url']

    def get_whatsapp_url(self, obj):
        church = obj.session.sunday_school_class.church
        if not obj.is_present:
            url = services.build_sunday_school_whatsapp_url(
                MessageTemplate.Category.EBD_ABSENCE_RESCUE,
                obj.enrollment,
                church,
                topic=obj.session.topic,
            )
            if url:
                return url
        phone = (obj.enrollment.phone or '').strip()
        if not phone:
            return None
        digits = re.sub(r'\D', '', phone)
        if len(digits) in (10, 11):
            digits = f'55{digits}'
        return f'https://wa.me/{digits}'


class SundaySchoolSessionSerializer(serializers.ModelSerializer):
    """Aula de EBD (resumo) com presenças aninhadas e totais."""

    class_name = serializers.CharField(source='sunday_school_class.name', read_only=True)
    registered_by_name = serializers.SerializerMethodField()
    present_count = serializers.SerializerMethodField()
    attendances = SundaySchoolAttendanceSerializer(many=True, read_only=True)

    class Meta:
        model = SundaySchoolSession
        fields = [
            'id', 'sunday_school_class', 'class_name', 'date', 'topic',
            'bibles_count', 'magazines_count', 'visitors_count',
            'offering_amount', 'notes', 'registered_by', 'registered_by_name',
            'present_count', 'attendances', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'class_name', 'registered_by', 'registered_by_name',
            'present_count', 'attendances', 'created_at', 'updated_at',
        ]

    def get_registered_by_name(self, obj):
        return obj.registered_by.name if obj.registered_by_id else ''

    def get_present_count(self, obj):
        return obj.attendances.filter(is_present=True).count()