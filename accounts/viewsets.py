"""Views de autenticação, cadastro e moderação (app accounts)."""
import mimetypes
import os
import secrets
import uuid

from django.conf import settings
from django.db import transaction
from django.db.models import Count, F, Q
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.text import slugify
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import SAFE_METHODS
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView

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
    MemberContactLog,
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
    SundaySchoolAttendance,
    SundaySchoolClass,
    SundaySchoolEnrollment,
    SundaySchoolSession,
    User as UserModel,
)
from .permissions import (
    CanAccessTargetChurch,
    GrowthGroupPermission,
    IsChurchRole,
    IsSedeManager,
    IsStaffPermission,
    accessible_churches,
    can_manage_church,
    is_admin,
)
from .serializers import (
    PUBLIC_SUBMISSION_FIELDS,
    AddChurchUserSerializer,
    ChurchCreateSerializer,
    ChurchLinksConfigSerializer,
    ChurchMembershipSerializer,
    ChurchMinutesSerializer,
    ChurchProfileSerializer,
    ChurchPublicLinkSerializer,
    ChurchSerializer,
    ChurchUpdateSerializer,
    CertificateTemplateSerializer,
    EcclesiasticalCertificateSerializer,
    LoanSerializer,
    MaterialItemSerializer,
    MemberDocumentSerializer,
    MemberSerializer,
    MemberSubmissionSerializer,
    MemberTransferSerializer,
    MessageTemplateSerializer,
    MinistryAreaSerializer,
    PastoralVisitSerializer,
    PendingChurchSerializer,
    PendingCongregationSerializer,
    PublicChurchLinksSerializer,
    PublicMemberCardSerializer,
    PublicMemberFormSerializer,
    PublicMemberProfileSerializer,
    PublicSubmissionSerializer,
    RegisterSerializer,
    StorageLocationSerializer,
    UserSerializer,
    WorshipServiceSerializer,
    GrowthGroupSerializer,
    GrowthGroupPublicSerializer,
    PrayerRequestSerializer,
    PublicPrayerRequestSerializer,
    SundaySchoolClassSerializer,
    SundaySchoolEnrollmentSerializer,
    SundaySchoolSessionSerializer,
)

ALLOWED_DOC_EXTENSIONS = {'.pdf', '.png', '.jpg', '.jpeg', '.webp'}


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distância em metros entre dois pontos (fórmula de Haversine)."""
    from math import asin, cos, radians, sin, sqrt

    r = 6371000.0  # raio médio da Terra (m)
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlmb = radians(lng2 - lng1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlmb / 2) ** 2
    return 2 * r * asin(sqrt(a))


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Login com payload customizado contendo os dados do usuário/igreja.

    Bloqueia o login de congregações ainda não aprovadas pela Sede (403).
    """

    def validate(self, attrs):
        data = super().validate(attrs)
        user = self.user
        church = getattr(user, 'church', None)
        if church is not None and not church.is_approved:
            if church.church_type == Church.ChurchType.CONGREGATION:
                raise PermissionDenied(
                    'Seu cadastro foi recebido e está aguardando aprovação da Igreja Sede.'
                )
            raise PermissionDenied(
                'Seu cadastro foi recebido e está aguardando aprovação da '
                'administração da IDB.'
            )
        if not user.is_active:
            raise AuthenticationFailed(
                'Usuário inativo. Aguarde a ativação da conta.',
                code='no_active_account',
            )
        if user.church is None and not is_admin(user):
            raise PermissionDenied(
                'Sua conta não está vinculada a nenhuma igreja.'
            )
        token = self.get_token(user)
        data['access'] = str(token.access_token)
        data['refresh'] = str(token)
        data['user'] = UserSerializer(user).data
        return data


class LoginView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer


