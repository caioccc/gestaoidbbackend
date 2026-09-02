"""Views de autenticação, cadastro e moderação (app accounts)."""
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import Church, User as UserModel
from . import services
from .serializers import (
    ChurchProfileSerializer,
    PendingChurchSerializer,
    RegisterSerializer,
    UserSerializer,
)


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Login com payload customizado contendo os dados do usuário/igreja."""

    def validate(self, attrs):
        data = super().validate(attrs)
        user = self.user
        token = self.get_token(user)
        data['access'] = str(token.access_token)
        data['refresh'] = str(token)
        data['user'] = UserSerializer(user).data
        return data


class LoginView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer


class RegisterView(APIView):
    """Cadastro público da congregação (Church PENDING + User inativo)."""

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = serializer.save()

        user = result['user']
        church = result['church']
        return Response(
            {
                'detail': (
                    'Igreja cadastrada com sucesso! '
                    'Aguarde a aprovação de um moderador para acessar o sistema.'
                ),
                'church_id': church.id,
                'user': UserSerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
        )


class IsStaffPermission(permissions.BasePermission):
    """Permite acesso apenas a usuários com is_staff=True."""

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_staff
        )


class AdminChurchesView(APIView):
    """Lista todas as igrejas (todas os status) para o painel admin (apenas staff)."""

    permission_classes = [IsStaffPermission]

    def get(self, request):
        churches = Church.objects.all().order_by('name', 'id')
        serializer = PendingChurchSerializer(churches, many=True)
        return Response(serializer.data)


class AdminPendingChurchesView(APIView):
    """Lista as igrejas pendentes de aprovação (apenas staff)."""

    permission_classes = [IsStaffPermission]

    def get(self, request):
        churches = Church.objects.filter(status='PENDING').order_by('created_at')
        serializer = PendingChurchSerializer(churches, many=True)
        return Response(serializer.data)


class AdminApproveChurchView(APIView):
    """Aprova a igreja e ativa o usuário vinculado (apenas staff)."""

    permission_classes = [IsStaffPermission]

    @transaction.atomic
    def post(self, request, pk):
        church = get_object_or_404(Church, pk=pk)
        if church.status != 'PENDING':
            return Response(
                {'detail': f'Igreja já está com status {church.status}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        church.status = 'ACTIVE'
        church.save()

        user = getattr(church, 'user_account', None)
        if user is not None:
            user.is_active = True
            user.save(update_fields=['is_active'])
            services.send_approval_notification(
                email=user.email,
                church_name=church.name,
                account_name=user.name,
            )

        # Semeia os eventos padrão do calendário financeiro.
        try:
            from finance.services import seed_default_calendar_events
            seed_default_calendar_events(church)
        except Exception:
            pass

        return Response(
            {
                'detail': 'Igreja aprovada e usuário ativado com sucesso.',
                'church': PendingChurchSerializer(church).data,
            }
        )


class AdminRejectChurchView(APIView):
    """Rejeita a igreja (apenas staff)."""

    permission_classes = [IsStaffPermission]

    def post(self, request, pk):
        church = get_object_or_404(Church, pk=pk)
        church.status = 'REJECTED'
        church.save()
        user = getattr(church, 'user_account', None)
        if user is not None:
            services.send_rejection_notification(
                email=user.email,
                church_name=church.name,
                account_name=user.name,
            )
        return Response(
            {
                'detail': 'Igreja rejeitada.',
                'church': PendingChurchSerializer(church).data,
            }
        )


class ProfileView(APIView):
    """Consulta e atualização dos dados da congregação do usuário."""

    permission_classes = [permissions.IsAuthenticated]

    def _get_church(self, request):
        church = request.user.church
        if church is None:
            return None
        return church

    def get(self, request):
        church = self._get_church(request)
        if church is None:
            return Response(
                {'detail': 'Usuário sem igreja vinculada.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(ChurchProfileSerializer(church).data)

    def put(self, request):
        church = self._get_church(request)
        if church is None:
            return Response(
                {'detail': 'Usuário sem igreja vinculada.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = ChurchProfileSerializer(church, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def patch(self, request):
        return self.put(request)


class AdminChurchProfileView(APIView):
    """Consulta e atualização do perfil de uma igreja específica (apenas staff)."""

    permission_classes = [IsStaffPermission]

    def _church(self, pk):
        return get_object_or_404(Church, pk=pk)

    def get(self, request, pk):
        church = self._church(pk)
        return Response(ChurchProfileSerializer(church).data)

    def put(self, request, pk):
        church = self._church(pk)
        serializer = ChurchProfileSerializer(church, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def patch(self, request, pk):
        return self.put(request, pk)


def _validate_new_password(password) -> Response | None:
    """Valida a nova senha. Retorna None quando válida, ou uma Response 400."""
    if not isinstance(password, str) or not password:
        return Response(
            {'detail': 'Informe a nova senha.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if (
        len(password) < 6
        or not any(c.isalpha() for c in password)
        or not any(c.isdigit() for c in password)
    ):
        return Response(
            {'detail': 'A senha deve ter ao menos 6 caracteres, com letras e números.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


def _reset_church_user_password(church, new_password) -> Response | None:
    """Define a nova senha do login responsável e envia e-mail. Retorna Response 400 se não houver usuário."""
    user = getattr(church, 'user_account', None)
    if user is None:
        return Response(
            {'detail': 'Esta igreja não possui um login responsável vinculado.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    user.set_password(new_password)
    user.save(update_fields=['password'])
    try:
        services.send_password_reset(
            email=user.email,
            password=new_password,
            church_name=church.name,
            account_name=user.name,
        )
    except Exception:  # noqa: BLE001 — envio de e-mail não pode quebrar o reset
        pass
    return None


class ResetOwnPasswordView(APIView):
    """Reset da senha do login responsável pelo próprio usuário."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        church = request.user.church
        if church is None:
            return Response(
                {'detail': 'Usuário sem igreja vinculada.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        new_password = request.data.get('new_password', '')
        error = _validate_new_password(new_password)
        if error is not None:
            return error
        result = _reset_church_user_password(church, new_password)
        if result is not None:
            return result
        return Response({'detail': 'Senha redefinida com sucesso.'}, status=status.HTTP_200_OK)


class AdminChurchResetPasswordView(APIView):
    """Reset da senha do login responsável de uma igreja (apenas staff)."""

    permission_classes = [IsStaffPermission]

    def post(self, request, pk):
        church = get_object_or_404(Church, pk=pk)
        new_password = request.data.get('new_password', '')
        error = _validate_new_password(new_password)
        if error is not None:
            return error
        result = _reset_church_user_password(church, new_password)
        if result is not None:
            return result
        return Response({'detail': 'Senha redefinida com sucesso.'}, status=status.HTTP_200_OK)


class AdminChurchClearDataView(APIView):
    """Apaga todos os dados financeiros de uma igreja (apenas staff).

    Mantém o perfil da igreja e a conta vinculada; remove entradas, saídas,
    dizimistas, registros de dízimo, fechamentos e eventos do calendário.
    """

    permission_classes = [IsStaffPermission]

    @transaction.atomic
    def post(self, request, pk):
        church = get_object_or_404(Church, pk=pk)
        from finance.models import (
            CalendarEvent,
            FinancialEntry,
            FinancialExit,
            MonthlyClosing,
            Tither,
            TitheRecord,
        )

        counts = {}
        counts['entries'] = FinancialEntry.objects.filter(church=church).count()
        counts['exits'] = FinancialExit.objects.filter(church=church).count()
        counts['tithers'] = Tither.objects.filter(church=church).count()
        counts['tithe_records'] = TitheRecord.objects.filter(
            tither__church=church
        ).count()
        counts['closings'] = MonthlyClosing.objects.filter(church=church).count()
        counts['calendar_events'] = CalendarEvent.objects.filter(church=church).count()

        FinancialEntry.objects.filter(church=church).delete()
        FinancialExit.objects.filter(church=church).delete()
        # Tither/TitheRecord se excluem em cascata por tither.
        Tither.objects.filter(church=church).delete()
        MonthlyClosing.objects.filter(church=church).delete()
        CalendarEvent.objects.filter(church=church).delete()

        # Re-semeia os eventos padrão do calendário.
        from finance.services import seed_default_calendar_events
        seed_default_calendar_events(church)

        return Response(
            {
                'detail': 'Todos os dados financeiros foram apagados com sucesso.',
                'deleted': counts,
            }
        )