class RegisterView(APIView):
    """Cadastro público de uma congregação (aguarda aprovação da Sede)."""

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = serializer.save()

        user = result['user']
        church = result['church']
        is_sede = church.church_type == Church.ChurchType.INDEPENDENT
        detail = (
            'Sede cadastrada com sucesso! Aguarde a aprovação da administração '
            'da IDB para acessar o sistema.'
            if is_sede
            else 'Congregação cadastrada com sucesso! '
                 'Aguarde a aprovação da Igreja Sede para acessar o sistema.'
        )
        return Response(
            {
                'detail': detail,
                'church_id': church.id,
                'user': UserSerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
        )


class SwitchChurchView(APIView):
    """Altera a igreja ativa (user.church) do usuário autenticado.

    Só permite igrejas operáveis (gateway da troca de contexto): a própria
    igreja, congregações aprovadas do Pastor de Sede, ou a Sede (nos dois
    sentidos). Admins trocam para qualquer igreja.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        church_id = request.data.get('church_id')
        if not church_id:
            return Response(
                {'detail': 'Informe church_id.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        church = get_object_or_404(Church, pk=church_id)
        if not can_manage_church(request.user, church):
            raise PermissionDenied('Você não pode operar esta igreja.')
        if (
            church.church_type == Church.ChurchType.CONGREGATION
            and not church.is_approved
        ):
            return Response(
                {'detail': 'Congregação ainda não aprovada pela Sede.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        request.user.church = church
        request.user.save(update_fields=['church'])
        return Response({
            'user': UserSerializer(request.user).data,
            'detail': 'Contexto alterado com sucesso.',
        })


class MeView(APIView):
    """Sessão atual do usuário autenticado (dados frescos para o frontend)."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response({'user': UserSerializer(request.user).data})


class SearchParentChurchesView(APIView):
    """Busca assíncrona de Igrejas Independentes (Sedes) no auto-cadastro.

    Sem `q`: pré-lista 5 sedes. Com `q` (mín. 3 caracteres no frontend):
    busca por nome/cidade (retorna até 20). Pública: o cadastro em /register
    é feito por usuários não autenticados.
    """

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        q = (request.query_params.get('q') or '').strip()
        queryset = Church.objects.filter(
            church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE',
        ).order_by('name', 'city')
        if len(q) >= 3:
            queryset = queryset.filter(
                Q(name__icontains=q) | Q(city__icontains=q)
            )[:20]
        else:
            queryset = queryset[:5]
        return Response(ChurchSerializer(queryset, many=True).data)


class PendingCongregationsView(APIView):
    """Solicitações pendentes de vínculo das congregações (Sede ou ADMIN)."""

    permission_classes = [permissions.IsAuthenticated, IsSedeManager]

    def get(self, request):
        if is_admin(request.user):
            queryset = Church.objects.filter(
                status='PENDING',
                church_type=Church.ChurchType.CONGREGATION,
            ).order_by('created_at')
        else:
            church = request.user.church
            if church is None or not church.is_sede():
                raise PermissionDenied(
                    'Apenas Igrejas Independentes aprovam congregações.'
                )
            queryset = Church.objects.filter(
                status='PENDING',
                church_type=Church.ChurchType.CONGREGATION,
                parent_church=church,
            ).order_by('created_at')
        return Response(PendingCongregationSerializer(queryset, many=True).data)


class ApproveCongregationView(APIView):
    """Aprova uma congregação pendente: ativa, associa a categoria contábil e
    ativa o usuário solicitante. Restrita à Sede da congregação ou a ADMIN."""

    permission_classes = [permissions.IsAuthenticated, IsSedeManager]

    @transaction.atomic
    def post(self, request, pk):
        church = get_object_or_404(Church, pk=pk)

        if not is_admin(request.user):
            user_church = request.user.church
            if (
                user_church is None
                or church.parent_church_id != user_church.id
            ):
                raise PermissionDenied(
                    'Você só pode aprovar congregações da sua Igreja Sede.'
                )
        if church.status != 'PENDING' or church.is_approved:
            return Response(
                {'detail': 'Esta solicitação já foi avaliada.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        accounting_category = (
            request.data.get('accounting_category') or ''
        ).strip()
        if not accounting_category:
            return Response(
                {'detail': 'Informe a categoria contábil do repasse.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Categorias personalizadas (ex.: CONGREGACAO_ABC) são persistidas no
        # catálogo para passarem a existir como opção para a Sede.
        accounting_category = services.persist_accounting_category(
            accounting_category
        )

        church.status = 'ACTIVE'
        church.is_approved = True
        church.accounting_category = accounting_category
        church.save(update_fields=['status', 'is_approved', 'accounting_category'])

        user = church.responsible_user
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
        except Exception:  # noqa: BLE001
            pass

        return Response({
            'detail': 'Congregação aprovada e usuário ativado com sucesso.',
            'church': PendingCongregationSerializer(church).data,
        })


class RejectCongregationView(APIView):
    """Rejeita uma congregação pendente (Sede da congregação ou ADMIN)."""

    permission_classes = [permissions.IsAuthenticated, IsSedeManager]

    def post(self, request, pk):
        church = get_object_or_404(Church, pk=pk)

        if not is_admin(request.user):
            user_church = request.user.church
            if (
                user_church is None
                or church.parent_church_id != user_church.id
            ):
                raise PermissionDenied(
                    'Você só pode rejeitar congregações da sua Igreja Sede.'
                )
        if church.status != 'PENDING' or church.is_approved:
            return Response(
                {'detail': 'Esta solicitação já foi avaliada.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        church.status = 'REJECTED'
        church.save(update_fields=['status'])
        user = church.responsible_user
        if user is not None:
            services.send_rejection_notification(
                email=user.email,
                church_name=church.name,
                account_name=user.name,
            )
        return Response({
            'detail': 'Solicitação rejeitada.',
            'church': PendingCongregationSerializer(church).data,
        })


class ChurchViewSet(viewsets.ModelViewSet):
    """Painel de Igrejas da Sede (ou visão nacional do ADMIN).

    - LIST: igrejas acessíveis (sede + congregações; ADMIN vê tudo).
    - CREATE: admin pode criar tanto Sede (INDEPENDENT) quanto Congregação;
      sedes pastorais só criam Congregação vinculada à sua Sede. Criação
      nasce aprovada (is_approved=True, status ACTIVE) com Categoria Contábil
      obrigatória; opcionalmente vincula um responsável.
    - UPDATE/PATCH: edita dados gerais e permite reclassificar a Categoria
      Contábil vinculada.
    - DESTROY: remove a igreja em cascata (dados financeiros, dízimos,
      fechamentos, validações, eventos de calendário, membros, áreas de
      atuação e vínculos de usuários). Excluir uma Sede (apenas ADMIN)
      remove também todas as congregações relacionadas e seus dados.
    """

    serializer_class = ChurchSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_permissions(self):
        if self.action == 'create':
            # Criação de congregação é restrita a Pastor de Sede (ou ADMIN).
            return [IsSedeManager()]
        return [permissions.IsAuthenticated()]

    def get_serializer_class(self):
        if self.action == 'create':
            return ChurchCreateSerializer
        if self.action in ('update', 'partial_update'):
            return ChurchUpdateSerializer
        return ChurchSerializer

    def get_queryset(self):
        return accessible_churches(self.request.user)

    def _assert_sede_manager(self):
        user = self.request.user
        if is_admin(user):
            return None
        if (
            user.church is not None
            and user.church.is_sede()
            and user.get_role_for(user.church) == ChurchMembership.Role.PASTOR
        ):
            return user.church
        raise PermissionDenied(
            'Apenas Pastores de Igreja Independente ou Administradores '
            'podem gerenciar igrejas.'
        )

    def perform_create(self, serializer):
        data = serializer.validated_data
        user = self.request.user
        church_type = data.get('church_type', Church.ChurchType.CONGREGATION)

        if church_type == Church.ChurchType.INDEPENDENT:
            # Só admins podem criar Igrejas Sede.
            if not is_admin(user):
                raise PermissionDenied(
                    'Apenas administradores podem criar Igrejas Sede.'
                )
            parent = None
        else:
            # Congregação: validação de parent_church.
            if is_admin(user):
                parent = data.get('parent_church')
                if parent is None or not parent.is_sede():
                    raise ValidationError(
                        {'parent_church': 'Informe a Igreja Sede para a congregação.'}
                    )
                if parent.status != 'ACTIVE':
                    raise ValidationError(
                        {'parent_church': 'A Igreja Sede informada não está ativa.'}
                    )
            else:
                parent = self._assert_sede_manager()

        responsible = data.pop('responsible_user', None)
        church = serializer.save(
            church_type=church_type,
            parent_church=parent,
            status='ACTIVE',
            is_approved=True,
        )

        if responsible:
            if 'user_id' in responsible:
                u = UserModel.objects.filter(
                    pk=responsible['user_id']
                ).first()
                if u is not None:
                    services.link_church_user(
                        church=church,
                        user=u,
                        role=responsible['role'],
                    )
            else:
                services.create_church_user(
                    church=church,
                    email=responsible['email'],
                    name=responsible.get('name', ''),
                    role=responsible['role'],
                )

        # Semeia os eventos padrão do calendário financeiro, como na aprovação.
        try:
            from finance.services import seed_default_calendar_events
            seed_default_calendar_events(church)
        except Exception:  # noqa: BLE001
            pass

    def perform_update(self, serializer):
        responsible = serializer.validated_data.pop('responsible_user', None)
        if responsible is not None:
            # Troca de responsável é operação de gestão: Sede/ADMIN apenas.
            self._assert_sede_manager()
        church = serializer.save()
        if responsible:
            user = UserModel.objects.filter(pk=responsible['user_id']).first()
            if user is not None:
                self._reassign_responsible(church, user, responsible['role'])

    def _reassign_responsible(self, church, user, role):
        """Troca o responsável da congregação.

        Vincula (ou atualiza o papel de) o usuário escolhido e remove o
        vínculo do responsável anterior — mantendo o modelo de um único
        responsável por congregação.
        """
        previous = church.responsible_user
        services.link_church_user(church=church, user=user, role=role)
        if previous is not None and previous.id != user.id:
            membership = ChurchMembership.objects.filter(
                church=church, user=previous,
            ).first()
            if membership is not None:
                self._unlink_church_user(church, previous, membership)

    def _unlink_church_user(self, church, user, membership):
        """Remove o vínculo de um usuário e re-aponta User.church se preciso."""
        membership.delete()
        if user.church_id == church.id and user.church_memberships.exists():
            first = user.church_memberships.first()
            user.church = first.church
        user.save(update_fields=['church'])

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        # Exclusão de Sede é destrutiva (varre todas as congregações e dados):
        # restrita a administradores. Congregações seguem excluíveis por
        # Pastor de Sede/ADMIN.
        if instance.is_sede() and not is_admin(request.user):
            raise PermissionDenied(
                'Apenas administradores podem excluir uma Igreja Sede.'
            )
        if not instance.is_sede():
            self._assert_sede_manager()
        with transaction.atomic():
            instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ChurchUsersView(APIView):
    """Usuários (Pastor(a)/Tesoureiro(a)/Secretário(a)) da igreja da rota.

    GET: leitura para PASTOR e SECRETARIA (a Secretária apenas vê a lista).
    Criação (POST) continua restrita a PASTOR/ADMIN.
    """

    permission_classes = [IsChurchRole('PASTOR')]

    def get_permissions(self):
        if self.request.method == 'GET':
            return [IsChurchRole('PASTOR', 'SECRETARIA')()]
        return super().get_permissions()

    def _church(self, request):
        church = get_object_or_404(Church, pk=self.kwargs['church_pk'])
        if not can_manage_church(request.user, church):
            raise PermissionDenied('Você não pode gerenciar usuários desta igreja.')
        return church

    def get(self, request, church_pk):
        church = self._church(request)
        memberships = (
            ChurchMembership.objects.filter(church=church)
            .select_related('user')
            .order_by('user__name')
        )
        return Response(ChurchMembershipSerializer(memberships, many=True).data)

    @transaction.atomic
    def post(self, request, church_pk):
        church = self._church(request)
        serializer = AddChurchUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data['email'].lower()
        role = serializer.validated_data['role']
        name = serializer.validated_data.get('name', '')
        password = serializer.validated_data['password']

        user = UserModel.objects.filter(email__iexact=email).first()
        if user is None:
            user = UserModel.objects.create_user(
                email=email,
                password=password,
                name=name,
                is_active=True,
            )
        else:
            # A provisão pelo gestor redefine a senha do usuário já existente.
            user.set_password(password)
            user.save(update_fields=['password'])

        membership, _created = ChurchMembership.objects.get_or_create(
            user=user,
            church=church,
            defaults={'role': role},
        )
        if not _created and membership.role != role:
            membership.role = role
            membership.save(update_fields=['role'])

        if user.church is None:
            user.church = church
            user.is_active = True
            user.save(update_fields=['church', 'is_active'])

        return Response(
            ChurchMembershipSerializer(membership).data,
            status=status.HTTP_201_CREATED,
        )


class ChurchUserDetailView(APIView):
    """Edita o papel ou remove o vínculo de um usuário na igreja (PASTOR/ADMIN)."""

    permission_classes = [IsChurchRole('PASTOR')]

    def _target(self, request, church_pk, membership_pk):
        church = get_object_or_404(Church, pk=church_pk)
        if not can_manage_church(request.user, church):
            raise PermissionDenied('Você não pode gerenciar usuários desta igreja.')
        membership = get_object_or_404(
            ChurchMembership, pk=membership_pk, church=church,
        )
        return membership

    def patch(self, request, church_pk, membership_pk):
        membership = self._target(request, church_pk, membership_pk)
        role = request.data.get('role')
        if role not in ChurchMembership.Role.values:
            return Response(
                {'detail': 'Papel inválido.'}, status=status.HTTP_400_BAD_REQUEST,
            )
        membership.role = role
        membership.save(update_fields=['role'])
        return Response(ChurchMembershipSerializer(membership).data)

    def delete(self, request, church_pk, membership_pk):
        membership = self._target(request, church_pk, membership_pk)
        user = membership.user
        membership.delete()
        if user.church_id == membership.church_id and user.church_memberships.exists():
            first = user.church_memberships.first()
            user.church = first.church
        user.save(update_fields=['church'])
        return Response(status=status.HTTP_204_NO_CONTENT)


def apply_member_filters(qs, params):
    """Filtros combinados para o diretório de membros (query params):
    search, status, area, education, marital_status, church_entry, age_min,
    age_max. Parâmetros inválidos/desconhecidos são ignorados com segurança."""
    from datetime import date

    search = params.get('search', '').strip()
    if search:
        query = Q()
        for term in search.split():
            query &= (
                Q(name__icontains=term)
                | Q(phone__icontains=term)
                | Q(email__icontains=term)
                | Q(card_number__icontains=term)
            )
        qs = qs.filter(query)

    status = params.get('status')
    if status in Member.Status.values:
        qs = qs.filter(status=status)

    area = params.get('area')
    if area:
        qs = qs.filter(ministry_areas__id=area).distinct()

    education = params.get('education')
    if education in Member.EducationLevel.values:
        qs = qs.filter(education_level=education)

    marital_status = params.get('marital_status')
    if marital_status in Member.MaritalStatus.values:
        qs = qs.filter(marital_status=marital_status)

    church_entry = params.get('church_entry')
    if church_entry in Member.ChurchEntry.values:
        qs = qs.filter(church_entry=church_entry)

    today = date.today()
    try:
        if params.get('age_min'):
            cutoff = date(today.year - int(params['age_min']), today.month, today.day)
            qs = qs.filter(birth_date__lte=cutoff)
        if params.get('age_max'):
            cutoff = date(today.year - int(params['age_max']) - 1, today.month, today.day)
            qs = qs.filter(birth_date__gte=cutoff)
    except ValueError:
        pass
    return qs


class MemberListPagination(PageNumberPagination):
    """Paginação da listagem de membros (ativa com `?paginate=1`)."""

    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 200


class MemberSelfViewSet(viewsets.ModelViewSet):
    """CRUD de membros da igreja ativa (Secretaria/Pastor/Admin). Sem dados
    financeiros.

    `list` retorna todos quando chamado sem `paginate`, e paginado
    ({count, next, previous, results}) quando `?paginate=1`. Os filtros
    (search/status/area/education/marital_status/church_entry) aplicam-se
    nos dois modos.
    """

    serializer_class = MemberSerializer
    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO', 'INTERCESSAO')]
    pagination_class = None

    def get_permissions(self):
        """INTERCESSAO tem consulta somente-leitura (contato pastoral).

        Ações com `permission_classes` explícitas no decorator mantêm suas
        regras originais (ex.: `prepare-whatsapp` e `stage` são
        PASTOR/SECRETARIA).
        """
        if self.action in ('prepare_whatsapp', 'stage'):
            return super().get_permissions()
        if self.request and self.request.method in SAFE_METHODS:
            return [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO', 'INTERCESSAO')()]
        return [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')()]

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['church'] = self.request.user.church
        return context

    def list(self, request, *args, **kwargs):
        if request.query_params.get('paginate') != '1':
            return super().list(request, *args, **kwargs)
        self.pagination_class = MemberListPagination
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        church = self.request.user.church
        if church is None:
            return Member.objects.none()
        return apply_member_filters(
            Member.objects.filter(church=church).order_by('name'),
            self.request.query_params,
        )

    def perform_create(self, serializer):
        serializer.save(church=self.request.user.church)

    @action(detail=True, methods=['get'], url_path='public-link')
    def public_link(self, request, pk=None):
        member = self.get_object()
        member.ensure_public_hash()
        member.save(update_fields=['public_hash', 'updated_at'])
        return Response({
            'id': member.id,
            'public_url': f'{settings.FRONTEND_URL}/cartao/{member.public_hash}',
        })

    @action(detail=True, methods=['post'], url_path='public-link/regenerate')
    def public_link_regenerate(self, request, pk=None):
        member = self.get_object()
        member.regenerate_public_hash()
        return Response({
            'id': member.id,
            'public_url': f'{settings.FRONTEND_URL}/cartao/{member.public_hash}',
        })

    @action(
        detail=True, methods=['post'], url_path='prepare-whatsapp',
        permission_classes=[IsChurchRole('PASTOR', 'SECRETARIA')],
    )
    def prepare_whatsapp(self, request, pk=None):
        """Prepara uma mensagem semiautomática de WhatsApp para o membro.

        Recebe `template_id` (modelo ativo da igreja) **ou** `custom_text`
        (mensagem personalizada), interpola os tokens, valida o telefone,
        monta a URL `wa.me`, registra o contato em `MemberContactLog` e
        atualiza `last_contact_at`. A secretaria abre a URL em nova aba.
        """
        member = self.get_object()
        church = request.user.church
        payload = request.data or {}

        template_id = payload.get('template_id')
        custom_text = (payload.get('custom_text') or '').strip()
        category = (payload.get('category') or '').upper()

        if category and category not in MessageTemplate.Category.values:
            raise ValidationError({'category': 'Categoria inválida.'})

        if template_id is not None:
            template = get_object_or_404(
                MessageTemplate.objects.filter(church=church), pk=template_id
            )
            message = services.render_message_template(
                template.content, member, church
            )
            category = category or template.category
        elif custom_text:
            message = services.render_message_template(custom_text, member, church)
            category = category or MessageTemplate.Category.CUSTOM
        else:
            raise ValidationError(
                {'template_id': 'Informe um modelo ou o texto personalizado.'}
            )
        if not category:
            category = MessageTemplate.Category.CUSTOM

        digits = services.normalize_whatsapp_phone(member.phone)
        if not digits:
            raise ValidationError(
                {'phone': 'O membro não possui telefone válido para WhatsApp.'}
            )

        whatsapp_url = services.build_whatsapp_url(member.phone, message)

        MemberContactLog.objects.create(
            member=member,
            church=church,
            contacted_by=request.user,
            category=category,
            message_content=message,
        )
        member.last_contact_at = timezone.now()
        member.save(update_fields=['last_contact_at', 'updated_at'])

        return Response({
            'member_id': member.id,
            'whatsapp_url': whatsapp_url,
            'formatted_message': message,
        })

    @action(
        detail=True, methods=['patch'], url_path='stage',
        permission_classes=[IsChurchRole('PASTOR', 'SECRETARIA')],
    )
    def stage(self, request, pk=None):
        """Move o membro entre os estágios do funil pastoral."""
        member = self.get_object()
        stage_value = (
            (request.data or {}).get('lifecycle_stage')
            or (request.data or {}).get('stage')
        )
        if not stage_value or stage_value not in Member.LifecycleStage.values:
            raise ValidationError({'lifecycle_stage': 'Estágio inválido.'})
        member.lifecycle_stage = stage_value
        member.save(update_fields=['lifecycle_stage', 'updated_at'])
        return Response({
            'id': member.id,
            'lifecycle_stage': member.lifecycle_stage,
            'lifecycle_stage_display': member.get_lifecycle_stage_display(),
        })


class MemberImportInspectView(APIView):
    """Inspeciona a planilha do rol e sugere o mapeamento de colunas."""

    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]

    def post(self, request):
        file = request.FILES.get('file')
        if file is None:
            raise ValidationError({'file': 'Envie um arquivo.'})
        if request.user.church is None:
            raise PermissionDenied('Você não tem uma igreja ativa.')
        try:
            inspection = services.inspect_members_sheet(file)
        except Exception:  # noqa: BLE001
            raise ValidationError(
                {'file': 'Não foi possível ler a planilha. Use .xlsx, .xls ou .csv.'}
            )
        return Response(inspection)


class MemberImportView(APIView):
    """Importa o rol de membros (dry_run=1 apenas simula, sem gravar)."""

    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]

    def post(self, request):
        file = request.FILES.get('file')
        if file is None:
            raise ValidationError({'file': 'Envie um arquivo.'})
        if request.user.church is None:
            raise PermissionDenied('Você não tem uma igreja ativa.')
        dry_run = str(request.data.get('dry_run', '')).lower() in ('1', 'true', 'yes')
        mapping_raw = request.data.get('mapping')
        mapping = {}
        if mapping_raw:
            if isinstance(mapping_raw, str):
                import json  # noqa: PLC0415
                try:
                    mapping = json.loads(mapping_raw)
                except json.JSONDecodeError:
                    raise ValidationError({'mapping': 'Mapeamento inválido.'})
            else:
                mapping = dict(mapping_raw)
        result = services.import_member_rows(
            request.user.church,
            file,
            mapping,
            dry_run=dry_run,
        )
        if result['errors'] and not dry_run:
            raise ValidationError({'errors': result['errors']})
        return Response(result)


class MessageTemplateViewSet(viewsets.ModelViewSet):
    """CRUD dos modelos de mensagem da igreja ativa (Secretaria/Pastor)."""

    serializer_class = MessageTemplateSerializer
    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA')]
    pagination_class = None

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['church'] = self.request.user.church
        return context

    def get_queryset(self):
        church = self.request.user.church
        if church is None:
            return MessageTemplate.objects.none()
        return MessageTemplate.objects.filter(church=church).order_by(
            'category', 'title'
        )


class MinistryAreaViewSet(viewsets.ModelViewSet):
    """CRUD das áreas de atuação da igreja ativa (Secretaria/Pastor/Admin)."""

    serializer_class = MinistryAreaSerializer
    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]
    pagination_class = None

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['church'] = self.request.user.church
        return context

    def get_queryset(self):
        church = self.request.user.church
        if church is None:
            return MinistryArea.objects.none()
        return MinistryArea.objects.filter(church=church).order_by('name')

    def perform_create(self, serializer):
        serializer.save(church=self.request.user.church)


class StorageLocationViewSet(viewsets.ModelViewSet):
    """CRUD dos locais de armazenamento da igreja ativa."""

    serializer_class = StorageLocationSerializer
    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]
    pagination_class = None

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['church'] = self.request.user.church
        return context

    def get_queryset(self):
        church = self.request.user.church
        if church is None:
            return StorageLocation.objects.none()
        return StorageLocation.objects.filter(church=church).order_by('name')

    def perform_create(self, serializer):
        serializer.save(church=self.request.user.church)


class MaterialItemViewSet(viewsets.ModelViewSet):
    """CRUD dos materiais/equipamentos do inventário da igreja ativa."""

    serializer_class = MaterialItemSerializer
    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]
    pagination_class = None

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['church'] = self.request.user.church
        return context

    def get_queryset(self):
        church = self.request.user.church
        if church is None:
            return MaterialItem.objects.none()
        return MaterialItem.objects.filter(church=church).order_by('name')

    def perform_create(self, serializer):
        serializer.save(church=self.request.user.church)

    def perform_destroy(self, instance):
        if instance.loans.exists():
            raise ValidationError(
                'O material possui empréstimos registrados e não pode ser excluído.'
            )
        instance.delete()


class LoanViewSet(viewsets.ModelViewSet):
    """CRUD dos empréstimos de materiais/equipamentos da igreja ativa."""

    serializer_class = LoanSerializer
    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]
    pagination_class = None

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['church'] = self.request.user.church
        return context

    def get_queryset(self):
        church = self.request.user.church
        if church is None:
            return Loan.objects.none()
        return Loan.objects.filter(church=church).order_by('-borrowed_at', '-id')

    def perform_create(self, serializer):
        serializer.save(church=self.request.user.church, created_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(church=self.request.user.church)

    @action(detail=True, methods=['post'], url_path='return')
    def return_item(self, request, pk=None):
        """Registra a baixa da devolução do empréstimo."""
        loan = self.get_object()
        if loan.returned_at is not None:
            return Response(
                {'detail': 'Este empréstimo já foi devolvido.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        loan.returned_at = timezone.now()
        loan.returned_by = request.user
        loan.save(update_fields=['returned_at', 'returned_by', 'updated_at'])
        return Response(self.get_serializer(loan).data)


class WorshipServiceViewSet(viewsets.ModelViewSet):
    """Registro de cultos da igreja ativa (livro de cultos).

    Disponível a todos os perfis com igreja ativa (sem restrição de papel).
    """

    serializer_class = WorshipServiceSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        church = self.request.user.church
        if church is None:
            return WorshipService.objects.none()
        return WorshipService.objects.filter(church=church).order_by('-date', '-id')

    def perform_create(self, serializer):
        serializer.save(church=self.request.user.church, created_by=self.request.user)


class GrowthGroupViewSet(viewsets.ModelViewSet):
    """Gestão dos Grupos de Crescimento (GCs) da igreja ativa.

    RBAC: leitura para todos os autenticados; criar/editar para secretaria,
    tesoureiro, pastor e admin; excluir estritamente para secretaria, pastor
    e admin (tesoureiro NÃO pode excluir) — ver `GrowthGroupPermission`.

    Filtros:
    - `?search=<texto>` (nome do GC, nome do líder ou endereço — logradouro/bairro/cidade/CEP);
    - `?weekday=<0..4>` (dia de encontro permitido).
    """

    serializer_class = GrowthGroupSerializer
    permission_classes = [GrowthGroupPermission]
    pagination_class = None

    def get_queryset(self):
        church = self.request.user.church
        if church is None:
            return GrowthGroup.objects.none()
        qs = GrowthGroup.objects.filter(church=church)
        search = self.request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(leader__name__icontains=search)
                | Q(street__icontains=search)
                | Q(neighborhood__icontains=search)
                | Q(city__icontains=search)
                | Q(cep__icontains=search)
            )
        weekday = self.request.query_params.get('weekday')
        if weekday not in (None, ''):
            try:
                qs = qs.filter(weekday=int(weekday))
            except (TypeError, ValueError):
                qs = qs.none()
        return qs.order_by('weekday', 'name')

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['church'] = self.request.user.church
        return context

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save()

    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Indicadores dos cards da dashboard executiva de GCs."""
        qs = self.get_queryset()
        total_active = qs.filter(is_active=True).count()
        total_leaders = (
            qs.filter(is_active=True).values('leader').distinct().count()
        )
        most_frequent_day = None
        by_day = (
            qs.filter(is_active=True)
            .values('weekday')
            .annotate(n=Count('id'))
            .order_by('-n', 'weekday')
        )
        if by_day:
            most_frequent_day = by_day[0]['weekday']

        with_coords = list(
            qs.filter(latitude__isnull=False, longitude__isnull=False)
        )
        coverage = len(with_coords)

        # Sobreposição: pares de GCs cuja distância < r1 + r2.
        overlaps = 0
        overlap_ids = set()
        for i in range(len(with_coords)):
            a = with_coords[i]
            for b in with_coords[i + 1:]:
                distance = _haversine_m(
                    a.latitude, a.longitude, b.latitude, b.longitude
                )
                if distance < (a.radius_meters + b.radius_meters):
                    overlaps += 1
                    overlap_ids.add(a.id)
                    overlap_ids.add(b.id)

        return Response(
            {
                'total_active': total_active,
                'total_leaders': total_leaders,
                'most_frequent_day': most_frequent_day,
                'coverage': coverage,
                'overlap_count': overlaps,
                'overlap_ids': sorted(overlap_ids),
            }
        )


class ChurchMinutesViewSet(viewsets.ModelViewSet):
    """Gestão de atas da igreja ativa, com PDF opcional e link público.

    Disponível a todos os perfis com igreja ativa (sem restrição de papel).
    """

    serializer_class = ChurchMinutesSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        church = self.request.user.church
        if church is None:
            return ChurchMinutes.objects.none()
        return ChurchMinutes.objects.filter(church=church).order_by('-meeting_date', '-id')

    def perform_create(self, serializer):
        serializer.save(church=self.request.user.church, created_by=self.request.user)

    @action(detail=True, methods=['post'], url_path='regenerate-hash')
    def regenerate_hash(self, request, pk=None):
        """Regenera o hash público da ata (invalida a URL anterior)."""
        minutes = self.get_object()
        minutes.regenerate_public_hash()
        return Response(self.get_serializer(minutes).data)

    @action(detail=True, methods=['get'], url_path='pdf')
    def download_pdf(self, request, pk=None):
        """Download autenticado do PDF da ata."""
        minutes = self.get_object()
        if not minutes.pdf:
            return Response(
                {'detail': 'Esta ata não possui PDF anexado.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return FileResponse(
            minutes.pdf.open('rb'),
            as_attachment=True,
            filename=os.path.basename(minutes.pdf.name or 'ata.pdf'),
        )


class ChurchPublicLinkViewSet(viewsets.ModelViewSet):
    """CRUD de links públicos da igreja ativa (PASTOR / SECRETARIA)."""

    serializer_class = ChurchPublicLinkSerializer
    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA')]
    pagination_class = None

    DEFAULT_LINKS = (
        (ChurchPublicLink.LinkType.CALENDAR, 'Agenda de Cultos', 'calendar'),
        (ChurchPublicLink.LinkType.MEMBERSHIP, 'Ficha de Membro / Cadastro', 'user-plus'),
        (ChurchPublicLink.LinkType.PRAYER, 'Pedido de Oração', 'pray'),
        (ChurchPublicLink.LinkType.GROWTH_GROUPS, 'Grupos de Crescimento', 'users'),
    )

    def get_queryset(self):
        church = self.request.user.church
        if church is None:
            return ChurchPublicLink.objects.none()
        return ChurchPublicLink.objects.filter(church=church).order_by('order', 'id')

    def list(self, request, *args, **kwargs):
        self._ensure_defaults(request.user.church)
        return super().list(request, *args, **kwargs)

    def _ensure_defaults(self, church):
        """Garante os links padrão (Agenda de Cultos / Ficha de Membro) como
        itens do painel, para que possam ser reposicionados pelo usuário."""
        if church is None:
            return
        stored = set(
            church.public_links.filter(
                link_type__in=[lt for lt, _, _ in self.DEFAULT_LINKS]
            ).values_list('link_type', flat=True)
        )
        missing = [d for d in self.DEFAULT_LINKS if d[0] not in stored]
        if not missing:
            return
        with transaction.atomic():
            count = church.public_links.count()
            if count:
                ChurchPublicLink.objects.filter(church=church).update(
                    order=F('order') + len(missing)
                )
            for i, (link_type, title, icon) in enumerate(missing):
                ChurchPublicLink.objects.create(
                    church=church,
                    title=title,
                    link_type=link_type,
                    icon_key=icon,
                    order=i,
                    highlight=False,
                )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['church'] = self.request.user.church
        return context

    def perform_create(self, serializer):
        church = self.request.user.church
        count = ChurchPublicLink.objects.filter(church=church).count()
        serializer.save(church=church, order=count)

    @action(detail=False, methods=['post'])
    def reorder(self, request):
        """Recebe `{"order": [5, 3, 1]}` e atualiza a ordem em bloco."""
        church = request.user.church
        if church is None:
            return Response(
                {'detail': 'Nenhuma igreja ativa.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ids = request.data.get('order', [])
        if not isinstance(ids, list):
            raise ValidationError({'order': 'Esperado uma lista de IDs.'})
        with transaction.atomic():
            links = {
                l.id: l
                for l in ChurchPublicLink.objects.filter(church=church, id__in=ids)
            }
            for position, pk in enumerate(ids):
                link = links.get(pk)
                if link is None:
                    continue
                ChurchPublicLink.objects.filter(pk=pk).update(order=position)
        return Response({'status': 'ok'}, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get', 'patch'], url_path='config')
    def config(self, request):
        """Consulta e edição da configuração de links da igreja."""
        church = request.user.church
        if church is None:
            return Response(
                {'detail': 'Nenhuma igreja ativa.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if request.method == 'PATCH':
            serializer = ChurchLinksConfigSerializer(
                church, data=request.data, partial=True,
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(serializer.data)
        return Response(ChurchLinksConfigSerializer(church).data)


class PublicMinutesView(APIView):
    """Consulta pública de uma ata pelo hash (sem autenticação)."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def get(self, request, hash):
        try:
            minutes = ChurchMinutes.objects.get(public_hash=hash)
        except ChurchMinutes.DoesNotExist:
            raise NotFound('Ata não encontrada.')
        return Response({
            'id': minutes.id,
            'title': minutes.title,
            'meeting_type': minutes.meeting_type,
            'meeting_type_display': minutes.get_meeting_type_display(),
            'meeting_date': minutes.meeting_date.isoformat(),
            'location': minutes.location,
            'recorder': minutes.recorder,
            'participants': minutes.participants,
            'content': minutes.content,
            'has_pdf': bool(minutes.pdf),
            'church': {
                'id': minutes.church.id,
                'name': minutes.church.name,
                'city': minutes.church.city,
                'state': minutes.church.state,
            },
            'created_at': minutes.created_at.isoformat(),
        })


class PublicMinutesPdfView(APIView):
    """Download público do PDF da ata (via hash)."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def get(self, request, hash):
        try:
            minutes = ChurchMinutes.objects.get(public_hash=hash)
        except ChurchMinutes.DoesNotExist:
            raise NotFound('Ata não encontrada.')
        if not minutes.pdf:
            raise NotFound('Ata não encontrada.')
        return FileResponse(
            minutes.pdf.open('rb'),
            as_attachment=True,
            filename=os.path.basename(minutes.pdf.name or 'ata.pdf'),
        )


class ChurchMembersViewSet(viewsets.ModelViewSet):
    """CRUD de membros de uma igreja específica da rota (Secretaria/Pastor/Admin)."""

    serializer_class = MemberSerializer
    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]
    pagination_class = None

    def _church(self):
        church = get_object_or_404(Church, pk=self.kwargs['church_pk'])
        if not can_manage_church(self.request.user, church):
            raise PermissionDenied('Você não pode acessar os membros desta igreja.')
        return church

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['church'] = self._church()
        return context

    def get_queryset(self):
        return apply_member_filters(
            Member.objects.filter(church=self._church()).order_by('name'),
            self.request.query_params,
        )

    def perform_create(self, serializer):
        serializer.save(church=self._church())


class ChurchMinistryAreasViewSet(viewsets.ModelViewSet):
    """CRUD das áreas de atuação de uma igreja específica da rota
    (Secretaria/Pastor/Admin). Espelha ChurchMembersViewSet: a igreja da rota
    é a dona das áreas, não a igreja ativa do usuário."""

    serializer_class = MinistryAreaSerializer
    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]
    pagination_class = None

    def _church(self):
        church = get_object_or_404(Church, pk=self.kwargs['church_pk'])
        if not can_manage_church(self.request.user, church):
            raise PermissionDenied('Você não pode acessar as áreas desta igreja.')
        return church

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['church'] = self._church()
        return context

    def get_queryset(self):
        return MinistryArea.objects.filter(church=self._church()).order_by('name')

    def perform_create(self, serializer):
        serializer.save(church=self._church())


class AccountingCategoriesView(APIView):
    """Categorias contábeis disponíveis para o repasse de congregações.

    Mescla as categorias padrão do módulo financeiro com as categorias
    personalizadas já persistidas pela Sede (AccountingCategory) — que passam a
    existir como opção após serem criadas na aprovação ou na edição da
    congregação. Um texto livre ("custom_allowed") também é aceito.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from finance.models import DepartmentCategory

        standard = [{'value': c.value, 'label': c.label} for c in DepartmentCategory]
        known = {c['value'] for c in standard}
        custom = [
            {'value': c.key, 'label': c.label}
            for c in AccountingCategory.objects.exclude(key__in=known)
        ]
        return Response({
            'categories': standard + custom,
            'custom_allowed': True,
        })


class TransferTargetChurchesView(APIView):
    """Busca igrejas ativas como destino de transferência (exclui a própria)."""

    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]

    def get(self, request):
        church = request.user.church
        if church is None:
            return Response([])
        q = (request.query_params.get('q') or '').strip()
        qs = Church.objects.filter(status='ACTIVE').exclude(pk=church.id)
        if q:
            qs = qs.filter(name__icontains=q)
        qs = qs.order_by('name')[:15]
        return Response([
            {'id': c.id, 'name': c.name, 'city': c.city, 'state': c.state}
            for c in qs
        ])


class MemberTransferView(APIView):
    """Transferências emitidas pela igreja ativa (lista) e emissão."""

    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]

    def _church(self, request):
        church = request.user.church
        if church is None:
            raise PermissionDenied('Você não tem uma igreja ativa.')
        return church

    def get(self, request):
        church = self._church(request)
        queryset = MemberTransfer.objects.filter(source_church=church)
        return Response(MemberTransferSerializer(queryset, many=True).data)

    def post(self, request):
        church = self._church(request)
        member_id = request.data.get('member_id')
        target_id = request.data.get('target_church_id')
        if member_id is None or target_id is None:
            raise ValidationError({
                'detail': 'Envie member_id e target_church_id.',
            })
        member = get_object_or_404(Member, pk=member_id, church=church)
        target = get_object_or_404(Church, pk=target_id)
        transfer = services.issue_member_transfer(church, member, target)
        return Response(
            MemberTransferSerializer(transfer).data,
            status=status.HTTP_201_CREATED,
        )


class IncomingMemberTransfersView(APIView):
    """Transferências recebidas pela igreja ativa (entrada)."""

    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]

    def get(self, request):
        church = request.user.church
        if church is None:
            raise PermissionDenied('Você não tem uma igreja ativa.')
        queryset = MemberTransfer.objects.filter(target_church=church)
        return Response(MemberTransferSerializer(queryset, many=True).data)


class ReceiveMemberTransferView(APIView):
    """Recebe uma transferência pendente e cria o membro no rol."""

    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]

    def post(self, request, pk):
        church = request.user.church
        if church is None:
            raise PermissionDenied('Você não tem uma igreja ativa.')
        transfer = get_object_or_404(
            MemberTransfer, pk=pk, target_church=church,
        )
        services.receive_member_transfer(church, transfer, request.user)
        return Response(MemberTransferSerializer(transfer).data)


class CancelMemberTransferView(APIView):
    """Cancela uma transferência pendente (somente a igreja de origem)."""

    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]

    def post(self, request, pk):
        church = request.user.church
        if church is None:
            raise PermissionDenied('Você não tem uma igreja ativa.')
        transfer = get_object_or_404(
            MemberTransfer, pk=pk, source_church=church,
        )
        services.cancel_member_transfer(church, transfer, request.user)
        return Response(MemberTransferSerializer(transfer).data)


class MemberDeclarationView(APIView):
    """Gera o PDF da declaração/comprovante de membresia de um membro.

    Somente o membro deve pertencer à igreja ativa do usuário
    (Secretaria/Pastor/Admin).
    """

    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]

    def get(self, request, pk):
        church = request.user.church
        if church is None:
            raise PermissionDenied('Você não tem uma igreja ativa.')
        member = get_object_or_404(Member, pk=pk, church=church)
        signer_name = (
            church.pastor_name
            or request.user.name
            or request.user.email
        )
        pdf = services.build_membership_declaration_pdf(
            member, signer_name=signer_name, signer_role='Pastor Responsável',
        )
        filename = f'declaracao-membresia-{slugify(member.name)[:40]}.pdf'
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


class MemberDocumentsView(APIView):
    """Listagem e upload de documentos de um membro da igreja ativa."""

    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]

    def _church(self, request):
        church = request.user.church
        if church is None:
            raise PermissionDenied('Você não tem uma igreja ativa.')
        return church

    def get(self, request, pk):
        church = self._church(request)
        member = get_object_or_404(Member, pk=pk, church=church)
        docs = member.documents.all()
        return Response(MemberDocumentSerializer(docs, many=True).data)

    def post(self, request, pk):
        church = self._church(request)
        member = get_object_or_404(Member, pk=pk, church=church)
        upload = request.FILES.get('file')
        doc_type = (request.data.get('doc_type') or '').strip()
        notes = (request.data.get('notes') or '').strip()
        if upload is None:
            raise ValidationError({'file': 'Envie o arquivo (campo file).'})
        if doc_type not in MemberDocument.DocType.values:
            raise ValidationError({'doc_type': 'Tipo de documento inválido.'})
        ext = os.path.splitext(upload.name)[1].lower()
        if ext not in ALLOWED_DOC_EXTENSIONS:
            raise ValidationError({
                'file': 'Formato não permitido (use PDF, PNG ou JPG).',
            })
        doc = MemberDocument.objects.create(
            member=member,
            doc_type=doc_type,
            file=upload,
            notes=notes,
            uploaded_by=request.user,
        )
        return Response(
            MemberDocumentSerializer(doc).data,
            status=status.HTTP_201_CREATED,
        )


class MemberDocumentDetailView(APIView):
    """Exclusão de um documento (restrito à igreja do membro)."""

    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]

    def delete(self, request, pk):
        church = request.user.church
        if church is None:
            raise PermissionDenied('Você não tem uma igreja ativa.')
        doc = get_object_or_404(
            MemberDocument, pk=pk, member__church=church,
        )
        doc.file.delete(save=False)
        doc.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MemberDocumentDownloadView(APIView):
    """Download autenticado de um documento da igreja ativa."""

    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]

    def get(self, request, pk):
        church = request.user.church
        if church is None:
            raise PermissionDenied('Você não tem uma igreja ativa.')
        doc = get_object_or_404(
            MemberDocument, pk=pk, member__church=church,
        )
        try:
            content = doc.file.read()
        except Exception:  # noqa: BLE001
            raise ValidationError({'detail': 'Arquivo não encontrado.'})
        content_type = (
            mimetypes.guess_type(doc.file.name)[0]
            or 'application/octet-stream'
        )
        response = HttpResponse(content, content_type=content_type)
        response['Content-Disposition'] = (
            f'attachment; filename="{os.path.basename(doc.file.name)}"'
        )
        return response


class MemberReportPdfView(APIView):
    """PDF do rol de membros da igreja ativa (filtro por status).

    Gera um relatório portátil com todos os membros do rol (ativos por
    padrão; `?status=INACTIVE` para inativos), no padrão xhtml2pdf.
    """

    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]

    def get(self, request):
        church = request.user.church
        if church is None:
            raise PermissionDenied('Você não tem uma igreja ativa.')
        status_filter = (request.query_params.get('status') or '').strip().upper()
        qs = Member.objects.filter(church=church)
        if status_filter in {Member.Status.ACTIVE, Member.Status.INACTIVE}:
            qs = qs.filter(status=status_filter)
        else:
            qs = qs.filter(status=Member.Status.ACTIVE)
        qs = qs.order_by('name')
        filename = f'rol-membros-{slugify(church.name)[:40]}.pdf'
        pdf = services.build_members_report_pdf(church, qs)
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


class CalendarPublicLinkView(APIView):
    """URL pública (hash) do calendário geral da igreja (PASTOR/SECRETARIA).

    GET retorna o hash atual, gerando-o caso não exista ainda.
    """

    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]

    def get(self, request):
        church = request.user.church
        if church is None:
            return Response(
                {'detail': 'Você não tem uma igreja ativa.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        hash_value = church.ensure_public_hash()
        church.save(update_fields=['calendar_public_hash'])
        return Response({
            'hash': hash_value,
            'url': f'/calendario/{hash_value}',
        })


class CalendarPublicLinkRegenerateView(APIView):
    """Regenera o hash público do calendário (PASTOR/SECRETARIA).

    Regenerar invalida a URL antiga que já estiver divulgada.
    """

    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]

    def post(self, request):
        church = request.user.church
        if church is None:
            return Response(
                {'detail': 'Você não tem uma igreja ativa.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        church.calendar_public_hash = uuid.uuid4().hex
        church.save(update_fields=['calendar_public_hash'])
        return Response({
            'hash': church.calendar_public_hash,
            'url': f'/calendario/{church.calendar_public_hash}',
        })


class ChurchMemberFormLinkView(APIView):
    """Hash do formulário público de candidatos da igreja (PASTOR/SECRETARIA).

    GET  → retorna o link genérico `/formulario/{hash}` (gera se preciso).
    POST → regenera o hash (invalida o link antigo divulgado).
    """

    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]

    def get(self, request):
        church = request.user.church
        if church is None:
            return Response(
                {'detail': 'Você não tem uma igreja ativa.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        hash_value = church.ensure_member_form_hash()
        church.save(update_fields=['member_form_hash'])
        return Response({
            'hash': hash_value,
            'url': f'/formulario/{hash_value}',
        })

    def post(self, request):
        church = request.user.church
        if church is None:
            return Response(
                {'detail': 'Você não tem uma igreja ativa.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        church.member_form_hash = secrets.token_urlsafe(32)
        church.save(update_fields=['member_form_hash'])
        return Response({
            'hash': church.member_form_hash,
            'url': f'/formulario/{church.member_form_hash}',
        })


class PublicMemberCardView(APIView):
    """Cartão público de um membro pelo hash (https://.../cartao/{hash})."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def get(self, request, hash):
        try:
            member = Member.objects.select_related('church').get(
                public_hash=hash,
                status=Member.Status.ACTIVE,
            )
        except Member.DoesNotExist:
            raise NotFound('Cartão não encontrado.')
        return Response(PublicMemberCardSerializer(member).data)


class PublicMemberProfileView(APIView):
    """Perfil público do membro (https://.../perfil/{hash})."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def get(self, request, hash):
        member = (
            Member.objects.select_related('church')
            .filter(
                public_hash=hash,
                status=Member.Status.ACTIVE,
                church__status='ACTIVE',
            )
            .first()
        )
        if member is None:
            raise NotFound('Perfil não encontrado.')
        return Response(PublicMemberProfileSerializer(member).data)


class PublicMemberFormView(APIView):
    """Formulário público de membro (https://.../formulario/{hash}).

    O hash pode ser:
    - o `public_hash` de um membro  → formulário do tipo `member` (atualização);
    - o `member_form_hash` da igreja → formulário do tipo `candidate` (genérico).

    GET  → metadados (nome da igreja, tipo, e, se for membro, nome/cartão).
    POST → valida e cria a pendência de revisão (`MemberSubmission`).
    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def _resolve(self, hash):
        member = Member.objects.select_related('church').filter(public_hash=hash).first()
        if member is not None:
            return {
                'type': 'member',
                'church': member.church,
                'member': member,
            }
        church = Church.objects.filter(member_form_hash=hash).first()
        if church is not None:
            return {'type': 'candidate', 'church': church, 'member': None}
        return None

    def get(self, request, hash):
        resolved = self._resolve(hash)
        if resolved is None:
            raise NotFound('Formulário não encontrado.')
        church = resolved['church']
        meta = {
            'type': resolved['type'],
            'church_name': church.name,
            'church_city': church.city,
            'church_state': church.state,
            'church_phone': church.phone,
        }
        if resolved['member'] is not None:
            meta['member_name'] = resolved['member'].name
            meta['card_number'] = resolved['member'].card_number or ''
        return Response(PublicMemberFormSerializer(meta).data)

    def post(self, request, hash):
        resolved = self._resolve(hash)
        if resolved is None:
            raise NotFound('Formulário não encontrado.')
        serializer = PublicSubmissionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        submission = MemberSubmission.objects.create(
            church=resolved['church'],
            member=resolved['member'],
            source_hash=hash,
            data=serializer.validated_data['data'],
        )
        return Response({
            'id': submission.id,
            'status': submission.status,
        }, status=status.HTTP_201_CREATED)


class PublicChurchLinksView(APIView):
    """Página pública de links da igreja (https://.../p/{slug}).

    Aceita o slug amigável ou o `links_hash` alternativo. Retorna 404 quando
    o recurso não existe ou a página pública está desabilitada.
    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def get(self, request, slug):
        church = (
            Church.objects.select_related('parent_church')
            .filter(slug=slug, public_links_enabled=True)
            .first()
        )
        if church is None:
            church = (
                Church.objects.select_related('parent_church')
                .filter(links_hash=slug, public_links_enabled=True)
                .first()
            )
        if church is None:
            raise NotFound('Página não encontrada.')
        links = church.public_links.filter(is_active=True).order_by('order', 'id')
        return Response(
            PublicChurchLinksSerializer(
                {'church': church, 'links': links}, context={'request': request},
            ).data
        )


class PublicChurchLinkClickView(APIView):
    """Incrementa (de forma atômica) o contador de cliques de um link."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, pk):
        updated = ChurchPublicLink.objects.filter(pk=pk).update(
            click_count=F('click_count') + 1
        )
        if not updated:
            raise NotFound('Link não encontrado.')
        return Response({'status': 'ok'})


class GrowthGroupPublicView(APIView):
    """Mapa público de Grupos de Crescimento da igreja.

    Lista os GCs ativos da igreja (com link público habilitado) para o
    frontend do mapa. Segue o mesmo fluxo de `PublicChurchLinksView`: aceita
    o slug amigável e retorna 404 quando o recurso não existe ou a página
    pública está desabilitada.
    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def get(self, request, slug):
        church = (
            Church.objects.select_related('parent_church')
            .filter(slug=slug, public_links_enabled=True)
            .first()
        )
        if church is None:
            raise NotFound('Página não encontrada.')
        groups = church.growth_groups.filter(is_active=True).order_by('weekday', 'name')
        return Response(
            {
                'church': {
                    'name': church.name,
                    'city': church.city,
                    'neighborhood': church.neighborhood,
                    'state': church.state,
                    'theme_color': church.theme_color,
                    'logo': services.cloudinary_url(church.logo),
                },
                'growth_groups': GrowthGroupPublicSerializer(groups, many=True).data,
            },
            status=status.HTTP_200_OK,
        )


class MemberSubmissionsView(APIView):
    """Lista as submissões (pendências de revisão) da igreja ativa.

    PASTOR/SECRETARIA. `?status=PENDING|APPROVED|REJECTED` filtra.
    """

    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]
    pagination_class = None

    def get(self, request):
        church = request.user.church
        if church is None:
            return Response([])
        qs = MemberSubmission.objects.filter(church=church).select_related('member')
        status_filter = request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        return Response(MemberSubmissionSerializer(qs, many=True).data)


class MemberSubmissionReviewView(APIView):
    """Aprova ou rejeita uma submissão (aplica os dados ou arquiva)."""

    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'TESOUREIRO')]

    def post(self, request, pk):
        submission = MemberSubmission.objects.filter(
            pk=pk, church=request.user.church,
        ).first()
        if submission is None:
            raise NotFound('Submissão não encontrada.')
        if submission.status != MemberSubmission.Status.PENDING:
            raise ValidationError(
                {'detail': 'Esta submissão já foi revisada.'}
            )
        action = request.data.get('action', '')
        notes = (request.data.get('notes') or '').strip()
        if action == 'approve':
            self._apply(submission, request.user)
        elif action == 'reject':
            submission.notes = notes
        else:
            raise ValidationError(
                {'detail': "Informe 'action' = 'approve' ou 'reject'."}
            )
        submission.status = (
            MemberSubmission.Status.APPROVED
            if action == 'approve'
            else MemberSubmission.Status.REJECTED
        )
        submission.reviewed_at = timezone.now()
        submission.reviewed_by = request.user
        submission.save()
        return Response(MemberSubmissionSerializer(submission).data)

    def _apply(self, submission, reviewer):
        """Aplica os dados da submissão: atualiza o membro ou cria o candidato."""
        data = dict(submission.data)
        for date_field in ('birth_date', 'marriage_date'):
            if data.get(date_field) in (None, ''):
                data[date_field] = None
        photo_raw = (data.pop('photo', '') or '')
        photo_file = services.data_url_to_file(photo_raw, 'member_photo.png')
        relatives = data.pop('relatives', None) or []
        if not isinstance(relatives, list):
            relatives = []

        def save_member_photo(member):
            if photo_file is not None:
                member.photo = photo_file

        if submission.member is not None:
            for field in PUBLIC_SUBMISSION_FIELDS:
                if field in data and field not in ('name', 'photo', 'relatives'):
                    setattr(submission.member, field, data[field])
            if data.get('name'):
                submission.member.name = data['name'].strip()
            save_member_photo(submission.member)
            submission.member.save()
            submission.member.relatives.all().delete()
            self._create_relatives(submission.member, relatives)
            submission.notes = 'Dados aplicados ao membro existente.'
            return
        member_kwargs = {k: v for k, v in data.items() if k in (
            'name', 'phone', 'email', 'birth_date', 'cpf', 'rg',
            'born_in_city', 'born_in_state', 'profession',
            'education_level', 'marital_status', 'marriage_date',
            'father_name', 'mother_name', 'church_entry',
            'church_entry_other', 'street', 'number', 'complement',
            'neighborhood', 'city', 'state', 'cep', 'notes',
        )}
        if photo_file is not None:
            member_kwargs['photo'] = photo_file
        submission.member = Member.objects.create(
            church=submission.church,
            status=Member.Status.ACTIVE,
            **member_kwargs,
        )
        submission.member.card_number = services.next_member_card_number(submission.church)
        submission.member.save(update_fields=['card_number'])
        self._create_relatives(submission.member, relatives)
        submission.notes = 'Candidato criado a partir do formulário público.'

    @staticmethod
    def _create_relatives(member, relatives):
        for rel in relatives:
            MemberRelative.objects.create(
                member=member,
                name=(rel.get('name') or '').strip(),
                kinship=rel.get('kinship'),
                birth_date=rel.get('birth_date') or None,
                phone=(rel.get('phone') or '').strip(),
            )


class BirthdayMembersView(APIView):
    """Aniversariantes da igreja ativa por mês.

    Por padrão retorna os membros ATIVOS do mês (1-12) ordenados pelo dia,
    com a idade calculada. `?status=INACTIVE` filtra inativos; `?status=ALL`
    inclui ambos. Usuários sem igreja recebem lista vazia.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from django.utils import timezone  # noqa: PLC0415

        church = request.user.church
        if church is None:
            return Response([])

        month_param = request.query_params.get('month')
        if month_param is None:
            month = timezone.localdate().month
        else:
            try:
                month = int(month_param)
            except (TypeError, ValueError):
                raise ValidationError({'month': 'Mês inválido (1 a 12).'})
            if not 1 <= month <= 12:
                raise ValidationError({'month': 'Mês inválido (1 a 12).'})

        status_filter = (request.query_params.get('status') or '').strip().upper()
        qs = Member.objects.filter(
            church=church, birth_date__isnull=False, birth_date__month=month,
        )
        if status_filter in {Member.Status.ACTIVE, Member.Status.INACTIVE}:
            qs = qs.filter(status=status_filter)
        elif status_filter and status_filter != 'ALL':
            raise ValidationError({'status': 'Status inválido.'})
        elif not status_filter:
            qs = qs.filter(status=Member.Status.ACTIVE)

        qs = qs.order_by('birth_date__day', 'name')
        return Response([
            {
                'id': m.id,
                'name': m.name,
                'birth_date': m.birth_date.isoformat(),
                'day': m.birth_date.day,
                'age': services.member_age(m.birth_date),
                'phone': m.phone,
                'email': m.email,
            }
            for m in qs
        ])


class SecretaryActionsView(APIView):
    """Ações recomendadas para o painel da Secretaria (envio via WhatsApp).

    Restrito a PASTOR/SECRETARIA. Retorna listas de membros acionáveis:
    aniversariantes do dia, membros sob cuidado pastoral sem contato há >15
    dias, visitantes/em integração sem contato há >7 dias e membros com a
    carteirinha a vencer (janela de 30 dias, mesma dos alertas).
    """

    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA')]

    def get(self, request):
        church = request.user.church
        if church is None:
            actions = {
                'birthdays_today': [],
                'absent_pending_contact': [],
                'new_visitors': [],
                'cards_expiring': [],
            }
        else:
            actions = services.compute_secretary_actions(church)
        return Response({
            'generated_at': timezone.now().isoformat(),
            **actions,
        })


class AlertsView(APIView):
    """Alertas computados da igreja ativa (polling).

    Disponível para qualquer usuário autenticado com igreja ativa: aniversariantes
    (hoje e próximos 7 dias), validade da carteirinha (vence em <=30 dias ou já
    vencida) e devoluções de empréstimos (hoje, próximos 7 dias ou atrasadas —
    estes últimos somente para PASTOR/SECRETARIA/ADMIN, que acessam o inventário).
    Usuários sem igreja recebem lista vazia.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from django.utils import timezone  # noqa: PLC0415

        church = request.user.church
        if church is None:
            alerts = []
        else:
            role = request.user.get_role_for(church)
            include_loans = request.user.is_staff or role in ('PASTOR', 'SECRETARIA')
            alerts = services.compute_church_alerts(
                church,
                include_loans=include_loans,
            )
        return Response({
            'alerts': alerts,
            'generated_at': timezone.now().isoformat(),
        })


# --------------------------------------------------------------------------- #
# Painel Admin (nacional) legado — mantido para ADMIN (staff/superuser).
# --------------------------------------------------------------------------- #
class AdminChurchesView(APIView):
    """Lista todas as igrejas (todas os status) para o painel admin (apenas staff)."""

    permission_classes = [IsStaffPermission]

    def get(self, request):
        churches = (
            Church.objects.select_related('parent_church')
            .order_by('name', 'id')
        )
        serializer = PendingChurchSerializer(churches, many=True)
        return Response(serializer.data)


class AdminPendingChurchesView(APIView):
    """Lista as igrejas pendentes de aprovação (apenas staff)."""

    permission_classes = [IsStaffPermission]

    def get(self, request):
        # Apenas Igrejas Independentes caem na fila de aprovação do Admin:
        # congregações são aprovadas pela Sede na Central de Aprovação.
        churches = Church.objects.filter(
            status='PENDING',
            church_type=Church.ChurchType.INDEPENDENT,
        ).order_by('created_at')
        serializer = PendingChurchSerializer(churches, many=True)
        return Response(serializer.data)


class AdminApproveChurchView(APIView):
    """Aprova a igreja e ativa o usuário vinculado (apenas staff)."""

    permission_classes = [IsStaffPermission]

    @transaction.atomic
    def post(self, request, pk):
        church = get_object_or_404(Church, pk=pk)
        if church.church_type == Church.ChurchType.CONGREGATION:
            raise PermissionDenied(
                'Congregações são aprovadas pela Igreja Sede na Central de Aprovação.'
            )
        if church.status != 'PENDING':
            return Response(
                {'detail': f'Igreja já está com status {church.status}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        church.status = 'ACTIVE'
        church.is_approved = True
        church.save(update_fields=['status', 'is_approved'])

        user = church.responsible_user
        if user is not None:
            user.is_active = True
            user.save(update_fields=['is_active'])
            ChurchMembership.objects.get_or_create(
                user=user, church=church, defaults={'role': 'PASTOR'},
            )
            services.send_approval_notification(
                email=user.email,
                church_name=church.name,
                account_name=user.name,
            )

        # Semeia os eventos padrão do calendário financeiro.
        try:
            from finance.services import seed_default_calendar_events
            seed_default_calendar_events(church)
        except Exception:  # noqa: BLE001
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
        user = church.responsible_user
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


class ChurchProfileManageView(APIView):
    """Perfil de uma igreja específica para quem a gerencia (Sede Pastor, ativa).

    Mesmo formato do AdminChurchProfileView, mas autoriado por
    CanAccessTargetChurch: a Sede (PASTOR) opera o perfil das suas
    congregações, e usuários operam o perfil da própria igreja ativa.
    """

    permission_classes = [CanAccessTargetChurch]

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


def _set_new_user_password(user, new_password, church_name):
    """Define a nova senha do usuário e envia o e-mail (sem quebrar em erro)."""
    user.set_password(new_password)
    user.save(update_fields=['password'])
    try:
        services.send_password_reset(
            email=user.email,
            password=new_password,
            church_name=church_name,
            account_name=user.name,
        )
    except Exception:  # noqa: BLE001 — envio de e-mail não pode quebrar o reset
        pass


def _reset_church_user_password(church, new_password) -> Response | None:
    """Define a nova senha do login responsável e envia e-mail. Retorna Response 400 se não houver usuário."""
    user = church.responsible_user
    if user is None:
        return Response(
            {'detail': 'Esta igreja não possui um login responsável vinculado.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    _set_new_user_password(user, new_password, church.name)
    return None


class ResetOwnPasswordView(APIView):
    """Reset da senha do próprio usuário autenticado."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        if user.church is None and not is_admin(user):
            return Response(
                {'detail': 'Usuário sem igreja vinculada.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        new_password = request.data.get('new_password', '')
        error = _validate_new_password(new_password)
        if error is not None:
            return error
        church_name = user.church.name if user.church else user.name
        _set_new_user_password(user, new_password, church_name)
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


class CertificateTemplateViewSet(viewsets.ModelViewSet):
    """CRUD de modelos de certificados da igreja ativa (PASTOR / SECRETARIA).

    A igreja pode cadastrar molduras em imagem (media_storage) ou documentos
    base em PDF (raw_storage), além do layout padrão do sistema.
    """

    serializer_class = CertificateTemplateSerializer
    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA')]
    pagination_class = None

    def get_queryset(self):
        church = self.request.user.church
        if church is None:
            return CertificateTemplate.objects.none()
        return CertificateTemplate.objects.filter(church=church).order_by(
            '-created_at'
        )

    def perform_create(self, serializer):
        serializer.save(church=self.request.user.church)

    def perform_destroy(self, instance):
        for field in ('background_image', 'base_pdf'):
            file = getattr(instance, field, None)
            if file and file.name:
                file.delete(save=False)
        super().perform_destroy(instance)


class EcclesiasticalCertificateViewSet(viewsets.ModelViewSet):
    """Registro e emissão de certificados eclesiais (PASTOR / SECRETARIA).

    Recebe o PDF gerado no frontend (modos standard/moldura) via multipart ou
    compila o documento base (modo BASE_PDF) no backend com reportlab+pypdf.
    """

    serializer_class = EcclesiasticalCertificateSerializer
    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA')]
    pagination_class = None

    def get_queryset(self):
        church = self.request.user.church
        if church is None:
            return EcclesiasticalCertificate.objects.none()
        qs = EcclesiasticalCertificate.objects.filter(church=church)
        params = self.request.query_params
        cert_type = params.get('type')
        if cert_type in dict(CertificateTemplate.CertificateType.choices):
            qs = qs.filter(certificate_type=cert_type)
        year = (params.get('year') or '').strip()
        if year.isdigit():
            qs = qs.filter(event_date__year=year)
        search = (params.get('search') or '').strip()
        if search:
            qs = qs.filter(
                Q(recipient_name__icontains=search)
                | Q(member__name__icontains=search)
            )
        return qs.select_related('template', 'member').order_by('-created_at')

    def create(self, request, *args, **kwargs):
        from django.core.files.base import ContentFile

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        church = request.user.church
        template = serializer.validated_data.get('template')
        attachment = request.FILES.get('generated_pdf')

        if attachment:
            certificate = serializer.save(
                church=church,
                created_by=request.user,
                generated_pdf=attachment,
            )
            return Response(
                self.get_serializer(certificate).data,
                status=status.HTTP_201_CREATED,
            )

        if (
            template is not None
            and template.church_id == church.id
            and template.layout_mode == CertificateTemplate.LayoutMode.BASE_PDF
        ):
            certificate = serializer.save(church=church, created_by=request.user)
            try:
                payload = services.build_base_pdf_certificate(certificate)
            except (ValueError, OSError) as exc:
                certificate.delete()
                raise ValidationError({'generated_pdf': [str(exc)]}) from exc
            filename = (
                f'certificado_{certificate.pk}_{slugify(certificate.recipient_name)[:40]}.pdf'
            )
            certificate.generated_pdf.save(filename, ContentFile(payload))
            certificate.save(update_fields=['generated_pdf'])
            return Response(
                self.get_serializer(certificate).data,
                status=status.HTTP_201_CREATED,
            )

        raise ValidationError(
            {
                'generated_pdf': [
                    'Envie o PDF gerado ou utilize um modelo com documento base em PDF.'
                ]
            }
        )

    @action(detail=True, methods=['get'], url_path='pdf')
    def download_pdf(self, request, pk=None):
        """Reimpressão/download do PDF emitido."""
        certificate = self.get_object()
        if not certificate.generated_pdf:
            return Response(
                {'detail': 'Este certificado não possui PDF emitido.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return FileResponse(
            certificate.generated_pdf.open('rb'),
            as_attachment=True,
            filename=os.path.basename(
                certificate.generated_pdf.name or 'certificado.pdf'
            ),
        )

    def perform_destroy(self, instance):
        if instance.generated_pdf and instance.generated_pdf.name:
            instance.generated_pdf.delete(save=False)
        super().perform_destroy(instance)


class PastoralVisitViewSet(viewsets.ModelViewSet):
    """Planejamento e execução de visitas pastorais (mapa de visitação).

    Acesso a PASTOR, SECRETARIA e INTERCESSAO (ADMIN sempre passa via
    `IsChurchRole`). Filtros por competência (`year`/`month`), `status` e
    `type` atuam sobre a igreja ativa. O resumo mensal (`summary`) alimenta
    os indicadores da tela.
    """

    serializer_class = PastoralVisitSerializer
    permission_classes = [IsChurchRole('PASTOR', 'SECRETARIA', 'INTERCESSAO')]
    pagination_class = None

    def get_queryset(self):
        church = self.request.user.church
        if church is None:
            return PastoralVisit.objects.none()
        qs = PastoralVisit.objects.filter(church=church).select_related('member')
        params = self.request.query_params
        if params.get('year'):
            try:
                qs = qs.filter(competence_year=int(params['year']))
            except ValueError:
                raise ValidationError({'year': 'Ano inválido.'})
        if params.get('month'):
            try:
                qs = qs.filter(competence_month=int(params['month']))
            except ValueError:
                raise ValidationError({'month': 'Mês inválido.'})
        if params.get('status'):
            qs = qs.filter(status=params['status'].upper())
        if params.get('type'):
            qs = qs.filter(visit_type=params['type'].upper())
        return qs

    def perform_create(self, serializer):
        visit = serializer.save(
            church=self.request.user.church,
            created_by=self.request.user,
        )
        if visit.prayer_request_id:
            prayer = PrayerRequest.objects.filter(pk=visit.prayer_request_id).first()
            if prayer and prayer.church_id == visit.church_id:
                if prayer.status == PrayerRequest.Status.PENDING:
                    prayer.status = PrayerRequest.Status.VISIT_SCHEDULED
                    prayer.save(update_fields=['status'])

    def perform_destroy(self, instance):
        super().perform_destroy(instance)

    def _snapshot_from_member(self, data):
        """Quando a visita é de um membro cadastrado, leva o endereço do
        membro como ponto de partida caso a tela não envie os campos."""
        member_id = data.get('member')
        if not member_id:
            return
        member = Member.objects.filter(pk=member_id).first()
        if member is None:
            return
        for source, target in (
            ('street', 'street'),
            ('number', 'number'),
            ('neighborhood', 'neighborhood'),
            ('city', 'city'),
            ('state', 'state'),
            ('cep', 'cep'),
            ('phone', 'target_phone'),
        ):
            if not (data.get(target) or '').strip() and getattr(member, source, ''):
                data[target] = getattr(member, source)

    @action(detail=False, methods=['post'], url_path='snapshot-member')
    def snapshot_member(self, request):
        """Devolve endereço/telefone do membro para o agendamento da visita."""
        payload = request.data or {}
        member_id = payload.get('member')
        if not member_id:
            raise ValidationError({'member': ['Informe o membro.']})
        member = get_object_or_404(
            Member.objects.filter(church=request.user.church), pk=member_id
        )
        return Response({
            'id': member.id,
            'name': member.name,
            'phone': member.phone,
            'street': member.street,
            'number': member.number,
            'neighborhood': member.neighborhood,
            'city': member.city,
            'state': member.state,
            'cep': member.cep,
        })

    @action(detail=True, methods=['post'], url_path='complete')
    def complete(self, request, pk=None):
        """Registra a realização da visita com relatório pastoral."""
        visit = self.get_object()
        if visit.status == PastoralVisit.Status.COMPLETED:
            raise ValidationError({'detail': 'Visita já realizada.'})
        data = request.data or {}
        completed_at = None
        raw_completed_at = data.get('completed_at')
        if raw_completed_at:
            parsed = parse_datetime(str(raw_completed_at))
            if parsed is None:
                raise ValidationError({'completed_at': ['Data/hora inválida.']})
            completed_at = parsed
            if timezone.is_naive(completed_at):
                completed_at = timezone.make_aware(completed_at)
        visit.status = PastoralVisit.Status.COMPLETED
        visit.completed_at = completed_at or timezone.now()
        if data.get('visited_by'):
            visit.visited_by = str(data['visited_by']).strip()
        if data.get('notes'):
            visit.notes = str(data['notes']).strip()
        visit.needs_followup = bool(data.get('needs_followup'))
        visit.save(update_fields=[
            'status', 'completed_at', 'visited_by', 'notes', 'needs_followup', 'updated_at',
        ])
        return Response(PastoralVisitSerializer(visit).data)

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        """Cancela uma visita ainda planejada."""
        visit = self.get_object()
        if visit.status != PastoralVisit.Status.PLANNED:
            raise ValidationError({'detail': 'Apenas visitas planejadas podem ser canceladas.'})
        visit.status = PastoralVisit.Status.CANCELLED
        visit.save(update_fields=['status', 'updated_at'])
        return Response(PastoralVisitSerializer(visit).data)

    @action(detail=False, methods=['get'], url_path='summary')
    def summary(self, request):
        """Totais do mês de competência informado (padrão: mês atual)."""
        today = timezone.now().date()
        year = int(request.query_params.get('year') or today.year)
        month = int(request.query_params.get('month') or today.month)
        church = request.user.church
        if church is None:
            return Response(self._empty_summary(year, month))
        qs = PastoralVisit.objects.filter(
            church=church, competence_year=year, competence_month=month
        )
        completed = qs.filter(status=PastoralVisit.Status.COMPLETED)
        top_neighborhoods = (
            completed.filter(neighborhood__isnull=False)
            .exclude(neighborhood='')
            .values('neighborhood')
            .annotate(count=Count('id'))
            .order_by('-count')[:5]
        )
        return Response({
            'year': year,
            'month': month,
            'total': qs.count(),
            'planned': qs.filter(status=PastoralVisit.Status.PLANNED).count(),
            'completed': completed.count(),
            'cancelled': qs.filter(status=PastoralVisit.Status.CANCELLED).count(),
            'needs_followup': completed.filter(needs_followup=True).count(),
            'top_neighborhoods': [
                {'neighborhood': row['neighborhood'], 'count': row['count']}
                for row in top_neighborhoods
            ],
        })

    @staticmethod
    def _empty_summary(year, month):
        return {
            'year': year,
            'month': month,
            'total': 0,
            'planned': 0,
            'completed': 0,
            'cancelled': 0,
            'needs_followup': 0,
            'top_neighborhoods': [],
        }


class PrayerRequestViewSet(viewsets.ModelViewSet):
    """Triagem administrativa dos Pedidos de Oração (Intercessão/Pastoral).

    Permite listar, editar status/responsável/notas e gerar o Caderno de
    Oração em PDF. A captação pública (sem login) acontece na rota
    `public/churches/<slug>/prayer-requests/`.
    """

    serializer_class = PrayerRequestSerializer
    permission_classes = [IsChurchRole('INTERCESSAO', 'PASTOR', 'SECRETARIA')]
    pagination_class = None

    def get_queryset(self):
        church = self.request.user.church
        if church is None:
            return PrayerRequest.objects.none()
        qs = PrayerRequest.objects.filter(church=church).select_related('assigned_to')
        params = self.request.query_params
        if params.get('status'):
            status_value = params['status'].upper()
            if status_value in PrayerRequest.Status.values:
                qs = qs.filter(status=status_value)
        if params.get('category'):
            category = params['category'].upper()
            if category in PrayerRequest.Category.values:
                qs = qs.filter(category=category)
        if params.get('wants_visit') in ('1', 'true', 'True'):
            qs = qs.filter(wants_visit=True)
        search = params.get('q', '').strip()
        if search:
            qs = qs.filter(
                Q(requester_name__icontains=search)
                | Q(requester_phone__icontains=search)
                | Q(description__icontains=search)
                | Q(neighborhood__icontains=search)
            )
        return qs

    def list(self, request, *args, **kwargs):
        if request.query_params.get('paginate') != '1':
            return super().list(request, *args, **kwargs)
        self.pagination_class = MemberListPagination
        return super().list(request, *args, **kwargs)

    @action(detail=True, methods=['post'], url_path='prepare-whatsapp')
    def prepare_whatsapp(self, request, pk=None):
        """Gera link wa.me com saudação pastoral para contato imediato."""
        from urllib.parse import quote

        request_obj = self.get_object()
        phone = (request_obj.requester_phone or '').strip()
        if not phone:
            raise ValidationError({'detail': 'Solicitação sem telefone cadastrado.'})
        data = PrayerRequestSerializer(request_obj).data
        url = data.get('whatsapp_url')
        if not url:
            raise ValidationError({'detail': 'Número de WhatsApp inválido.'})
        return Response({'id': request_obj.id, 'url': url, 'phone': phone})

    @action(detail=False, methods=['get'], url_path='print-sheet')
    def print_sheet(self, request):
        """Caderno de Oração em PDF (motivos ativos da igreja)."""
        active = (
            PrayerRequest.objects.filter(
                church=request.user.church,
            )
            .exclude(status=PrayerRequest.Status.ARCHIVED)
            .order_by('created_at')
        )
        pdf = services.build_prayer_book_pdf(request.user.church, active)
        filename = f'caderno-oracao-{timezone.localdate().isoformat()}.pdf'
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


class PublicPrayerRequestView(APIView):
    """Captação pública de pedidos de oração via agregador de links (/p/<slug>)."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, slug):
        church = (
            Church.objects.filter(slug=slug, public_links_enabled=True).first()
            or Church.objects.filter(links_hash=slug, public_links_enabled=True).first()
        )
        if church is None:
            raise NotFound('Página não encontrada.')
        serializer = PublicPrayerRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        prayer_request = serializer.save(
            church=church,
            status=PrayerRequest.Status.PENDING,
        )
        return Response(
            PublicPrayerRequestSerializer(prayer_request).data,
            status=status.HTTP_201_CREATED,
        )


class SundaySchoolClassViewSet(viewsets.ModelViewSet):
    """CRUD das classes de EBD da igreja ativa (Secretaria/Pastor)."""

    serializer_class = SundaySchoolClassSerializer
    permission_classes = [IsChurchRole('SECRETARIA', 'PASTOR')]
    pagination_class = None

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['church'] = self.request.user.church
        return context

    def get_queryset(self):
        church = self.request.user.church
        if church is None:
            return SundaySchoolClass.objects.none()
        return SundaySchoolClass.objects.filter(church=church).order_by('name')

    def perform_create(self, serializer):
        serializer.save(church=self.request.user.church)

    @action(detail=True, methods=['get', 'post', 'patch', 'delete'], url_path='students')
    def students(self, request, pk=None):
        """Alunos da classe: lista (GET), matricula (POST), edita (PATCH) e remove (DELETE).

        Para o PATCH, informe `enrollment_id` no corpo junto com os campos
        a atualizar (ex.: `{"enrollment_id": 7, "student_name": "..."}`).

        Para o DELETE, informe `enrollment_id` no corpo (`{"enrollment_id": 7}`)
        ou na query string `?enrollment_id=7`.
        """
        sunday_class = self.get_object()
        if request.method == 'GET':
            qs = sunday_class.enrollments.select_related('member', 'sunday_school_class')
            search = (request.query_params.get('q') or '').strip()
            if search:
                qs = qs.filter(
                    Q(student_name__icontains=search) | Q(phone__icontains=search)
                )
            return Response(
                SundaySchoolEnrollmentSerializer(
                    qs, many=True, context={'request': request}
                ).data
            )
        if request.method == 'POST':
            data = {
                **(request.data or {}),
                'sunday_school_class': sunday_class.id,
            }
            serializer = SundaySchoolEnrollmentSerializer(
                data=data, context={'request': request}
            )
            serializer.is_valid(raise_exception=True)
            serializer.save(sunday_school_class=sunday_class)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        if request.method == 'PATCH':
            payload = request.data or {}
            enrollment_id = payload.get('enrollment_id')
            enrollment = get_object_or_404(sunday_class.enrollments, pk=enrollment_id)
            serializer = SundaySchoolEnrollmentSerializer(
                enrollment, data=payload, partial=True, context={'request': request}
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(serializer.data)

        payload = request.data or {}
        enrollment_id = payload.get('enrollment_id') or request.query_params.get('enrollment_id')
        enrollment = get_object_or_404(sunday_class.enrollments, pk=enrollment_id)
        enrollment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'], url_path='prepare-whatsapp')
    def prepare_whatsapp(self, request, pk=None):
        """Gera os links `wa.me` do aviso de aula para todos os alunos da turma.

        Aceita `kind` (categoria do template EBD) e `topic` (tema da próxima
        aula). Cada aluno com telefone válido recebe um link pronto para envio.
        """
        kind = (request.data or {}).get('kind') or MessageTemplate.Category.EBD_CLASS_ANNOUNCEMENT
        if kind not in MessageTemplate.Category.values:
            raise ValidationError({'kind': 'Categoria de mensagem inválida.'})
        topic = (request.data or {}).get('topic') or ''
        sunday_class = self.get_object()
        rows = []
        for enrollment in sunday_class.enrollments.filter(is_active=True).select_related(
            'sunday_school_class'
        ):
            url = services.build_sunday_school_whatsapp_url(
                kind, enrollment, sunday_class.church, topic=topic,
            )
            if url:
                rows.append({
                    'enrollment_id': enrollment.id,
                    'student_name': enrollment.student_name,
                    'phone': enrollment.phone or '',
                    'url': url,
                })
        return Response({
            'class_id': sunday_class.id,
            'class_name': sunday_class.name,
            'rows': rows,
        })


class SundaySchoolSessionViewSet(viewsets.ModelViewSet):
    """Aulas EBD: lista por classe/data, prepara a folha de chamada e salva."""

    serializer_class = SundaySchoolSessionSerializer
    permission_classes = [IsChurchRole('SECRETARIA', 'PASTOR')]
    pagination_class = None

    def get_queryset(self):
        church = self.request.user.church
        if church is None:
            return SundaySchoolSession.objects.none()
        qs = SundaySchoolSession.objects.filter(
            sunday_school_class__church=church,
        ).select_related('sunday_school_class', 'registered_by')
        class_id = self.request.query_params.get('class_id')
        if class_id:
            qs = qs.filter(sunday_school_class_id=class_id)
        return qs

    def list(self, request, *args, **kwargs):
        """Com `class_id` + `date` devolve a folha completa; senão, o histórico."""
        params = request.query_params
        class_id = params.get('class_id')
        date_value = params.get('date')
        if class_id and date_value:
            session_date = parse_date(date_value)
            if session_date is None:
                raise ValidationError({'date': 'Data inválida. Use YYYY-MM-DD.'})
            sunday_class = get_object_or_404(
                SundaySchoolClass.objects.filter(church=request.user.church),
                pk=class_id,
            )
            session, _ = self._prepare_or_get(sunday_class, session_date)
            serializer = SundaySchoolSessionSerializer(
                session, context={'request': request}
            )
            return Response(serializer.data)
        return super().list(request, *args, **kwargs)

    def _prepare_or_get(self, sunday_class, session_date):
        """Busca a aula; se ainda não existir, prepara a folha de chamada."""
        session = SundaySchoolSession.objects.filter(
            sunday_school_class=sunday_class,
            date=session_date,
        ).first()
        if session is not None:
            return session, False
        with transaction.atomic():
            session = SundaySchoolSession.objects.create(
                sunday_school_class=sunday_class,
                date=session_date,
                registered_by=self.request.user,
            )
            rows = [
                SundaySchoolAttendance(session=session, enrollment=enrollment)
                for enrollment in sunday_class.enrollments.filter(is_active=True)
            ]
            if rows:
                SundaySchoolAttendance.objects.bulk_create(rows)
        return session, True

    def create(self, request, *args, **kwargs):
        """Consolida a aula: salva resumo + presenças em transação atômica."""
        payload = request.data or {}
        church = request.user.church
        class_id = payload.get('sunday_school_class')
        date_value = payload.get('date')
        if not class_id or not date_value:
            raise ValidationError({'detail': 'Informe sunday_school_class e date.'})
        if church is None:
            raise NotFound('Igreja não definida.')
        sunday_class = get_object_or_404(
            SundaySchoolClass.objects.filter(church=church), pk=class_id,
        )
        session_date = parse_date(str(date_value))
        if session_date is None:
            raise ValidationError({'date': 'Data inválida. Use YYYY-MM-DD.'})

        from decimal import Decimal  # noqa: PLC0415

        with transaction.atomic():
            session, _ = SundaySchoolSession.objects.update_or_create(
                sunday_school_class=sunday_class,
                date=session_date,
                defaults={
                    'topic': (payload.get('topic') or '').strip(),
                    'bibles_count': int(payload.get('bibles_count') or 0),
                    'magazines_count': int(payload.get('magazines_count') or 0),
                    'visitors_count': int(payload.get('visitors_count') or 0),
                    'offering_amount': Decimal(str(payload.get('offering_amount') or '0.00')),
                    'notes': (payload.get('notes') or '').strip(),
                    'registered_by': request.user,
                },
            )
            active_enrollments = sunday_class.enrollments.filter(is_active=True)
            for row in payload.get('attendance') or []:
                enrollment_id = row.get('enrollment_id')
                enrollment = active_enrollments.filter(pk=enrollment_id).first()
                if enrollment is None:
                    raise ValidationError({
                        'attendance': f'Matrícula inválida ou inativa: {enrollment_id}',
                    })
                SundaySchoolAttendance.objects.update_or_create(
                    session=session,
                    enrollment=enrollment,
                    defaults={
                        'is_present': bool(row.get('is_present', False)),
                        'brought_bible': bool(row.get('brought_bible', False)),
                        'brought_magazine': bool(row.get('brought_magazine', False)),
                    },
                )

        serializer = SundaySchoolSessionSerializer(
            session, context={'request': request}
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class SundaySchoolMonthlyReportView(APIView):
    """Relatório mensal de EBD (matriz aluno × domingos + totais por classe)."""

    permission_classes = [IsChurchRole('SECRETARIA', 'PASTOR')]

    def get(self, request):
        church = request.user.church
        if church is None:
            raise NotFound('Igreja não definida.')
        params = request.query_params
        year = int(params.get('year') or timezone.localdate().year)
        month = int(params.get('month') or timezone.localdate().month)
        if month < 1 or month > 12:
            raise ValidationError({'month': 'Mês inválido.'})
        class_id = params.get('class_id')
        report = services.build_sunday_school_monthly_report(
            church, year, month, int(class_id) if class_id else None,
        )
        return Response(report)


class SundaySchoolMonthlyReportPdfView(SundaySchoolMonthlyReportView):
    """Exporta o Relatório Mensal de EBD em PDF (xhtml2pdf)."""

    def get(self, request):
        church = request.user.church
        params = request.query_params
        year = int(params.get('year') or timezone.localdate().year)
        month = int(params.get('month') or timezone.localdate().month)
        class_id = params.get('class_id')
        report = services.build_sunday_school_monthly_report(
            church, year, month, int(class_id) if class_id else None,
        )
        pdf = services.build_sunday_school_report_pdf(church, report)
        filename = f'relatorio-ebd-{year}-{month:02d}.pdf'
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response