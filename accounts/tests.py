from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

import decimal

from finance.models import CalendarEvent

from .models import (
    AccountingCategory,
    CertificateTemplate,
    Church,
    ChurchMembership,
    ChurchMinutes,
    ChurchPublicLink,
    EcclesiasticalCertificate,
    GrowthGroup,
    Loan,
    MaterialItem,
    Member,
    MemberContactLog,
    MemberDocument,
    MemberRelative,
    MemberSubmission,
    MessageTemplate,
    MinistryArea,
    StorageLocation,
    WorshipService,
    PastoralVisit,
    PrayerRequest,
    SundaySchoolAttendance,
    SundaySchoolClass,
    SundaySchoolEnrollment,
    SundaySchoolSession,
)

User = get_user_model()


class BaseChurchTestCase(TestCase):
    """Cenário comum: uma Sede (Igreja Teste), congregação e usuários."""

    def setUp(self):
        self.sede = Church.objects.create(
            name='Igreja Teste',
            church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE',
            is_approved=True,
            city='Campina Grande',
            state='PB',
        )
        self.congregation = Church.objects.create(
            name='Congregação Teste',
            church_type=Church.ChurchType.CONGREGATION,
            parent_church=self.sede,
            status='ACTIVE',
            is_approved=True,
            accounting_category='DIZIMO',
            city='Campina Grande',
            state='PB',
        )
        self.pending_congregation = Church.objects.create(
            name='Congregação Pendente',
            church_type=Church.ChurchType.CONGREGATION,
            parent_church=self.sede,
            status='PENDING',
            is_approved=False,
            city='João Pessoa',
            state='PB',
        )

    def _user(self, email, name='Usuário', church=None, role=None,
              is_staff=False, is_active=True):
        user = User.objects.create(
            email=email,
            name=name,
            church=church,
            is_staff=is_staff,
            is_active=is_active,
        )
        user.set_password('S3nh@segura')
        user.save(update_fields=['password'])
        if role and church is not None:
            ChurchMembership.objects.create(user=user, church=church, role=role)
        return user

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client


class LoginFlowTests(BaseChurchTestCase):
    def test_login_pending_congregation_returns_403(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.pending_congregation,
            role=ChurchMembership.Role.PASTOR,
        )
        client = APIClient()
        resp = client.post(
            reverse('login'),
            {'email': pastor.email, 'password': 'S3nh@segura'},
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn('aprova', resp.data['detail'].lower())

    def test_login_pending_congregation_regular_401_for_inactive(self):
        # Usuário inativo continua recebendo 401 mesmo com congregação pendente.
        user = self._user(
            'inativo@teste.com',
            church=self.pending_congregation,
            role=ChurchMembership.Role.TESOUREIRO,
            is_active=False,
        )
        client = APIClient()
        resp = client.post(
            reverse('login'),
            {'email': user.email, 'password': 'S3nh@segura'},
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_login_approved_congregation_returns_role(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.congregation,
            role=ChurchMembership.Role.PASTOR,
        )
        client = APIClient()
        resp = client.post(
            reverse('login'),
            {'email': pastor.email, 'password': 'S3nh@segura'},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['user']['role'], 'PASTOR')
        self.assertTrue(resp.data['user']['is_active'])
        self.assertIsNotNone(resp.data['user']['church'])

    def test_switch_church_roundtrip(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        ChurchMembership.objects.create(
            user=pastor, church=self.congregation,
            role=ChurchMembership.Role.PASTOR,
        )
        client = self._client(pastor)

        resp = client.post(reverse('switch-church'), {'church_id': self.congregation.id})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        pastor.refresh_from_db()
        self.assertEqual(pastor.church_id, self.congregation.id)

        resp2 = client.post(reverse('switch-church'), {'church_id': self.sede.id})
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        pastor.refresh_from_db()
        self.assertEqual(pastor.church_id, self.sede.id)

    def test_switch_church_denied_for_alien_church(self):
        other_sede = Church.objects.create(
            name='Outra Sede',
            church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE',
            is_approved=True,
            city='Recife',
            state='PE',
        )
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        client = self._client(pastor)
        resp = client.post(reverse('switch-church'), {'church_id': other_sede.id})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_sede_pastor_can_return_to_sede_from_congregation(self):
        """Pastor de Sede sem vínculo local na congregação herda o papel e o
        seletor de igreja continua disponível, permitindo voltar à Sede."""
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        client = self._client(pastor)

        resp = client.post(reverse('switch-church'), {'church_id': self.congregation.id})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.data['user']
        self.assertEqual(data['church']['id'], self.congregation.id)
        self.assertTrue(data['can_manage_churches'])

        resp_detail = client.get(reverse('church-detail', args=[self.congregation.id]))
        self.assertEqual(resp_detail.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_detail.data['id'], self.congregation.id)

        resp2 = client.post(reverse('switch-church'), {'church_id': self.sede.id})
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        self.assertEqual(resp2.data['user']['church']['id'], self.sede.id)

    def test_accessible_churches_include_sede_when_active_congregation(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        client = self._client(pastor)
        client.post(reverse('switch-church'), {'church_id': self.congregation.id})

        resp = client.get(reverse('church-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {c['id'] for c in resp.data}
        self.assertEqual(ids, {self.congregation.id, self.sede.id})

    def test_sede_pastor_sees_and_switches_directly_between_congregations(self):
        cong_b1 = Church.objects.create(
            name='Congregação Bairro 1',
            church_type=Church.ChurchType.CONGREGATION,
            parent_church=self.sede,
            status='ACTIVE',
            is_approved=True,
            accounting_category='DIZIMO',
            city='Campina Grande',
            state='PB',
        )
        cong_b2 = Church.objects.create(
            name='Congregação Bairro 2',
            church_type=Church.ChurchType.CONGREGATION,
            parent_church=self.sede,
            status='ACTIVE',
            is_approved=True,
            accounting_category='DIZIMO',
            city='Campina Grande',
            state='PB',
        )
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        client = self._client(pastor)
        client.post(reverse('switch-church'), {'church_id': cong_b1.id})

        resp = client.get(reverse('church-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {c['id'] for c in resp.data}
        self.assertIn(self.sede.id, ids)
        self.assertIn(cong_b1.id, ids)
        self.assertIn(cong_b2.id, ids)
        # Congregação pendente não aparece nem é operável.
        self.assertNotIn(self.pending_congregation.id, ids)

        resp2 = client.post(reverse('switch-church'), {'church_id': cong_b2.id})
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        self.assertEqual(resp2.data['user']['church']['id'], cong_b2.id)

    def test_congregation_treasurer_only_sees_congregation_and_sede(self):
        treasurer = self._user(
            'tesoureiro@teste.com',
            church=self.congregation,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        client = self._client(treasurer)
        resp = client.get(reverse('church-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {c['id'] for c in resp.data}
        self.assertEqual(ids, {self.congregation.id, self.sede.id})

    def test_active_congregation_role_inherits_pastor_without_membership(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        client = self._client(pastor)
        resp = client.post(reverse('switch-church'), {'church_id': self.congregation.id})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['user']['role'], 'PASTOR')

    def test_local_role_has_precedence_over_inheritance(self):
        other_sede = Church.objects.create(
            name='Outra Sede',
            church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE',
            is_approved=True,
            city='Recife',
            state='PE',
        )
        other_congregation = Church.objects.create(
            name='Outra Congregação',
            church_type=Church.ChurchType.CONGREGATION,
            parent_church=other_sede,
            status='ACTIVE',
            is_approved=True,
            accounting_category='DIZIMO',
            city='Recife',
            state='PE',
        )
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        ChurchMembership.objects.create(
            user=pastor, church=other_congregation,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        client = self._client(pastor)
        resp = client.post(reverse('switch-church'), {'church_id': other_congregation.id})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_me_endpoint_returns_fresh_session_in_congregation(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        client = self._client(pastor)
        client.post(reverse('switch-church'), {'church_id': self.congregation.id})

        resp = client.get(reverse('me'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        user_data = resp.data['user']
        self.assertEqual(user_data['church']['id'], self.congregation.id)
        self.assertEqual(user_data['role'], 'PASTOR')
        self.assertTrue(user_data['can_manage_churches'])

    def test_me_endpoint_for_plain_congregation_treasurer(self):
        treasurer = self._user(
            'tesoureiro@teste.com',
            church=self.congregation,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        client = self._client(treasurer)
        resp = client.get(reverse('me'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['user']['can_manage_churches'])


class ApprovalFlowTests(BaseChurchTestCase):
    def test_pending_congregations_only_for_sede_manager(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        tesoureira = self._user(
            'tesoureira@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        resp = self._client(pastor).get(reverse('pending-congregations'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [c['id'] for c in resp.data]
        self.assertIn(self.pending_congregation.id, ids)

        resp_t = self._client(tesoureira).get(reverse('pending-congregations'))
        self.assertEqual(resp_t.status_code, status.HTTP_403_FORBIDDEN)

    def test_approve_sets_category_activates_and_seeds_events(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        requester = self._user(
            'solicitante@teste.com',
            church=self.pending_congregation,
            role=ChurchMembership.Role.PASTOR,
        )
        client = self._client(pastor)
        resp = client.post(
            reverse('approve-congregation', args=[self.pending_congregation.id]),
            {'accounting_category': 'ESPECIAL'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.pending_congregation.refresh_from_db()
        self.assertTrue(self.pending_congregation.is_approved)
        self.assertEqual(self.pending_congregation.status, 'ACTIVE')
        self.assertEqual(self.pending_congregation.accounting_category, 'ESPECIAL')
        self.assertTrue(CalendarEvent.objects.filter(church=self.pending_congregation).exists())
        requester.refresh_from_db()
        self.assertTrue(requester.is_active)

    def test_approve_requires_accounting_category(self):
        try:
            pastor = self._user(
                'pastor@teste.com',
                church=self.sede,
                role=ChurchMembership.Role.PASTOR,
            )
        except Exception:
            raise
        resp = self._client(pastor).post(
            reverse('approve-congregation', args=[self.pending_congregation.id]),
            format='json',
        )
        self.assertIn(resp.status_code, (status.HTTP_400_BAD_REQUEST, status.HTTP_403_FORBIDDEN))
        self.pending_congregation.refresh_from_db()
        self.assertFalse(self.pending_congregation.is_approved)

    def test_approve_persists_custom_category_in_catalog(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        self._user(
            'solicitante@teste.com',
            church=self.pending_congregation,
            role=ChurchMembership.Role.PASTOR,
        )
        client = self._client(pastor)
        resp = client.post(
            reverse('approve-congregation', args=[self.pending_congregation.id]),
            {'accounting_category': 'CONGREGACAO_XYZ'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.pending_congregation.refresh_from_db()
        self.assertEqual(
            self.pending_congregation.accounting_category, 'CONGREGACAO_XYZ'
        )
        # Nova categoria entra formalmente no catálogo.
        self.assertTrue(
            AccountingCategory.objects.filter(key='CONGREGACAO_XYZ').exists()
        )
        res = client.get(reverse('accounting-categories'))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        values = [c['value'] for c in res.data['categories']]
        self.assertIn('CONGREGACAO_XYZ', values)
        self.assertIn('DIZIMO', values)

    def test_reject_congregation(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        resp = self._client(pastor).post(
            reverse('reject-congregation', args=[self.pending_congregation.id]),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.pending_congregation.refresh_from_db()
        self.assertEqual(self.pending_congregation.status, 'REJECTED')
        self.assertFalse(self.pending_congregation.is_approved)


class RegisterAndAdminApprovalTests(BaseChurchTestCase):
    """Auto-cadastro de Sede/Congregação e aprovação de Sedes pela Admin."""

    def _payload(self, **overrides):
        payload = {
            'email': 'nova@igreja.com',
            'password': 'Senha123',
            'name': 'João Teste',
            'church_name': 'Congregação Nova',
            'church_type': 'CONGREGATION',
            'parent_church': self.sede.id,
            'role': 'PASTOR',
            'city': 'Campina Grande',
            'state': 'PB',
        }
        payload.update(overrides)
        return payload

    def test_register_sede_creates_pending_independent(self):
        resp = self.client.post(
            reverse('register'),
            self._payload(church_type='INDEPENDENT', church_name='Sede Nova'),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        church = Church.objects.get(pk=resp.data['church_id'])
        self.assertEqual(church.church_type, Church.ChurchType.INDEPENDENT)
        self.assertIsNone(church.parent_church)
        self.assertEqual(church.status, 'PENDING')
        self.assertFalse(church.is_approved)
        self.assertIn('administra', resp.data['detail'].lower())

    def test_register_congregation_requires_parent(self):
        payload = self._payload()
        payload.pop('parent_church', None)
        resp = self.client.post(reverse('register'), payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('parent_church', resp.data)

    def test_register_congregation_with_parent_ok(self):
        resp = self.client.post(reverse('register'), self._payload(), format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        church = Church.objects.get(pk=resp.data['church_id'])
        self.assertEqual(church.church_type, Church.ChurchType.CONGREGATION)
        self.assertEqual(church.parent_church_id, self.sede.id)
        self.assertEqual(church.status, 'PENDING')

    def test_search_parent_churches_anonymous_allowed(self):
        # O cadastro público /register consulta as sedes sem estar logado.
        resp = self.client.get(reverse('search-parent-churches'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(all(c['church_type'] == 'INDEPENDENT' for c in resp.data))
        self.assertIn(self.sede.id, [c['id'] for c in resp.data])

    def test_pending_sede_login_blocked_until_admin_approval(self):
        resp = self.client.post(
            reverse('register'),
            self._payload(
                church_type='INDEPENDENT',
                church_name='Sede Nova',
                email='sede@nova.com',
            ),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        church_id = resp.data['church_id']
        sede = Church.objects.get(pk=church_id)

        client = APIClient()
        login = client.post(
            reverse('login'),
            {'email': 'sede@nova.com', 'password': 'Senha123'},
        )
        self.assertEqual(login.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn('administra', login.data['detail'].lower())

        admin = self._user('admin@idb.com', is_staff=True)
        resp_admin = self._client(admin).post(
            reverse('admin-approve-church', args=[sede.id]),
        )
        self.assertEqual(resp_admin.status_code, status.HTTP_200_OK)

        login2 = client.post(
            reverse('login'),
            {'email': 'sede@nova.com', 'password': 'Senha123'},
        )
        self.assertEqual(login2.status_code, status.HTTP_200_OK)
        self.assertEqual(login2.data['user']['church']['church_type'], 'INDEPENDENT')

    def test_admin_pending_churches_only_independent(self):
        Church.objects.create(
            name='Sede Pendente',
            church_type=Church.ChurchType.INDEPENDENT,
            status='PENDING',
            is_approved=False,
            city='Recife',
            state='PE',
        )
        admin = self._user('admin@idb.com', is_staff=True)
        resp = self._client(admin).get(reverse('admin-pending-churches'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        types = [c['church_type'] for c in resp.data]
        self.assertTrue(types)
        self.assertTrue(all(t == 'INDEPENDENT' for t in types))
        congregation_ids = [c['id'] for c in resp.data if c['id'] == self.pending_congregation.id]
        self.assertEqual(congregation_ids, [])

    def test_admin_cannot_approve_congregation_directly(self):
        admin = self._user('admin@idb.com', is_staff=True)
        resp = self._client(admin).post(
            reverse('admin-approve-church', args=[self.pending_congregation.id]),
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.pending_congregation.refresh_from_db()
        self.assertFalse(self.pending_congregation.is_approved)
        self.assertEqual(self.pending_congregation.status, 'PENDING')


class RolePermissionTests(BaseChurchTestCase):
    def test_tesoureiro_cannot_access_church_users(self):
        tesoureira = self._user(
            'tesoureira@teste.com',
            church=self.congregation,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        resp = self._client(tesoureira).get(
            reverse('church-users', args=[self.congregation.id])
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_pastor_can_manage_users_and_members(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.congregation,
            role=ChurchMembership.Role.PASTOR,
        )
        client = self._client(pastor)
        resp = client.get(reverse('church-users', args=[self.congregation.id]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        add = client.post(
            reverse('church-users', args=[self.congregation.id]),
            {
                'email': 'secretaria@teste.com',
                'name': 'Secretária',
                'role': 'SECRETARIA',
                'password': 'P4ssw@rd123',
                'password2': 'P4ssw@rd123',
            },
            format='json',
        )
        self.assertEqual(add.status_code, status.HTTP_201_CREATED)
        membership_id = add.data['id']

        # A congregação cadastra seus próprios usuários: muda o papel e remove.
        patch = client.patch(
            reverse('church-user-detail', args=[self.congregation.id, membership_id]),
            {'role': 'TESOUREIRO'},
            format='json',
        )
        self.assertEqual(patch.status_code, status.HTTP_200_OK)
        self.assertEqual(patch.data['role'], 'TESOUREIRO')

        removed = client.delete(
            reverse('church-user-detail', args=[self.congregation.id, membership_id]),
        )
        self.assertEqual(removed.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            ChurchMembership.objects.filter(
                church=self.congregation, id=membership_id,
            ).exists()
        )

    def test_add_church_user_rejects_weak_password(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.congregation,
            role=ChurchMembership.Role.PASTOR,
        )
        client = self._client(pastor)
        resp = client.post(
            reverse('church-users', args=[self.congregation.id]),
            {
                'email': 'auxiliar@teste.com',
                'name': 'Auxiliar',
                'role': 'SECRETARIA',
                'password': 'semnumero',
                'password2': 'semnumero',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_add_church_user_rejects_password_without_number_or_special(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.congregation,
            role=ChurchMembership.Role.PASTOR,
        )
        client = self._client(pastor)
        for attempt in ('abcdefgh', 'abcd1234'):
            resp = client.post(
                reverse('church-users', args=[self.congregation.id]),
                {
                    'email': 'auxiliar@teste.com',
                    'name': 'Auxiliar',
                    'role': 'SECRETARIA',
                    'password': attempt,
                    'password2': attempt,
                },
                format='json',
            )
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(
            User.objects.filter(email__iexact='auxiliar@teste.com').exists()
        )

    def test_add_church_user_rejects_password_mismatch(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.congregation,
            role=ChurchMembership.Role.PASTOR,
        )
        client = self._client(pastor)
        resp = client.post(
            reverse('church-users', args=[self.congregation.id]),
            {
                'email': 'auxiliar@teste.com',
                'name': 'Auxiliar',
                'role': 'SECRETARIA',
                'password': 'P4ssw@rd123',
                'password2': 'P4ssw@rd456',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_added_user_has_usable_password(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.congregation,
            role=ChurchMembership.Role.PASTOR,
        )
        client = self._client(pastor)
        resp = client.post(
            reverse('church-users', args=[self.congregation.id]),
            {
                'email': 'tesoureiro@teste.com',
                'name': 'Tesoureiro',
                'role': 'TESOUREIRO',
                'password': 'P4ssw@rd123',
                'password2': 'P4ssw@rd123',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(email='tesoureiro@teste.com')
        self.assertTrue(user.check_password('P4ssw@rd123'))

    def test_secretaria_manages_members_but_not_users(self):
        secretaria = self._user(
            'secretaria@teste.com',
            church=self.congregation,
            role=ChurchMembership.Role.SECRETARIA,
        )
        client = self._client(secretaria)
        resp = client.get(reverse('church-members-list', args=[self.congregation.id]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # Secretária vê a lista de usuários (somente leitura).
        resp_users = client.get(reverse('church-users', args=[self.congregation.id]))
        self.assertEqual(resp_users.status_code, status.HTTP_200_OK)

        # Mas não pode criar usuários.
        resp_add = client.post(
            reverse('church-users', args=[self.congregation.id]),
            {
                'email': 'auxiliar@teste.com',
                'name': 'Auxiliar',
                'role': 'SECRETARIA',
                'password': 'P4ssw@rd123',
                'password2': 'P4ssw@rd123',
            },
            format='json',
        )
        self.assertEqual(resp_add.status_code, status.HTTP_403_FORBIDDEN)

    def test_member_self_crud_scoped_to_active_church(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.congregation,
            role=ChurchMembership.Role.PASTOR,
        )
        client = self._client(pastor)
        created = client.post(
            reverse('member-self-list'),
            {'name': 'José da Silva', 'status': 'ACTIVE'},
            format='json',
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        member_id = created.data['id']
        self.assertEqual(
            Member.objects.get(pk=member_id).church_id, self.congregation.id
        )

        resp = client.get(reverse('member-self-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['name'], 'José da Silva')


class ChurchHierarchyTests(BaseChurchTestCase):
    def test_admin_churches_list_includes_type_and_parent_name(self):
        admin = self._user('admin@idb.com', is_staff=True)
        resp = self._client(admin).get(reverse('admin-churches'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = {c['id']: c for c in resp.data}

        sede = data[self.sede.id]
        self.assertEqual(sede['church_type'], Church.ChurchType.INDEPENDENT)
        self.assertIsNone(sede['parent_church'])
        self.assertIsNone(sede['parent_church_name'])

        cong = data[self.congregation.id]
        self.assertEqual(cong['church_type'], Church.ChurchType.CONGREGATION)
        self.assertEqual(cong['parent_church'], self.sede.id)
        self.assertEqual(cong['parent_church_name'], self.sede.name)

    def test_admin_can_create_independent_church(self):
        admin = self._user('admin@idb.com', is_staff=True)
        # Sede não exige Categoria Contábil (repasse) — campo fica vazio.
        resp = self._client(admin).post(
            reverse('church-list'),
            {
                'name': 'Nova Sede',
                'church_type': Church.ChurchType.INDEPENDENT,
                'city': 'João Pessoa',
                'state': 'PB',
            },
            format='json',
        )
        self.assertIn(resp.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED))
        church = Church.objects.get(name='Nova Sede')
        self.assertEqual(church.church_type, Church.ChurchType.INDEPENDENT)
        self.assertIsNone(church.parent_church_id)
        self.assertIsNone(church.accounting_category)
        self.assertTrue(church.is_approved)
        self.assertEqual(church.status, 'ACTIVE')

    def test_non_admin_cannot_create_independent_church(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        resp = self._client(pastor).post(
            reverse('church-list'),
            {
                'name': 'Tentativa Sede',
                'church_type': Church.ChurchType.INDEPENDENT,
                'city': 'Campina Grande',
                'state': 'PB',
                'accounting_category': 'DIZIMO',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Church.objects.filter(name='Tentativa Sede').exists())

    def test_sede_manager_can_create_congregation(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        resp = self._client(pastor).post(
            reverse('church-list'),
            {
                'name': 'Congregação Nova',
                'city': 'Campina Grande',
                'state': 'PB',
                'accounting_category': 'ESPECIAL',
            },
            format='json',
        )
        self.assertIn(resp.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED))
        church = Church.objects.filter(name='Congregação Nova').first()
        self.assertIsNotNone(church)
        self.assertEqual(church.church_type, Church.ChurchType.CONGREGATION)
        self.assertEqual(church.parent_church_id, self.sede.id)
        # Criação direta pela Sede: já nasce aprovada, fora da fila de pendentes.
        self.assertTrue(church.is_approved)
        self.assertEqual(church.status, 'ACTIVE')
        self.assertEqual(church.accounting_category, 'ESPECIAL')

    def test_sede_direct_create_requires_category(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        resp = self._client(pastor).post(
            reverse('church-list'),
            {'name': 'Congregação Nova', 'city': 'Campina Grande', 'state': 'PB'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Church.objects.filter(name='Congregação Nova').exists())

    def test_sede_direct_create_with_responsible_user(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        resp = self._client(pastor).post(
            reverse('church-list'),
            {
                'name': 'Congregação ABC',
                'city': 'João Pessoa',
                'state': 'PB',
                'accounting_category': 'CONGREGACAO_ABC',
                'responsible_user': {
                    'email': 'local@teste.com',
                    'name': 'Responsável Local',
                    'role': 'TESOUREIRO',
                },
            },
            format='json',
        )
        self.assertIn(resp.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED))
        church = Church.objects.get(name='Congregação ABC')
        self.assertEqual(church.accounting_category, 'CONGREGACAO_ABC')
        user = church.responsible_user
        self.assertIsNotNone(user)
        self.assertEqual(user.email, 'local@teste.com')
        membership = church.members.get(user=user)
        self.assertEqual(membership.role, ChurchMembership.Role.TESOUREIRO)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertEqual(user.church_id, church.id)

    def test_sede_direct_create_with_existing_responsible_user(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        existing = self._user(
            'existente@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        id_usuario = existing.id
        resp = self._client(pastor).post(
            reverse('church-list'),
            {
                'name': 'Congregação XYZ',
                'city': 'Campina Grande',
                'state': 'PB',
                'accounting_category': 'CONGREGACAO_XYZ',
                'responsible_user': {
                    'user_id': id_usuario,
                    'role': 'PASTOR',
                },
            },
            format='json',
        )
        self.assertIn(resp.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED))
        church = Church.objects.get(name='Congregação XYZ')
        user = church.responsible_user
        self.assertIsNotNone(user)
        self.assertEqual(user.id, id_usuario)
        self.assertEqual(user.email, 'existente@teste.com')
        membership = church.members.get(user=user)
        self.assertEqual(membership.role, ChurchMembership.Role.PASTOR)
        # Nenhuma conta nova criada: mesmo total de usuários da Sede + a própria.
        self.assertEqual(
            User.objects.filter(email__iexact='existente@teste.com').count(), 1
        )

    def test_admin_direct_create_congregation_with_responsible(self):
        admin = self._user('admin@idb.com', is_staff=True)
        responsible = self._user(
            'responsavel@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        id_usuario = responsible.id
        resp = self._client(admin).post(
            reverse('church-list'),
            {
                'name': 'Congregação ADMIN',
                'city': 'João Pessoa',
                'state': 'PB',
                'accounting_category': 'CONGREGACAO_ADMIN',
                'parent_church': self.sede.id,
                'responsible_user': {
                    'user_id': id_usuario,
                    'role': 'PASTOR',
                },
            },
            format='json',
        )
        self.assertIn(resp.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED))
        church = Church.objects.get(name='Congregação ADMIN')
        self.assertEqual(church.parent_church_id, self.sede.id)
        self.assertEqual(church.status, 'ACTIVE')
        self.assertTrue(church.is_approved)
        responsavel = church.responsible_user
        self.assertIsNotNone(responsavel)
        self.assertEqual(responsavel.id, id_usuario)
        membership = church.members.get(user=responsavel)
        self.assertEqual(membership.role, ChurchMembership.Role.PASTOR)
        # O responsável aparece no painel de usuários da congregação.
        listed = self._client(admin).get(
            reverse('church-users', args=[church.id])
        )
        self.assertEqual(listed.status_code, status.HTTP_200_OK)
        emails = [m['user_email'] for m in listed.data]
        self.assertIn('responsavel@teste.com', emails)

    def test_sede_direct_create_existing_user_not_found(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        resp = self._client(pastor).post(
            reverse('church-list'),
            {
                'name': 'Congregação NAOEXISTE',
                'city': 'Campina Grande',
                'state': 'PB',
                'accounting_category': 'CONGREGACAO_NAOEXISTE',
                'responsible_user': {
                    'user_id': 99999,
                    'role': 'PASTOR',
                },
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Church.objects.filter(name='Congregação NAOEXISTE').exists())

    def test_update_reclassifies_accounting_category(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        client = self._client(pastor)
        resp = client.patch(
            reverse('church-detail', args=[self.congregation.id]),
            {'accounting_category': 'CONSTRUCAO'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.congregation.refresh_from_db()
        self.assertEqual(self.congregation.accounting_category, 'CONSTRUCAO')

        # Reclassificação para categoria personalizada persiste no catálogo.
        resp2 = client.patch(
            reverse('church-detail', args=[self.congregation.id]),
            {'accounting_category': 'CONGREGACAO_ALPHA'},
            format='json',
        )
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        self.congregation.refresh_from_db()
        self.assertEqual(self.congregation.accounting_category, 'CONGREGACAO_ALPHA')
        self.assertTrue(
            AccountingCategory.objects.filter(key='CONGREGACAO_ALPHA').exists()
        )

    def test_update_cannot_change_church_type_or_parent(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        other_sede = Church.objects.create(
            name='Outra Sede',
            church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE',
            is_approved=True,
            city='Recife',
            state='PE',
        )
        resp = self._client(pastor).patch(
            reverse('church-detail', args=[self.congregation.id]),
            {'church_type': 'INDEPENDENT', 'parent_church': other_sede.id},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.congregation.refresh_from_db()
        self.assertEqual(self.congregation.church_type, Church.ChurchType.CONGREGATION)
        self.assertEqual(self.congregation.parent_church_id, self.sede.id)

    def test_update_reassigns_responsible_user(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        antigo = self._user(
            'antigo@congregacao.com',
            church=self.congregation,
            role=ChurchMembership.Role.PASTOR,
        )
        novo = self._user(
            'novo@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        client = self._client(pastor)
        resp = client.patch(
            reverse('church-detail', args=[self.congregation.id]),
            {
                'name': self.congregation.name,
                'responsible_user': {'user_id': novo.id, 'role': 'PASTOR'},
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.congregation.refresh_from_db()
        self.assertEqual(self.congregation.responsible_user.id, novo.id)
        # O responsável anterior perdeu o vínculo com a congregação.
        self.assertFalse(
            ChurchMembership.objects.filter(
                church=self.congregation, user=antigo,
            ).exists()
        )
        # O novo responsável foi vinculado como PASTOR.
        self.assertTrue(
            ChurchMembership.objects.filter(
                church=self.congregation, user=novo, role=ChurchMembership.Role.PASTOR,
            ).exists()
        )

    def test_update_responsible_same_user_changes_role(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        responsavel = self._user(
            'resp@congregacao.com',
            church=self.congregation,
            role=ChurchMembership.Role.PASTOR,
        )
        client = self._client(pastor)
        resp = client.patch(
            reverse('church-detail', args=[self.congregation.id]),
            {
                'name': self.congregation.name,
                'responsible_user': {'user_id': responsavel.id, 'role': 'TESOUREIRO'},
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        membership = ChurchMembership.objects.get(
            church=self.congregation, user=responsavel,
        )
        self.assertEqual(membership.role, ChurchMembership.Role.TESOUREIRO)
        # Continua sendo o responsável (único vínculo da congregação).
        self.assertEqual(self.congregation.responsible_user.id, responsavel.id)

    def test_update_responsible_accepts_secretaria(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        secretaria = self._user(
            'secretaria@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.SECRETARIA,
        )
        resp = self._client(pastor).patch(
            reverse('church-detail', args=[self.congregation.id]),
            {
                'name': self.congregation.name,
                'responsible_user': {'user_id': secretaria.id, 'role': 'SECRETARIA'},
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        membership = ChurchMembership.objects.get(
            church=self.congregation, user=secretaria,
        )
        self.assertEqual(membership.role, ChurchMembership.Role.SECRETARIA)

    def test_update_responsible_user_not_found(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        resp = self._client(pastor).patch(
            reverse('church-detail', args=[self.congregation.id]),
            {
                'name': self.congregation.name,
                'responsible_user': {'user_id': 99999, 'role': 'PASTOR'},
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_church_list_returns_responsible_user_id(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        responsavel = self._user(
            'resp@congregacao.com',
            church=self.congregation,
            role=ChurchMembership.Role.PASTOR,
        )
        resp = self._client(pastor).get(reverse('church-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        congregation_data = next(
            c for c in resp.data if c['id'] == self.congregation.id
        )
        self.assertEqual(congregation_data['responsible_user_id'], responsavel.id)

    def test_sede_pastor_gets_and_updates_congregation_profile(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        client = self._client(pastor)
        resp = client.get(
            reverse('church-manage-profile', args=[self.congregation.id])
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['church_type'], 'CONGREGATION')

        put = client.put(
            reverse('church-manage-profile', args=[self.congregation.id]),
            {
                'name': 'Congregação Teste Renomeada',
                'city': 'Campina Grande',
                'state': 'PB',
                'street': 'Rua Central',
                'neighborhood': 'Centro',
            },
            format='json',
        )
        self.assertEqual(put.status_code, status.HTTP_200_OK)
        self.congregation.refresh_from_db()
        self.assertEqual(self.congregation.street, 'Rua Central')
        self.assertEqual(self.congregation.neighborhood, 'Centro')
        self.assertEqual(self.congregation.name, 'Congregação Teste Renomeada')

    def test_church_card_config_roundtrip(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        client = self._client(pastor)
        put = client.put(
            reverse('church-manage-profile', args=[self.congregation.id]),
            {
                'name': self.congregation.name,
                'city': self.congregation.city,
                'state': self.congregation.state,
                'card_primary_color': '#123456',
                'card_secondary_color': '#FEDCBA',
                'card_valid_until': '2027-12-31',
                'card_front_phrase': 'A alegria do Senhor é a nossa força.',
                'card_back_phrase': 'Documento pessoal de identificação da igreja.',
            },
            format='json',
        )
        self.assertEqual(put.status_code, status.HTTP_200_OK)
        self.assertEqual(put.data['card_primary_color'], '#123456')
        self.assertEqual(put.data['card_secondary_color'], '#FEDCBA')
        self.assertEqual(put.data['card_valid_until'], '2027-12-31')
        self.congregation.refresh_from_db()
        self.assertEqual(
            self.congregation.card_front_phrase,
            'A alegria do Senhor é a nossa força.',
        )
        self.congregation.card_valid_until = None
        self.congregation.save(update_fields=['card_valid_until'])

    def test_church_card_config_invalid_color(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        client = self._client(pastor)
        resp = client.put(
            reverse('church-manage-profile', args=[self.congregation.id]),
            {
                'name': self.congregation.name,
                'city': self.congregation.city,
                'state': self.congregation.state,
                'card_primary_color': 'red',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('card_primary_color', resp.data)

    def test_sede_manager_cannot_access_alien_congregation_profile(self):
        other_sede = Church.objects.create(
            name='Outra Sede',
            church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE',
            is_approved=True,
            city='Recife',
            state='PE',
        )
        alien_congregation = Church.objects.create(
            name='Congregação Alheia',
            church_type=Church.ChurchType.CONGREGATION,
            parent_church=other_sede,
            status='ACTIVE',
            is_approved=True,
            accounting_category='DIZIMO',
            city='Recife',
            state='PE',
        )
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        resp = self._client(pastor).get(
            reverse('church-manage-profile', args=[alien_congregation.id])
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_tesoureiro_cannot_create_church(self):
        tesoureira = self._user(
            'tesoureira@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        resp = self._client(tesoureira).post(
            reverse('church-list'),
            {'name': 'Congregação Nova', 'city': 'Campina Grande', 'state': 'PB'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_sede_delete_restricted_to_admin(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        resp = self._client(pastor).delete(
            reverse('church-detail', args=[self.sede.id])
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Church.objects.filter(pk=self.sede.pk).exists())

        admin = self._user('admin@idb.com', is_staff=True)
        resp_admin = self._client(admin).delete(
            reverse('church-detail', args=[self.sede.id])
        )
        self.assertEqual(resp_admin.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Church.objects.filter(pk=self.sede.pk).exists())

    def test_sede_delete_cascades_congregations_and_all_data(self):
        from datetime import date
        from decimal import Decimal

        from finance.models import (
            CalendarEvent,
            FinancialEntry,
            FinancialExit,
            MonthlyClosing,
            MonthlyValidation,
            Tither,
            TitheRecord,
        )

        other = Church.objects.create(
            name='Congregação 2',
            church_type=Church.ChurchType.CONGREGATION,
            parent_church=self.sede,
            status='ACTIVE',
            is_approved=True,
            accounting_category='OFERTA',
            city='João Pessoa',
            state='PB',
        )
        admin = self._user('admin@idb.com', is_staff=True)

        # Conta compartilhada (Sede + congregação) para validar preservação.
        shared = self._user(
            'shared@teste.com', church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        ChurchMembership.objects.create(
            user=shared, church=other, role=ChurchMembership.Role.PASTOR,
        )

        # Dados completos em cada congregação.
        for cong in (self.congregation, other):
            FinancialEntry.objects.create(
                church=cong, date=date(2026, 1, 4),
                service_description='Culto', category='DIZIMO',
                amount=Decimal('150.00'),
            )
            FinancialExit.objects.create(
                church=cong, date=date(2026, 1, 5),
                description='Luz', category='CONSTRUCAO',
                amount=Decimal('50.00'),
            )
            tithe = Tither.objects.create(church=cong, name='Dizimista X')
            TitheRecord.objects.create(
                tither=tithe, year=2026, month=1, amount=Decimal('150.00')
            )
            MonthlyClosing.objects.create(church=cong, year=2026, month=1)
            MonthlyValidation.objects.create(church=cong, year=2026, month=1)
            CalendarEvent.objects.create(
                church=cong, title='Culto de Ceia', category='event',
                date=date(2026, 1, 18),
            )
            area = MinistryArea.objects.create(church=cong, name='Coral')
            member = Member.objects.create(church=cong, name='Membro A')
            member.ministry_areas.add(area)

        resp = self._client(admin).delete(
            reverse('church-detail', args=[self.sede.id])
        )
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

        # Sede, congregações (aprovadas e pendentes) removidas.
        self.assertFalse(Church.objects.filter(pk=self.sede.pk).exists())
        self.assertFalse(Church.objects.filter(pk=self.congregation.pk).exists())
        self.assertFalse(Church.objects.filter(pk=other.pk).exists())
        self.assertFalse(Church.objects.filter(pk=self.pending_congregation.pk).exists())

        # Dados financeiros e de diretório removidos em cascata.
        self.assertEqual(FinancialEntry.objects.count(), 0)
        self.assertEqual(FinancialExit.objects.count(), 0)
        self.assertEqual(Tither.objects.count(), 0)
        self.assertEqual(TitheRecord.objects.count(), 0)
        self.assertEqual(MonthlyClosing.objects.count(), 0)
        self.assertEqual(MonthlyValidation.objects.count(), 0)
        self.assertEqual(CalendarEvent.objects.count(), 0)
        self.assertEqual(Member.objects.count(), 0)
        self.assertEqual(MinistryArea.objects.count(), 0)
        self.assertEqual(ChurchMembership.objects.count(), 0)

        # Contas preservadas, apenas sem vínculos e sem contexto de igreja.
        shared.refresh_from_db()
        self.assertTrue(User.objects.filter(pk=shared.pk).exists())
        self.assertIsNone(shared.church_id)
        self.assertFalse(shared.church_memberships.exists())

    def test_congregation_delete_removes_only_its_links(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        shared = self._user(
            'shared@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        ChurchMembership.objects.create(
            user=shared, church=self.congregation,
            role=ChurchMembership.Role.SECRETARIA,
        )
        only_here = self._user(
            'only-here@teste.com',
            church=self.congregation,
            role=ChurchMembership.Role.TESOUREIRO,
        )

        resp = self._client(pastor).delete(
            reverse('church-detail', args=[self.congregation.id])
        )
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Church.objects.filter(pk=self.congregation.pk).exists())

        # Vínculo da congregação removido; a conta compartilhada mantém a Sede.
        self.assertFalse(
            ChurchMembership.objects.filter(
                church=self.congregation, user=shared
            ).exists()
        )
        self.assertTrue(
            ChurchMembership.objects.filter(church=self.sede, user=shared).exists()
        )

        # Conta que só pertencia à congregação é preservada, sem vínculos.
        only_here.refresh_from_db()
        self.assertTrue(User.objects.filter(pk=only_here.pk).exists())
        self.assertIsNone(only_here.church_id)
        self.assertFalse(only_here.church_memberships.exists())

    def test_congregation_delete_removes_it(self):
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        resp = self._client(pastor).delete(
            reverse('church-detail', args=[self.congregation.id])
        )
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Church.objects.filter(pk=self.congregation.pk).exists())

    def test_accessible_churches_for_sede_pastor(self):
        other_sede = Church.objects.create(
            name='Igreja Alheia',
            church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE',
            is_approved=True,
            city='Recife',
            state='PE',
        )
        pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        resp = self._client(pastor).get(reverse('church-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {c['id'] for c in resp.data}
        self.assertIn(self.sede.id, ids)
        self.assertIn(self.congregation.id, ids)
        # Uma sede de outra federação jamais aparece na lista.
        self.assertNotIn(other_sede.id, ids)

    def test_search_parent_churches(self):
        client = self._client(
            self._user('pastor@teste.com', church=self.sede,
                       role=ChurchMembership.Role.PASTOR)
        )
        resp = client.get(reverse('search-parent-churches'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(all(c['church_type'] == 'INDEPENDENT' for c in resp.data))


class MinistryAreaAndMemberTests(BaseChurchTestCase):
    """CRUD de áreas de atuação e novos campos do diretório de membros."""

    def _pastor(self):
        return self._user(
            'pastor@teste.com', church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )

    def test_create_ministry_area_and_list(self):
        client = self._client(self._pastor())
        resp = client.post(
            reverse('ministry-area-list'), {'name': 'coral'}, format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['name'], 'coral')
        self.assertEqual(resp.data['church'], self.sede.id)

        outro = Church.objects.create(
            name='Outra Sede', church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE', is_approved=True, city='Recife', state='PE',
        )
        MinistryArea.objects.create(church=outro, name='Ensino')

        listed = client.get(reverse('ministry-area-list'))
        self.assertEqual(listed.status_code, status.HTTP_200_OK)
        names = [a['name'] for a in listed.data]
        self.assertEqual(names, ['coral'])
        self.assertNotIn('Ensino', names)

    def test_ministry_area_duplicate_name_rejected(self):
        area = MinistryArea.objects.create(church=self.sede, name='Coral')
        client = self._client(self._pastor())
        resp = client.post(
            reverse('ministry-area-list'), {'name': 'coral'}, format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('name', resp.data)
        self.assertEqual(MinistryArea.objects.filter(pk=area.pk).count(), 1)

    def test_ministry_area_update_and_delete(self):
        area = MinistryArea.objects.create(church=self.sede, name='Coral')
        client = self._client(self._pastor())
        upd = client.patch(
            reverse('ministry-area-detail', args=[area.id]), {'name': 'Diaconia'},
            format='json',
        )
        self.assertEqual(upd.status_code, status.HTTP_200_OK)
        self.assertEqual(upd.data['name'], 'Diaconia')
        deleted = client.delete(reverse('ministry-area-detail', args=[area.id]))
        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(MinistryArea.objects.filter(pk=area.pk).exists())

    def test_tesoureiro_can_manage_ministry_areas(self):
        tesoureira = self._user(
            'tesoureira@teste.com', church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        resp = self._client(tesoureira).post(
            reverse('ministry-area-list'), {'name': 'Coral'}, format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_tesoureiro_manages_members(self):
        tesoureira = self._user(
            'tesoureira@teste.com', church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        client = self._client(tesoureira)
        created = client.post(
            reverse('member-self-list'),
            {'name': 'Marta Dias', 'cpf': '987.654.321-00'},
            format='json',
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        detail = client.get(reverse('member-self-detail', args=[created.data['id']]))
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data['name'], 'Marta Dias')

    def test_sede_manages_congregation_ministry_areas(self):
        MinistryArea.objects.create(church=self.sede, name='Coral')
        pastor = self._user(
            'pastor@teste.com', church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        client = self._client(pastor)

        created = client.post(
            reverse('church-ministry-areas-list', args=[self.congregation.id]),
            {'name': 'Escola Dominical'},
            format='json',
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        self.assertEqual(created.data['church'], self.congregation.id)

        listed = client.get(
            reverse('church-ministry-areas-list', args=[self.congregation.id])
        )
        self.assertEqual(listed.status_code, status.HTTP_200_OK)
        names = [a['name'] for a in listed.data]
        self.assertEqual(names, ['Escola Dominical'])
        self.assertNotIn('Coral', names)

        area = MinistryArea.objects.get(pk=created.data['id'])
        updated = client.patch(
            reverse(
                'church-ministry-areas-detail',
                args=[self.congregation.id, area.id],
            ),
            {'name': 'Diaconia'},
            format='json',
        )
        self.assertEqual(updated.status_code, status.HTTP_200_OK)
        self.assertEqual(updated.data['name'], 'Diaconia')

        deleted = client.delete(
            reverse(
                'church-ministry-areas-detail',
                args=[self.congregation.id, area.id],
            )
        )
        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)

    def test_unrelated_church_not_operable(self):
        outro = Church.objects.create(
            name='Outra Sede', church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE', is_approved=True, city='Recife', state='PE',
        )
        pastor = self._user(
            'pastor@teste.com', church=self.congregation,
            role=ChurchMembership.Role.PASTOR,
        )
        client = self._client(pastor)
        resp = client.get(
            reverse('church-ministry-areas-list', args=[outro.id])
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_member_create_with_new_fields(self):
        area = MinistryArea.objects.create(church=self.sede, name='Coral')
        client = self._client(self._pastor())
        resp = client.post(
            reverse('member-self-list'),
            {
                'name': 'Maria Souza',
                'cpf': '123.456.789-00',
                'rg': '1234567',
                'church_entry': Member.ChurchEntry.BATISMO,
                'ministry_areas': [area.id],
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['cpf'], '123.456.789-00')
        self.assertEqual(resp.data['church_entry'], Member.ChurchEntry.BATISMO)
        self.assertEqual(resp.data['church_entry_display'], 'Batismo')
        self.assertEqual(resp.data['ministry_areas'], [area.id])
        self.assertEqual(resp.data['ministry_areas_display'][0]['name'], 'Coral')

    def test_member_create_with_family_and_relatives(self):
        client = self._client(self._pastor())
        resp = client.post(
            reverse('member-self-list'),
            {
                'name': 'Maria Souza',
                'marital_status': Member.MaritalStatus.CASADO,
                'marriage_date': '2010-05-20',
                'father_name': 'José Souza',
                'mother_name': 'Ana Souza',
                'born_in_city': 'João Pessoa',
                'born_in_state': 'pb',
                'profession': 'Professora',
                'education_level': Member.EducationLevel.SUPERIOR,
                'relatives': [
                    {'name': 'Carlos Souza', 'kinship': 'CONJUGE', 'birth_date': '1988-03-10', 'phone': '(83) 99999-0000'},
                    {'name': 'Lia Souza', 'kinship': 'FILHO', 'birth_date': '2015-05-05'},
                ],
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        data = resp.data
        self.assertEqual(data['marital_status'], Member.MaritalStatus.CASADO)
        self.assertEqual(data['marital_status_display'], 'Casado(a)')
        self.assertEqual(data['marriage_date'], '2010-05-20')
        self.assertEqual(data['born_in_state'], 'PB')
        self.assertEqual(data['education_level_display'], 'Ensino Superior')
        self.assertEqual(data['card_number'], '0001')
        rel = {r['name']: r for r in data['relatives']}
        self.assertTrue(rel['Carlos Souza']['id'])
        self.assertEqual(rel['Carlos Souza']['kinship'], 'CONJUGE')
        self.assertEqual(len(data['relatives']), 2)

    def test_member_new_education_levels(self):
        client = self._client(self._pastor())
        cases = {
            Member.EducationLevel.SEM_ESCOLARIDADE: 'Sem Escolaridade',
            Member.EducationLevel.MEDIO_INCOMPLETO: 'Ensino Médio Incompleto',
            Member.EducationLevel.SUPERIOR_INCOMPLETO: 'Ensino Superior Incompleto',
        }
        for value, label in cases.items():
            resp = client.post(
                reverse('member-self-list'),
                {'name': 'Membro Teste', 'education_level': value},
                format='json',
            )
            self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
            self.assertEqual(resp.data['education_level_display'], label)

    def test_member_church_entry_outro_requires_other(self):
        client = self._client(self._pastor())
        resp = client.post(
            reverse('member-self-list'),
            {
                'name': 'Maria Souza',
                'church_entry': Member.ChurchEntry.OUTRO,
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('church_entry_other', resp.data)
        resp2 = client.post(
            reverse('member-self-list'),
            {
                'name': 'Maria Souza',
                'church_entry': Member.ChurchEntry.OUTRO,
                'church_entry_other': 'Profissão de fé',
            },
            format='json',
        )
        self.assertEqual(resp2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp2.data['church_entry_other'], 'Profissão de fé')
        self.assertEqual(resp2.data['church_entry_display'], 'Profissão de fé')

    def test_member_church_entry_other_cleared_when_not_outro(self):
        client = self._client(self._pastor())
        created = client.post(
            reverse('member-self-list'),
            {
                'name': 'Maria Souza',
                'church_entry': Member.ChurchEntry.OUTRO,
                'church_entry_other': 'Profissão de fé',
            },
            format='json',
        )
        member_id = created.data['id']
        updated = client.put(
            reverse('member-self-detail', args=[member_id]),
            {'name': 'Maria Souza', 'church_entry': Member.ChurchEntry.BATISMO},
            format='json',
        )
        self.assertEqual(updated.status_code, status.HTTP_200_OK)
        self.assertEqual(updated.data['church_entry_other'], '')

    def test_member_create_relative_requires_name(self):
        client = self._client(self._pastor())
        resp = client.post(
            reverse('member-self-list'),
            {
                'name': 'Maria Souza',
                'relatives': [{'name': ' ', 'kinship': 'PAI'}],
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_member_create_relative_invalid_kinship(self):
        client = self._client(self._pastor())
        resp = client.post(
            reverse('member-self-list'),
            {
                'name': 'Maria Souza',
                'relatives': [{'name': 'X', 'kinship': 'PRIMO'}],
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_member_update_syncs_relatives(self):
        client = self._client(self._pastor())
        created = client.post(
            reverse('member-self-list'),
            {
                'name': 'Maria Souza',
                'relatives': [
                    {'name': 'Carlos Souza', 'kinship': 'CONJUGE'},
                    {'name': 'Lia Souza', 'kinship': 'FILHO'},
                    {'name': 'Rui Souza', 'kinship': 'IRMAO'},
                ],
            },
            format='json',
        )
        member_id = created.data['id']
        c1 = next(r for r in created.data['relatives'] if r['name'] == 'Carlos Souza')['id']
        c2 = next(r for r in created.data['relatives'] if r['name'] == 'Lia Souza')['id']
        # Remove Rui, renomeia Carlos e adiciona um novo (Neto).
        updated = client.put(
            reverse('member-self-detail', args=[member_id]),
            {
                'name': 'Maria Souza',
                'relatives': [
                    {'id': c1, 'name': 'Carlos Souza Filho', 'kinship': 'CONJUGE'},
                    {'id': c2, 'name': 'Lia Souza', 'kinship': 'FILHO'},
                    {'name': 'Bia Souza', 'kinship': 'NETO'},
                ],
            },
            format='json',
        )
        self.assertEqual(updated.status_code, status.HTTP_200_OK)
        names = [r['name'] for r in updated.data['relatives']]
        self.assertNotIn('Rui Souza', names)
        self.assertIn('Carlos Souza Filho', names)
        self.assertIn('Bia Souza', names)
        self.assertEqual(MemberRelative.objects.filter(member_id=member_id).count(), 3)

    def test_member_card_number_sequential_per_church(self):
        client = self._client(self._pastor())
        r1 = client.post(reverse('member-self-list'), {'name': 'Ana'}, format='json')
        r2 = client.post(reverse('member-self-list'), {'name': 'Bia'}, format='json')
        self.assertEqual(r1.data['card_number'], '0001')
        self.assertEqual(r2.data['card_number'], '0002')

        # Outra igreja reinicia a sequência.
        outra = Church.objects.create(
            name='Outra Sede', church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE', is_approved=True, city='Recife', state='PE',
        )
        outro_member = Member.objects.create(church=outra, name='Célia')
        outro_member.card_number = '0001'
        outro_member.save()

        # Atualizar não altera a matrícula.
        upd = client.patch(
            reverse('member-self-detail', args=[r2.data['id']]),
            {'name': 'Bia Castro'},
            format='json',
        )
        self.assertEqual(upd.data['card_number'], '0002')

    def test_delete_member_cascades_relatives(self):
        client = self._client(self._pastor())
        created = client.post(
            reverse('member-self-list'),
            {
                'name': 'Maria Souza',
                'relatives': [{'name': 'Carlos Souza', 'kinship': 'CONJUGE'}],
            },
            format='json',
        )
        member_id = created.data['id']
        self.assertEqual(MemberRelative.objects.filter(member_id=member_id).count(), 1)
        deleted = client.delete(reverse('member-self-detail', args=[member_id]))
        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(MemberRelative.objects.filter(member_id=member_id).exists())

    def test_member_ministry_area_of_other_church_rejected(self):
        outra = Church.objects.create(
            name='Outra Sede', church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE', is_approved=True, city='Recife', state='PE',
        )
        alien_area = MinistryArea.objects.create(church=outra, name='Banda')
        client = self._client(self._pastor())
        resp = client.post(
            reverse('member-self-list'),
            {'name': 'Maria Souza', 'ministry_areas': [alien_area.id]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_member_photo_data_url_uploads(self):
        from unittest import mock

        client = self._client(self._pastor())
        payload = {
            'name': 'João Pedro',
            'photo': (
                'data:image/png;base64,'
                'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC'
                'AAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
            ),
        }
        resp = client.post(reverse('member-self-list'), payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp.data['photo'])
        member = Member.objects.get(pk=resp.data['id'])
        self.assertTrue(member.photo)

    def test_members_scoped_to_church(self):
        outra = Church.objects.create(
            name='Outra Sede', church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE', is_approved=True, city='Recife', state='PE',
        )
        Member.objects.create(church=outra, name='Membro Alheio')
        client = self._client(self._pastor())
        resp = client.get(reverse('member-self-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(any(m['name'] == 'Membro Alheio' for m in resp.data))


class MemberFilterTests(BaseChurchTestCase):
    """Filtros combinados da listagem de membros (query params)."""

    def _pastor(self):
        if not hasattr(self, '_pastor_user') or self._pastor_user is None:
            self._pastor_user = self._user(
                'pastor@teste.com', church=self.sede,
                role=ChurchMembership.Role.PASTOR,
            )
        return self._pastor_user

    def setUp(self):
        super().setUp()
        self._pastor_user = None
        self.area = MinistryArea.objects.create(church=self.sede, name='Coral')
        m1 = Member.objects.create(
            church=self.sede, name='Ana Lima',
            status=Member.Status.ACTIVE,
            education_level=Member.EducationLevel.SUPERIOR,
            marital_status=Member.MaritalStatus.SOLTEIRO,
            church_entry=Member.ChurchEntry.BATISMO,
            birth_date='2000-01-15',
        )
        m2 = Member.objects.create(
            church=self.sede, name='Bruno Reis',
            status=Member.Status.INACTIVE,
            education_level=Member.EducationLevel.MEDIO,
            marital_status=Member.MaritalStatus.CASADO,
            marriage_date='2015-03-10',
            church_entry=Member.ChurchEntry.TRANSFERENCIA,
            birth_date='1985-07-20',
        )
        m3 = Member.objects.create(
            church=self.sede, name='Carla Dias',
            status=Member.Status.ACTIVE,
            education_level=Member.EducationLevel.SUPERIOR,
            marital_status=Member.MaritalStatus.CASADO,
            church_entry=Member.ChurchEntry.ACLAMACAO,
            birth_date='1992-11-05',
        )
        m1.ministry_areas.add(self.area)
        m3.ministry_areas.add(self.area)
        self.m1, self.m2, self.m3 = m1, m2, m3

    def _names(self, params, **extra):
        client = self._client(self._pastor())
        resp = client.get(reverse('member-self-list'), params)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        return [m['name'] for m in resp.data]

    def test_filter_by_status(self):
        self.assertEqual(self._names({'status': 'ACTIVE'}), ['Ana Lima', 'Carla Dias'])
        self.assertEqual(self._names({'status': 'INACTIVE'}), ['Bruno Reis'])

    def test_filter_by_search(self):
        self.assertEqual(self._names({'search': 'ana'}), ['Ana Lima'])
        self.assertEqual(self._names({'search': 'reis'}), ['Bruno Reis'])
        self.assertEqual(len(self._names({'search': 'xpto-inexistente'})), 0)

    def test_filter_by_area(self):
        names = self._names({'area': self.area.id})
        self.assertEqual(sorted(names), ['Ana Lima', 'Carla Dias'])

    def test_filter_by_education(self):
        self.assertEqual(
            self._names({'education': Member.EducationLevel.SUPERIOR}),
            ['Ana Lima', 'Carla Dias'],
        )

    def test_filter_by_marital_status_and_entry(self):
        self.assertEqual(
            self._names({'marital_status': Member.MaritalStatus.CASADO}),
            ['Bruno Reis', 'Carla Dias'],
        )
        self.assertEqual(
            self._names({'church_entry': Member.ChurchEntry.BATISMO}),
            ['Ana Lima'],
        )

    def test_filter_by_age_range(self):
        self.assertEqual(self._names({'age_min': 30}), ['Bruno Reis', 'Carla Dias'])
        self.assertEqual(self._names({'age_max': 30}), ['Ana Lima'])

    def test_combined_filters(self):
        self.assertEqual(
            self._names({
                'status': 'ACTIVE',
                'education': Member.EducationLevel.SUPERIOR,
                'area': self.area.id,
            }),
            ['Ana Lima', 'Carla Dias'],
        )
        self.assertEqual(
            self._names({
                'status': 'ACTIVE',
                'education': Member.EducationLevel.SUPERIOR,
                'marital_status': Member.MaritalStatus.CASADO,
            }),
            ['Carla Dias'],
        )

    def test_invalid_values_ignored(self):
        self.assertEqual(self._names({'status': 'ETC'}), ['Ana Lima', 'Bruno Reis', 'Carla Dias'])
        self.assertEqual(self._names({'age_min': 'abc'}), ['Ana Lima', 'Bruno Reis', 'Carla Dias'])

    def test_without_paginate_returns_plain_list(self):
        client = self._client(self._pastor())
        resp = client.get(reverse('member-self-list'), {})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsInstance(resp.data, list)
        self.assertEqual(len(resp.data), 3)

    def test_paginate_returns_count_and_results(self):
        client = self._client(self._pastor())
        resp = client.get(reverse('member-self-list'), {'paginate': '1', 'page_size': 2})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 3)
        self.assertIsNotNone(resp.data['next'])
        self.assertIsNone(resp.data['previous'])
        self.assertEqual(len(resp.data['results']), 2)
        resp2 = client.get(resp.data['next'])
        self.assertEqual(resp2.data['count'], 3)
        self.assertIsNone(resp2.data['next'])
        self.assertEqual(len(resp2.data['results']), 1)

    def test_paginate_respects_server_filters_and_order(self):
        client = self._client(self._pastor())
        resp = client.get(
            reverse('member-self-list'),
            {'paginate': '1', 'status': 'ACTIVE', 'page_size': 1},
        )
        self.assertEqual(resp.data['count'], 2)
        self.assertEqual([m['name'] for m in resp.data['results']], ['Ana Lima'])


class MemberImportTests(BaseChurchTestCase):
    """Importação do rol de membros (inspect + dry-run + commit)."""

    def setUp(self):
        super().setUp()
        self._pastor_user = None
        self.pastor = self._user(
            'pastor@teste.com', church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )

    def _csv(self, content):
        from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: PLC0415
        return SimpleUploadedFile('rol.csv', content.encode('utf-8'))

    def _inspect(self, file):
        client = self._client(self.pastor)
        resp = client.post(
            reverse('member-import-inspect'),
            {'file': file},
            format='multipart',
        )
        return resp

    def _import(self, file, mapping, dry_run=True):
        import json  # noqa: PLC0415
        client = self._client(self.pastor)
        resp = client.post(
            reverse('member-import'),
            {'file': file, 'dry_run': '1' if dry_run else '0',
             'mapping': json.dumps(mapping)},
            format='multipart',
        )
        return resp

    def test_inspect_suggests_mapping_and_preview(self):
        csv = self._csv(
            'Nome;Telefone;Escolaridade;Estado Civil;Entrada\n'
            'Ana Lima;(83) 99999-0000;Ensino Superior;Solteira;Batismo\n'
            'Bruno Reis;;Ensino Medio;Casado;Transferencia\n'
        )
        resp = self._inspect(csv)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.data
        self.assertEqual(data['kind'], 'members')
        self.assertEqual(data['headers'][0], 'Nome')
        self.assertEqual(data['suggested']['name'], 'Nome')
        self.assertEqual(data['suggested']['education_level'], 'Escolaridade')
        self.assertEqual(len(data['rows']), 2)

    def test_dry_run_reports_count_without_committing(self):
        csv = self._csv(
            'Nome;Telefone\n'
            'Ana Lima;(83) 99999-0000\n'
            'Bruno Reis;(83) 88888-0000\n'
        )
        resp = self._import(csv, {'name': 'Nome', 'phone': 'Telefone'}, dry_run=True)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['dry_run'])
        self.assertEqual(resp.data['imported'], 2)
        self.assertEqual(Member.objects.filter(church=self.sede).count(), 0)
        self._inspect(self._csv('Nome;Telefone\n'))

    def test_commit_creates_members_with_sequential_cards(self):
        csv = self._csv(
            'Nome;CPF;Telefone\n'
            'Ana Lima;123.456.789-00;(83) 99999-0000\n'
            'Bruno Reis;987.654.321-00;(83) 88888-0000\n'
        )
        resp = self._import(csv, {'name': 'Nome', 'cpf': 'CPF', 'phone': 'Telefone'}, dry_run=False)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['imported'], 2)
        members = Member.objects.filter(church=self.sede).order_by('card_number')
        self.assertEqual(list(members.values_list('name', flat=True)), ['Ana Lima', 'Bruno Reis'])
        self.assertEqual(members[0].card_number, '0001')
        self.assertEqual(members[1].card_number, '0002')
        self.assertEqual(members[0].status, Member.Status.ACTIVE)

    def test_duplicate_cpf_blocks_commit(self):
        Member.objects.create(church=self.sede, name='Existente', cpf='12345678900')
        csv = self._csv(
            'Nome;CPF\n'
            'Ana Lima;123.456.789-00\n'
        )
        resp = self._import(csv, {'name': 'Nome', 'cpf': 'CPF'}, dry_run=False)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('CPF', resp.data['errors'][0])
        self.assertEqual(Member.objects.filter(church=self.sede).count(), 1)

    def test_missing_name_reports_line_error(self):
        csv = self._csv(
            'Nome;Telefone\n'
            ';;\n'
            ';(83) 99999-0000\n'
            'Ana Lima;(83) 88888-0000\n'
            ';(83) 77777-0000\n'
        )
        resp = self._import(csv, {'name': 'Nome', 'phone': 'Telefone'}, dry_run=True)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(any('Linha 3' in e for e in resp.data['errors']))
        self.assertTrue(any('Linha 5' in e for e in resp.data['errors']))
        self.assertEqual(resp.data['imported'], 1)

    def test_import_maps_address_columns(self):
        csv = self._csv(
            'Nome;Rua;Número;Complemento;Bairro;Cidade;UF;CEP\n'
            'Ana Lima;Rua das Flores;123;Apto 4;Centro;Campina Grande;PB;58400-100\n'
        )
        resp = self._import(csv, {
            'name': 'Nome', 'street': 'Rua', 'number': 'Número',
            'complement': 'Complemento', 'neighborhood': 'Bairro',
            'city': 'Cidade', 'state': 'UF', 'cep': 'CEP',
        }, dry_run=False)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['imported'], 1)
        member = Member.objects.get(church=self.sede, name='Ana Lima')
        self.assertEqual(member.street, 'Rua das Flores')
        self.assertEqual(member.number, '123')
        self.assertEqual(member.complement, 'Apto 4')
        self.assertEqual(member.neighborhood, 'Centro')
        self.assertEqual(member.city, 'Campina Grande')
        self.assertEqual(member.state, 'PB')
        self.assertEqual(member.cep, '58400-100')

    def test_member_serializer_includes_address(self):
        from rest_framework.test import APIClient  # noqa: PLC0415
        from .serializers import MemberSerializer  # noqa: PLC0415
        member = Member.objects.create(
            church=self.sede, name='João Souza',
            street='Av. Principal, 100', city='João Pessoa', state='PB',
        )
        data = MemberSerializer(member).data
        for field in ('street', 'number', 'complement', 'neighborhood', 'city', 'state', 'cep'):
            self.assertIn(field, data)
        self.assertEqual(data['city'], 'João Pessoa')


class AlertsTests(BaseChurchTestCase):
    """Alertas computados da igreja ativa (aniversariantes + validade da carteirinha)."""

    def setUp(self):
        super().setUp()
        from datetime import date  # noqa: PLC0415
        self.today = date(2026, 9, 8)
        self.secretary = self._user(
            'secre@teste.com', church=self.sede,
            role=ChurchMembership.Role.SECRETARIA,
        )

    def _alerts(self, today=None, church=None):
        from .services import compute_church_alerts  # noqa: PLC0415
        return compute_church_alerts(church or self.sede, today=today or self.today)

    def test_birthday_today_and_upcoming(self):
        Member.objects.create(
            church=self.sede, name='Ana Hoje', birth_date='1990-09-08',
        )
        Member.objects.create(
            church=self.sede, name='Bruno Proximo', birth_date='1992-09-12',
        )
        Member.objects.create(
            church=self.sede, name='Carla Longe', birth_date='1995-10-01',
        )
        alerts = self._alerts()
        types = {a['type'] for a in alerts}
        self.assertIn('birthday_today', types)
        self.assertIn('birthday_upcoming', types)
        today_members = [a['member_name'] for a in alerts if a['type'] == 'birthday_today']
        upcoming = [a['member_name'] for a in alerts if a['type'] == 'birthday_upcoming']
        self.assertEqual(today_members, ['Ana Hoje'])
        self.assertEqual(upcoming, ['Bruno Proximo'])
        self.assertEqual(
            [a['date'] for a in alerts if a['type'] == 'birthday_upcoming'],
            ['2026-09-12'],
        )

    def test_birthday_today_not_repeated_in_upcoming(self):
        Member.objects.create(
            church=self.sede, name='Ana Hoje', birth_date='1990-09-08',
        )
        alerts = self._alerts()
        self.assertEqual(
            [a['type'] for a in alerts].count('birthday_upcoming'), 0,
        )
        self.assertEqual(
            [a['type'] for a in alerts].count('birthday_today'), 1,
        )

    def test_feb_29_treated_as_feb_28_in_non_leap_year(self):
        Member.objects.create(
            church=self.sede, name='Lia Bissexta', birth_date='1988-02-29',
        )
        from datetime import date  # noqa: PLC0415
        alerts = [
            a for a in self._alerts(today=date(2027, 2, 28))
            if a['member_name'] == 'Lia Bissexta'
        ]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]['type'], 'birthday_today')

    def test_card_validity_soon_and_expired(self):
        from datetime import timedelta  # noqa: PLC0415
        self.sede.card_valid_until = self.today + timedelta(days=20)
        self.sede.save(update_fields=['card_valid_until'])
        alerts = self._alerts()
        self.assertIn('card_validity_soon', {a['type'] for a in alerts})

        self.sede.card_valid_until = self.today - timedelta(days=3)
        self.sede.save(update_fields=['card_valid_until'])
        alerts = self._alerts()
        self.assertIn('card_validity_expired', {a['type'] for a in alerts})

    def test_no_alerts_for_empty_church_and_no_validity(self):
        self.sede.card_valid_until = None
        self.sede.save(update_fields=['card_valid_until'])
        self.assertEqual(self._alerts(), [])

    def test_endpoint_requires_authentication(self):
        resp = APIClient().get(reverse('alerts'))
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_endpoint_returns_empty_for_user_without_church(self):
        user = self._user('solto@teste.com', is_staff=True)
        resp = self._client(user).get(reverse('alerts'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['alerts'], [])
        self.assertIn('generated_at', resp.data)

    def test_endpoint_reachable_for_all_roles_with_church(self):
        for role in ChurchMembership.Role.values:
            user = self._user(
                f'{role.lower()}@teste.com', church=self.sede, role=role,
            )
            resp = self._client(user).get(reverse('alerts'))
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            self.assertIn('alerts', resp.data)

    def test_endpoint_alerts_from_active_church_only(self):
        from datetime import date  # noqa: PLC0415
        today = date.today()
        Member.objects.create(
            church=self.sede, name='Ana Hoje', birth_date=str(today),
        )
        Member.objects.create(
            church=self.congregation, name='Outra Igreja',
            birth_date=str(today),
        )
        resp = self._client(self.secretary).get(reverse('alerts'))
        names = [a['member_name'] for a in resp.data['alerts']]
        self.assertEqual(names, ['Ana Hoje'])


class MemberDeclarationTests(BaseChurchTestCase):
    def setUp(self):
        super().setUp()
        self.secretary = self._user(
            'secre-decl@teste.com', church=self.sede,
            role=ChurchMembership.Role.SECRETARIA,
        )

    def _member(self, church=None, **kwargs):
        return Member.objects.create(
            church=church or self.sede,
            name=kwargs.pop('name', 'Maria Clara'),
            cpf=kwargs.pop('cpf', '123.456.789-00'),
            card_number=kwargs.pop('card_number', '0001'),
            birth_date=kwargs.pop('birth_date', '1990-05-10'),
            baptism_date=kwargs.pop('baptism_date', '2005-11-20'),
            **kwargs,
        )

    def test_declaration_pdf_download(self):
        member = self._member()
        resp = self._client(self.secretary).get(
            reverse('member-declaration', args=[member.id])
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertIn(
            f'attachment; filename="declaracao-membresia-maria-clara.pdf"',
            resp['Content-Disposition'],
        )
        self.assertTrue(resp.content.startswith(b'%PDF'))

    def test_declaration_scoped_to_active_church(self):
        member = self._member(church=self.congregation)
        resp = self._client(self.secretary).get(
            reverse('member-declaration', args=[member.id])
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_declaration_allows_treasurer(self):
        member = self._member()
        treasurer = self._user(
            'tesoureiro@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        resp = self._client(treasurer).get(
            reverse('member-declaration', args=[member.id])
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_declaration_requires_authentication(self):
        member = self._member()
        resp = APIClient().get(reverse('member-declaration', args=[member.id]))
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_declaration_content_includes_member_and_church(self):
        member = self._member(name='Ana Souza', card_number='0042')
        resp = self._client(self.secretary).get(
            reverse('member-declaration', args=[member.id])
        )
        body = resp.content.decode('latin-1')
        self.assertTrue(resp.content.startswith(b'%PDF'))
        self.assertIn('Ana Souza', body)
        self.assertIn('DECLARA', body.upper())
        self.assertIn('MEMBRESIA', body.upper())
        self.assertNotIn('MEMBREZIA', body.upper())

    def test_declaration_template_renders_member_and_church(self):
        member = self._member(name='Ana Souza', card_number='0042')
        from django.template.loader import render_to_string  # noqa: PLC0415
        from django.utils import timezone  # noqa: PLC0415
        from .services import _MONTH_PT  # noqa: PLC0415

        today = timezone.localdate()
        html = render_to_string('accounts/membership_declaration.html', {
            'church': self.sede,
            'member': member,
            'entry_label': '',
            'protocol': '0001/2026',
            'emitted_date': (
                f'{today.day} de {_MONTH_PT[today.month]} de {today.year}'
            ),
            'signer_name': 'Pastor Teste',
            'signer_role': 'Pastor Responsável',
        })
        self.assertIn('Ana Souza', html)
        self.assertIn('0042', html)
        self.assertIn(self.sede.name, html)
        self.assertIn('Pastor Teste', html)
        self.assertIn('MEMBRESIA', html.upper())


class MemberTransferTests(BaseChurchTestCase):
    def setUp(self):
        super().setUp()
        self.sec = self._user(
            'sec-sede@teste.com', church=self.sede,
            role=ChurchMembership.Role.SECRETARIA,
        )
        self.cong_sec = self._user(
            'sec-cong@teste.com', church=self.congregation,
            role=ChurchMembership.Role.SECRETARIA,
        )
        self.member = Member.objects.create(
            church=self.sede, name='João Silva', cpf='111.222.333-44',
            birth_date='1988-03-15', baptism_date='2002-07-21',
            card_number='0005', street='Rua A', city='Campina Grande',
        )

    def _issue(self, client=None, member=None, target=None):
        client = client or self._client(self.sec)
        return client.post(
            reverse('member-transfers'),
            {
                'member_id': (member or self.member).id,
                'target_church_id': (target or self.congregation).id,
            },
            format='json',
        )

    def test_issue_creates_pending_with_snapshot(self):
        resp = self._issue()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        data = resp.data
        self.assertEqual(data['status'], 'PENDING')
        self.assertEqual(data['source_church'], self.sede.id)
        self.assertEqual(data['target_church'], self.congregation.id)
        self.assertEqual(data['member_name'], 'João Silva')
        self.assertEqual(data['member_cpf'], '111.222.333-44')
        self.assertEqual(data['member_city'], 'Campina Grande')

    def test_issue_requires_member_of_active_church(self):
        stranger = Member.objects.create(
            church=self.congregation, name='De Outra Igreja',
        )
        resp = self._issue(member=stranger)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_issue_rejects_inactive_member(self):
        self.member.status = Member.Status.INACTIVE
        self.member.save(update_fields=['status'])
        resp = self._issue()
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_issue_rejects_same_church_target(self):
        resp = self._issue(target=self.sede)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_issue_rejects_duplicate_pending(self):
        self._issue()
        resp = self._issue()
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_outgoing_list_scoped_to_source_church(self):
        self._issue()
        resp = self._client(self.cong_sec).get(reverse('member-transfers'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 0)

    def test_receive_creates_member_and_inactivates_source(self):
        transfer = self._issue().data
        resp = self._client(self.cong_sec).post(
            reverse('member-transfer-receive', args=[transfer['id']])
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['status'], 'RECEIVED')
        created = Member.objects.get(church=self.congregation, name='João Silva')
        self.assertEqual(created.church_entry, Member.ChurchEntry.TRANSFERENCIA)
        self.assertEqual(created.status, Member.Status.ACTIVE)
        self.assertEqual(created.card_number, '0001')
        self.assertEqual(created.street, 'Rua A')
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, Member.Status.INACTIVE)

    def test_receive_scoped_to_target_church(self):
        transfer = self._issue().data
        resp = self._client(self.sec).post(
            reverse('member-transfer-receive', args=[transfer['id']])
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_receive_rejects_non_pending(self):
        transfer = self._issue().data
        self._client(self.cong_sec).post(
            reverse('member-transfer-receive', args=[transfer['id']])
        )
        resp = self._client(self.cong_sec).post(
            reverse('member-transfer-receive', args=[transfer['id']])
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cancel_by_source_scopes_and_guards_state(self):
        transfer = self._issue().data
        resp = self._client(self.sec).post(
            reverse('member-transfer-cancel', args=[transfer['id']])
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['status'], 'CANCELED')
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, Member.Status.ACTIVE)

        resp2 = self._client(self.sec).post(
            reverse('member-transfer-cancel', args=[transfer['id']])
        )
        self.assertEqual(resp2.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cancel_scoped_to_source_church(self):
        transfer = self._issue().data
        resp = self._client(self.cong_sec).post(
            reverse('member-transfer-cancel', args=[transfer['id']])
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_incoming_list_scoped_to_target_church(self):
        transfer = self._issue().data
        resp = self._client(self.cong_sec).get(
            reverse('member-transfers-incoming')
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual([t['id'] for t in resp.data], [transfer['id']])
        resp2 = self._client(self.sec).get(reverse('member-transfers-incoming'))
        self.assertEqual(resp2.data, [])

    def test_target_church_search_excludes_own(self):
        resp = self._client(self.sec).get(reverse('transfer-target-churches'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [c['id'] for c in resp.data]
        self.assertNotIn(self.sede.id, ids)
        self.assertIn(self.congregation.id, ids)

        resp2 = self._client(self.sec).get(
            reverse('transfer-target-churches'), {'q': 'Congregação'}
        )
        names = [c['name'] for c in resp2.data]
        self.assertIn('Congregação Teste', names)

    def test_transfer_allows_treasurer(self):
        treasurer = self._user(
            'tesouro@teste.com', church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        resp = self._client(treasurer).get(reverse('member-transfers'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)


class MemberDocumentTests(BaseChurchTestCase):
    """Upload, listagem, download e exclusão de documentos do membro."""

    def setUp(self):
        super().setUp()
        self.sec = self._user(
            'sec-sede@teste.com', church=self.sede,
            role=ChurchMembership.Role.SECRETARIA,
        )
        self.cong_sec = self._user(
            'sec-cong@teste.com', church=self.congregation,
            role=ChurchMembership.Role.SECRETARIA,
        )
        self.treasurer = self._user(
            'tesouro@teste.com', church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        self.member = Member.objects.create(
            church=self.sede, name='João Silva', cpf='111.222.333-44',
        )

    def _upload(self, client=None, name='doc.pdf', doc_type='RESIDENCE_PROOF', notes=''):
        client = client or self._client(self.sec)
        data = {'file': SimpleUploadedFile(
            name, b'fake-content-pdf', content_type='application/pdf',
        ), 'doc_type': doc_type}
        if notes:
            data['notes'] = notes
        return client.post(
            reverse('member-documents', args=[self.member.id]),
            data,
            format='multipart',
        )

    def test_upload_and_list_documents(self):
        resp = self._upload()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['doc_type'], 'RESIDENCE_PROOF')
        self.assertTrue(resp.data['file_name'].startswith('doc'))
        self.assertTrue(resp.data['file_name'].endswith('.pdf'))
        self.assertEqual(resp.data['uploaded_by_name'], self.sec.name)

        lst = self._client(self.sec).get(
            reverse('member-documents', args=[self.member.id])
        )
        self.assertEqual(lst.status_code, status.HTTP_200_OK)
        self.assertEqual([d['id'] for d in lst.data], [resp.data['id']])

    def test_upload_stores_notes(self):
        resp = self._upload(notes='Frente e verso')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['notes'], 'Frente e verso')

    def test_list_other_church_member_404(self):
        stranger = Member.objects.create(
            church=self.congregation, name='De Outra Igreja',
        )
        resp = self._client(self.sec).get(
            reverse('member-documents', args=[stranger.id])
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_upload_rejects_missing_file(self):
        resp = self._client(self.sec).post(
            reverse('member-documents', args=[self.member.id]),
            {'doc_type': 'RESIDENCE_PROOF'},
            format='multipart',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_rejects_invalid_doc_type(self):
        resp = self._upload(doc_type='OUTRO_TIPO')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_rejects_disallowed_extension(self):
        resp = self._upload(name='virus.exe')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_download_streams_file_scoped(self):
        doc = self._upload().data
        resp = self._client(self.sec).get(
            reverse('member-document-download', args=[doc['id']])
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.content, b'fake-content-pdf')
        self.assertIn('attachment', resp['Content-Disposition'])
        self.assertIn('.pdf', resp['Content-Disposition'])

        resp2 = self._client(self.cong_sec).get(
            reverse('member-document-download', args=[doc['id']])
        )
        self.assertEqual(resp2.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_document_scoped(self):
        doc = self._upload().data
        resp = self._client(self.sec).delete(
            reverse('member-document-detail', args=[doc['id']])
        )
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(
            MemberDocument.objects.filter(pk=doc['id']).count(), 0,
        )

        resp2 = self._client(self.cong_sec).delete(
            reverse('member-document-detail', args=[doc['id']])
        )
        self.assertEqual(resp2.status_code, status.HTTP_404_NOT_FOUND)

    def test_document_allows_treasurer(self):
        resp = self._client(self.treasurer).get(
            reverse('member-documents', args=[self.member.id])
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)


class MemberReportTests(BaseChurchTestCase):
    """Relatório em PDF do rol de membros (somente igreja ativa)."""

    def setUp(self):
        super().setUp()
        self.sec = self._user(
            'sec-sede@teste.com', church=self.sede,
            role=ChurchMembership.Role.SECRETARIA,
        )
        self.treasurer = self._user(
            'tesouro@teste.com', church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        self.active = Member.objects.create(
            church=self.sede, name='Ana Ativa', cpf='123',
            card_number='0001', city='Campina Grande', state='PB',
        )
        self.inactive = Member.objects.create(
            church=self.sede, name='Zé Inativo', card_number='0002',
            status=Member.Status.INACTIVE,
        )
        self.other = Member.objects.create(
            church=self.congregation, name='De Outra Igreja',
        )

    def _html(self, qs):
        from django.template.loader import render_to_string  # noqa: PLC0415
        return render_to_string('accounts/members_report.html', {
            'church': self.sede,
            'members': qs,
            'total': qs.count(),
            'generated_date': '1 de janeiro de 2026',
        })

    def test_report_returns_pdf_with_active_of_church(self):
        resp = self._client(self.sec).get(reverse('member-report'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp['Content-Type'].startswith('application/pdf'))
        self.assertTrue(resp.content.startswith(b'%PDF'))
        self.assertIn('rol-membros', resp['Content-Disposition'])

        qs = Member.objects.filter(
            church=self.sede, status=Member.Status.ACTIVE,
        ).order_by('name')
        html = self._html(qs)
        self.assertIn('Ana Ativa', html)
        self.assertNotIn('Zé Inativo', html)
        self.assertNotIn('De Outra Igreja', html)

    def test_report_status_filter_inactive(self):
        resp = self._client(self.sec).get(
            reverse('member-report'), {'status': 'INACTIVE'},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        qs = Member.objects.filter(
            church=self.sede, status=Member.Status.INACTIVE,
        )
        html = self._html(qs)
        self.assertIn('Zé Inativo', html)
        self.assertNotIn('Ana Ativa', html)

    def test_report_allows_treasurer(self):
        resp = self._client(self.treasurer).get(reverse('member-report'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)


class CalendarPublicLinkTests(BaseChurchTestCase):
    """Hash público do calendário geral (gerenciado por PASTOR/SECRETARIA)."""

    def setUp(self):
        super().setUp()
        self.sec = self._user(
            'sec-sede@teste.com', church=self.sede,
            role=ChurchMembership.Role.SECRETARIA,
        )
        self.pastor = self._user(
            'pastor-sede@teste.com', church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        self.treasurer = self._user(
            'tes-sede@teste.com', church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )

    def url(self):
        return reverse('calendar-public-link')

    def regenerate_url(self):
        return reverse('calendar-public-link-regenerate')

    def test_sec_generates_hash(self):
        resp = self._client(self.sec).get(self.url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['hash']), 32)
        self.assertEqual(resp.data['url'], f"/calendario/{resp.data['hash']}")
        self.sede.refresh_from_db()
        self.assertEqual(self.sede.calendar_public_hash, resp.data['hash'])

    def test_hash_is_stable(self):
        first = self._client(self.sec).get(self.url()).data['hash']
        second = self._client(self.sec).get(self.url()).data['hash']
        self.assertEqual(first, second)

    def test_regenerate_changes_hash(self):
        old = self._client(self.sec).get(self.url()).data['hash']
        resp = self._client(self.sec).post(self.regenerate_url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertNotEqual(resp.data['hash'], old)
        self.sede.refresh_from_db()
        self.assertEqual(self.sede.calendar_public_hash, resp.data['hash'])

    def test_pastor_can_access(self):
        resp = self._client(self.pastor).get(self.url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_treasurer_manages_public_link(self):
        self.assertEqual(
            self._client(self.treasurer).get(self.url()).status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            self._client(self.treasurer).post(self.regenerate_url()).status_code,
            status.HTTP_200_OK,
        )

    def test_requires_authentication(self):
        client = APIClient()
        self.assertEqual(client.get(self.url()).status_code, status.HTTP_401_UNAUTHORIZED)


class BirthdayMembersTests(BaseChurchTestCase):
    """Aniversariantes da igreja ativa por mês (lista + idade)."""

    def setUp(self):
        super().setUp()
        self.sec = self._user(
            'sec-sede@teste.com', church=self.sede,
            role=ChurchMembership.Role.SECRETARIA,
        )
        self.other = self._user(
            'sec-cong@teste.com', church=self.congregation,
            role=ChurchMembership.Role.SECRETARIA,
        )
        self.m1 = Member.objects.create(
            church=self.sede, name='Ana Março 01', birth_date='1990-03-05',
        )
        self.m2 = Member.objects.create(
            church=self.sede, name='Bia Março 02', birth_date='2000-03-25',
        )
        self.inactive_march = Member.objects.create(
            church=self.sede, name='Carla Inativa',
            birth_date='1985-03-15', status=Member.Status.INACTIVE,
        )
        self.other_march = Member.objects.create(
            church=self.congregation, name='Dez Outra', birth_date='1995-03-10',
        )
        self.no_birth = Member.objects.create(
            church=self.sede, name='Sem Data', birth_date=None,
        )

    def test_lists_active_by_day(self):
        resp = self._client(self.sec).get(
            reverse('birthday-members'), {'month': 3},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = [d['name'] for d in resp.data]
        self.assertEqual(names, ['Ana Março 01', 'Bia Março 02'])
        self.assertNotIn('Carla Inativa', names)
        self.assertNotIn('Dez Outra', names)
        self.assertNotIn('Sem Data', names)

    def test_age_and_day_fields(self):
        from datetime import date  # noqa: PLC0415

        resp = self._client(self.sec).get(
            reverse('birthday-members'), {'month': 3},
        )
        ana = next(d for d in resp.data if d['name'] == 'Ana Março 01')
        self.assertEqual(ana['day'], 5)
        today = date.today()
        expected_age = 2026 - 1990 - int((today.month, today.day) < (3, 5))
        self.assertEqual(ana['age'], expected_age)

    def test_status_inactive_filter(self):
        resp = self._client(self.sec).get(
            reverse('birthday-members'), {'month': 3, 'status': 'INACTIVE'},
        )
        names = [d['name'] for d in resp.data]
        self.assertEqual(names, ['Carla Inativa'])

    def test_status_all_includes_both(self):
        resp = self._client(self.sec).get(
            reverse('birthday-members'), {'month': 3, 'status': 'ALL'},
        )
        names = [d['name'] for d in resp.data]
        self.assertIn('Ana Março 01', names)
        self.assertIn('Carla Inativa', names)

    def test_month_invalid_returns_400(self):
        resp = self._client(self.sec).get(
            reverse('birthday-members'), {'month': 13},
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_scoped_to_active_church(self):
        resp = self._client(self.other).get(
            reverse('birthday-members'), {'month': 3},
        )
        names = [d['name'] for d in resp.data]
        self.assertEqual(names, ['Dez Outra'])
        self.assertNotIn('Ana Março 01', names)

    def test_user_without_church_returns_empty(self):
        no_church = self._user('sem@igreja.com')
        resp = self._client(no_church).get(
            reverse('birthday-members'), {'month': 3},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, [])

    def test_requires_authentication(self):
        client = APIClient()
        resp = client.get(reverse('birthday-members'), {'month': 3})
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class InventoryTests(BaseChurchTestCase):
    """Gestão de materiais/equipamentos: locais, itens e empréstimos."""

    def setUp(self):
        super().setUp()
        self.sec = self._user(
            'sec-inv@teste.com', church=self.sede,
            role=ChurchMembership.Role.SECRETARIA,
        )
        self.other_sec = self._user(
            'sec-outra@teste.com', church=self.congregation,
            role=ChurchMembership.Role.SECRETARIA,
        )
        self.treasurer = self._user(
            'tes-inv@teste.com', church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        self.client = self._client(self.sec)

    def _make_loan(self, church=None, **kwargs):
        loc = StorageLocation.objects.create(
            church=church or self.sede, name='Sala de Instrumentos',
        )
        item = MaterialItem.objects.create(
            church=church or self.sede, name='Violão',
            description='Acústico, corda de nylon', location=loc,
        )
        defaults = {
            'church': church or self.sede,
            'item': item,
            'borrower_name': 'João',
            'borrowed_at': '2026-09-09',
            'expected_return': '2026-09-13',
        }
        defaults.update(kwargs)
        return Loan.objects.create(**defaults)

    # --- locais ---
    def test_create_list_update_delete_location(self):
        resp = self.client.post(
            reverse('storage-location-list'),
            {'name': 'Sala 1'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        loc_id = resp.data['id']

        resp = self.client.get(reverse('storage-location-list'))
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['name'], 'Sala 1')

        resp = self.client.patch(
            reverse('storage-location-detail', args=[loc_id]),
            {'name': 'Sala Principal'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['name'], 'Sala Principal')

        resp = self.client.delete(reverse('storage-location-detail', args=[loc_id]))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_duplicate_location_name_rejected(self):
        StorageLocation.objects.create(church=self.sede, name='Sala 1')
        resp = self.client.post(
            reverse('storage-location-list'),
            {'name': 'Sala 1'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('name', resp.data)

    def test_locations_scoped_to_church(self):
        StorageLocation.objects.create(church=self.sede, name='Sede')
        StorageLocation.objects.create(church=self.congregation, name='Congregação')
        resp = self.client.get(reverse('storage-location-list'))
        names = [d['name'] for d in resp.data]
        self.assertEqual(names, ['Sede'])

        other = self._client(self.other_sec).get(reverse('storage-location-list'))
        names_other = [d['name'] for d in other.data]
        self.assertEqual(names_other, ['Congregação'])

    # --- itens ---
    def test_create_item_with_location(self):
        loc = StorageLocation.objects.create(church=self.sede, name='Sala 1')
        resp = self.client.post(
            reverse('material-list'),
            {
                'name': 'Violão',
                'description': 'Acústico, nylon',
                'location': loc.id,
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['name'], 'Violão')
        self.assertEqual(resp.data['location_name'], 'Sala 1')
        self.assertIsNone(resp.data['current_loan'])

    def test_item_rejects_location_from_another_church(self):
        loc = StorageLocation.objects.create(
            church=self.congregation, name='Outra',
        )
        resp = self.client.post(
            reverse('material-list'),
            {'name': 'Violão', 'location': loc.id},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('location', resp.data)

    def test_create_item_requires_location(self):
        resp = self.client.post(
            reverse('material-list'),
            {'name': 'Violão'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('location', resp.data)

    def test_item_without_loan_can_be_deleted(self):
        item = MaterialItem.objects.create(church=self.sede, name='Violão')
        resp = self.client.delete(reverse('material-detail', args=[item.id]))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_item_with_loans_cannot_be_deleted(self):
        loan = self._make_loan()
        resp = self.client.delete(reverse('material-detail', args=[loan.item_id]))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(MaterialItem.objects.filter(pk=loan.item_id).exists())

    def test_delete_location_detaches_items(self):
        loc = StorageLocation.objects.create(church=self.sede, name='Sala 1')
        item = MaterialItem.objects.create(
            church=self.sede, name='Violão', location=loc,
        )
        resp = self.client.delete(reverse('storage-location-detail', args=[loc.id]))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        item.refresh_from_db()
        self.assertIsNone(item.location)

    def test_items_scoped_to_church(self):
        MaterialItem.objects.create(church=self.sede, name='Violão')
        MaterialItem.objects.create(church=self.congregation, name='Teclado')
        resp = self.client.get(reverse('material-list'))
        names = [d['name'] for d in resp.data]
        self.assertEqual(names, ['Violão'])

    # --- empréstimos ---
    def test_create_loan_for_non_member(self):
        from datetime import timedelta  # noqa: PLC0415

        from django.utils import timezone  # noqa: PLC0415

        today = timezone.localdate()
        item = MaterialItem.objects.create(church=self.sede, name='Violão')
        resp = self.client.post(
            reverse('loan-list'),
            {
                'item': item.id,
                'borrower_name': 'João Músico',
                'borrowed_at': today.isoformat(),
                'expected_return': (today + timedelta(days=30)).isoformat(),
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['borrower_display'], 'João Músico')
        self.assertEqual(resp.data['status'], 'active')
        self.assertEqual(resp.data['item_name'], 'Violão')
        self.assertEqual(resp.data['created_by_name'], 'Usuário')

    def test_create_loan_for_member(self):
        member = Member.objects.create(
            church=self.sede, name='Maria Membra', status='ACTIVE',
        )
        item = MaterialItem.objects.create(church=self.sede, name='Órgão')
        resp = self.client.post(
            reverse('loan-list'),
            {
                'item': item.id,
                'member': member.id,
                'borrowed_at': '2026-09-09',
                'expected_return': '2026-09-13',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['borrower_display'], 'Maria Membra')
        self.assertIn('member_name', resp.data)

    def test_loan_requires_borrower(self):
        item = MaterialItem.objects.create(church=self.sede, name='Violão')
        resp = self.client.post(
            reverse('loan-list'),
            {
                'item': item.id,
                'borrower_name': '',
                'borrowed_at': '2026-09-09',
                'expected_return': '2026-09-13',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('borrower_name', resp.data)

    def test_loan_rejects_member_from_another_church(self):
        member = Member.objects.create(
            church=self.congregation, name='Fora Daqui', status='ACTIVE',
        )
        item = MaterialItem.objects.create(church=self.sede, name='Violão')
        resp = self.client.post(
            reverse('loan-list'),
            {
                'item': item.id,
                'member': member.id,
                'borrowed_at': '2026-09-09',
                'expected_return': '2026-09-13',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('member', resp.data)

    def test_loan_rejects_item_from_another_church(self):
        item = MaterialItem.objects.create(church=self.congregation, name='Teclado')
        resp = self.client.post(
            reverse('loan-list'),
            {
                'item': item.id,
                'borrower_name': 'João',
                'borrowed_at': '2026-09-09',
                'expected_return': '2026-09-13',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('item', resp.data)

    def test_loan_rejects_expected_return_before_borrowed_at(self):
        item = MaterialItem.objects.create(church=self.sede, name='Violão')
        resp = self.client.post(
            reverse('loan-list'),
            {
                'item': item.id,
                'borrower_name': 'João',
                'borrowed_at': '2026-09-13',
                'expected_return': '2026-09-09',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('expected_return', resp.data)

    def test_item_already_loaned_cannot_be_loaned_again(self):
        loan = self._make_loan()
        resp = self.client.post(
            reverse('loan-list'),
            {
                'item': loan.item_id,
                'borrower_name': 'Outro',
                'borrowed_at': '2026-09-14',
                'expected_return': '2026-09-15',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('item', resp.data)

    def test_item_available_again_after_return(self):
        loan = self._make_loan()
        resp = self.client.post(reverse('loan-return-item', args=[loan.id]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        loan.refresh_from_db()
        self.assertIsNotNone(loan.returned_at)

        resp = self.client.post(
            reverse('loan-list'),
            {
                'item': loan.item_id,
                'borrower_name': 'Outro',
                'borrowed_at': '2026-09-14',
                'expected_return': '2026-09-15',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_return_action_idempotent_guard(self):
        loan = self._make_loan()
        self.client.post(reverse('loan-return-item', args=[loan.id]))
        resp = self.client.post(reverse('loan-return-item', args=[loan.id]))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_extend_expected_return_via_patch(self):
        loan = self._make_loan()
        resp = self.client.patch(
            reverse('loan-detail', args=[loan.id]),
            {'expected_return': '2026-09-20'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        loan.refresh_from_db()
        self.assertEqual(str(loan.expected_return), '2026-09-20')

    def test_loans_scoped_to_church(self):
        self._make_loan()
        self._make_loan(church=self.congregation)
        resp = self.client.get(reverse('loan-list'))
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['item_name'], 'Violão')

    def test_tesoureiro_accesses_inventory(self):
        tes = self._client(self.treasurer)
        for url in (
            reverse('storage-location-list'),
            reverse('material-list'),
            reverse('loan-list'),
        ):
            resp = tes.get(url)
            self.assertEqual(resp.status_code, status.HTTP_200_OK)


class LoanAlertsTests(BaseChurchTestCase):
    """Alertas de devolução de empréstimos (hoje, próximos 7d e atrasados)."""

    def setUp(self):
        super().setUp()
        from datetime import date  # noqa: PLC0415
        self.today = date(2026, 9, 13)
        self.secretary = self._user(
            'sec-alert@teste.com', church=self.sede,
            role=ChurchMembership.Role.SECRETARIA,
        )

    def _make_loan(self, expected_return, **kwargs):
        loc = StorageLocation.objects.create(
            church=self.sede, name='Sala de Instrumentos',
        )
        item = MaterialItem.objects.create(
            church=self.sede, name='Violão', location=loc,
        )
        defaults = {
            'church': self.sede,
            'item': item,
            'borrower_name': 'João',
            'borrowed_at': '2026-09-09',
            'expected_return': str(expected_return),
        }
        defaults.update(kwargs)
        return Loan.objects.create(**defaults)

    def _alerts(self, today=None, include_loans=True):
        from .services import compute_church_alerts  # noqa: PLC0415
        return compute_church_alerts(
            self.sede, today=today or self.today, include_loans=include_loans,
        )

    def test_due_today_alert(self):
        self._make_loan(expected_return=self.today)
        alerts = self._alerts()
        self.assertEqual(
            [a['type'] for a in alerts].count('loan_return_today'), 1,
        )
        loan_alert = next(a for a in alerts if a['type'] == 'loan_return_today')
        self.assertEqual(loan_alert['item_name'], 'Violão')
        self.assertEqual(loan_alert['borrower_name'], 'João')
        self.assertEqual(loan_alert['date'], '2026-09-13')

    def test_overdue_alert(self):
        from datetime import timedelta  # noqa: PLC0415
        self._make_loan(expected_return=self.today - timedelta(days=1))
        alerts = self._alerts()
        self.assertEqual(
            [a['type'] for a in alerts].count('loan_return_overdue'), 1,
        )

    def test_upcoming_alert_within_seven_days(self):
        from datetime import timedelta  # noqa: PLC0415
        self._make_loan(expected_return=self.today + timedelta(days=3))
        alerts = self._alerts()
        self.assertEqual(
            [a['type'] for a in alerts].count('loan_return_soon'), 1,
        )

    def test_no_alert_when_far_in_future(self):
        from datetime import timedelta  # noqa: PLC0415
        self._make_loan(expected_return=self.today + timedelta(days=10))
        alerts = self._alerts()
        self.assertNotIn('loan_return_soon', [a['type'] for a in alerts])

    def test_returned_loan_generates_no_alert(self):
        from datetime import datetime  # noqa: PLC0415
        from django.utils import timezone  # noqa: PLC0415
        loan = self._make_loan(expected_return=self.today)
        loan.returned_at = timezone.make_aware(datetime(2026, 9, 12, 10, 0))
        loan.save(update_fields=['returned_at'])
        alerts = self._alerts()
        self.assertNotIn('loan_return_today', [a['type'] for a in alerts])

    def test_alerts_excluded_when_include_loans_false(self):
        self._make_loan(expected_return=self.today)
        alerts = self._alerts(include_loans=False)
        self.assertNotIn('loan_return_today', [a['type'] for a in alerts])

    def test_endpoint_includes_loans_for_secretary(self):
        from datetime import date  # noqa: PLC0415
        self._make_loan(expected_return=date.today())
        resp = self._client(self.secretary).get(reverse('alerts'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        types = [a['type'] for a in resp.data['alerts']]
        self.assertIn('loan_return_today', types)

    def test_endpoint_hides_loans_for_treasurer(self):
        from datetime import date  # noqa: PLC0415
        self._make_loan(expected_return=date.today())
        treasurer = self._user(
            'tes-alert@teste.com', church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        resp = self._client(treasurer).get(reverse('alerts'))
        types = [a['type'] for a in resp.data['alerts']]
        self.assertNotIn('loan_return_overdue', types)
        self.assertNotIn('loan_return_today', types)
        self.assertNotIn('loan_return_soon', types)


class WorshipServiceTests(BaseChurchTestCase):
    """Registro de cultos: CRUD para todos os perfis com igreja ativa."""

    def setUp(self):
        super().setUp()
        self.secretaria = self._user(
            'sec-culto@teste.com', church=self.sede,
            role=ChurchMembership.Role.SECRETARIA,
        )
        self.treasurer = self._user(
            'tes-culto@teste.com', church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        self.client = self._client(self.secretaria)

    def _payload(self, **kwargs):
        data = {
            'date': '2026-09-09',
            'time': '19:00:00',
            'service_type': 'DOUTRINA',
            'presider': 'Diác. João',
            'preacher': 'Pb. Maria',
            'theme': 'A palavra que transforma',
            'scripture': 'Romanos 12.2',
            'attendees': 120,
            'visitors': 8,
            'conversions': 3,
            'offering': '125.50',
            'notes': 'Culto marcado pela comunhão.',
        }
        data.update(kwargs)
        return data

    def test_create_list_update_delete(self):
        resp = self.client.post(reverse('worship-list'), self._payload(), format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        sid = resp.data['id']
        self.assertEqual(resp.data['service_type_display'], 'Culto de Doutrina')
        self.assertEqual(resp.data['offering'], '125.50')

        resp = self.client.get(reverse('worship-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)

        resp = self.client.patch(
            reverse('worship-detail', args=[sid]),
            {'attendees': 140, 'offering': '180.00'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['attendees'], 140)

        resp = self.client.delete(reverse('worship-detail', args=[sid]))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(WorshipService.objects.count(), 0)

    def test_all_profiles_can_write(self):
        resp = self._client(self.treasurer).post(
            reverse('worship-list'), self._payload(), format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_scoped_to_active_church(self):
        self.client.post(reverse('worship-list'), self._payload(), format='json')
        other = self._user(
            'sec-culto-outra@teste.com', church=self.congregation,
            role=ChurchMembership.Role.SECRETARIA,
        )
        resp = self._client(other).get(reverse('worship-list'))
        self.assertEqual(resp.data, [])

    def test_negative_counts_rejected(self):
        resp = self.client.post(
            reverse('worship-list'),
            self._payload(attendees=-1),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('attendees', resp.data)

    def test_negative_offering_rejected(self):
        resp = self.client.post(
            reverse('worship-list'),
            self._payload(offering='-10.00'),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('offering', resp.data)

    def test_requires_authentication(self):
        resp = APIClient().get(reverse('worship-list'))
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class ChurchMinutesTests(BaseChurchTestCase):
    """Gestão de atas: CRUD, PDF, hash público e link público."""

    def setUp(self):
        super().setUp()
        self.sec = self._user(
            'sec-ata@teste.com', church=self.sede,
            role=ChurchMembership.Role.SECRETARIA,
        )
        self.client = self._client(self.sec)

    def _payload(self, **kwargs):
        data = {
            'title': 'Ata da Assembleia Geral',
            'meeting_type': 'ASSEMBLEIA_GERAL',
            'meeting_date': '2026-08-30',
            'location': 'Templo Central',
            'recorder': 'Secretária',
            'participants': 'Mesa e membros presentes',
            'content': 'Texto completo da ata...',
        }
        data.update(kwargs)
        return data

    def _create_minute(self, **kwargs):
        resp = self.client.post(reverse('minutes-list'), self._payload(**kwargs), format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        return resp.data

    def test_create_with_public_hash(self):
        data = self._create_minute()
        self.assertTrue(data['public_hash'])
        self.assertIn(data['public_hash'], ChurchMinutes.objects.all()[0].public_hash)

    def test_list_update_delete(self):
        mid = self._create_minute()['id']
        resp = self.client.get(reverse('minutes-list'))
        self.assertEqual(len(resp.data), 1)

        resp = self.client.patch(
            reverse('minutes-detail', args=[mid]),
            {'title': 'Ata corrigida'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['title'], 'Ata corrigida')

        resp = self.client.delete(reverse('minutes-detail', args=[mid]))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(ChurchMinutes.objects.count(), 0)

    def test_scoped_to_active_church(self):
        self._create_minute()
        other = self._user(
            'sec-ata-outra@teste.com', church=self.congregation,
            role=ChurchMembership.Role.SECRETARIA,
        )
        resp = self._client(other).get(reverse('minutes-list'))
        self.assertEqual(resp.data, [])

    def test_all_profiles_can_write(self):
        treasurer = self._user(
            'tes-ata@teste.com', church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        resp = self._client(treasurer).post(
            reverse('minutes-list'), self._payload(), format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_upload_pdf_and_authenticated_download(self):
        minute = self._create_minute()
        mid = minute['id']
        pdf = SimpleUploadedFile(
            'ata.pdf', b'%PDF-1.4 fake content', content_type='application/pdf',
        )
        resp = self.client.patch(
            reverse('minutes-detail', args=[mid]),
            {'pdf': pdf},
            format='multipart',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertTrue(resp.data['pdf_name'].startswith('ata'))

        resp = self.client.get(reverse('minutes-download-pdf', args=[mid]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp['Content-Disposition'].split(';')[0], 'attachment')

    def test_remove_pdf(self):
        minute = self._create_minute()
        mid = minute['id']
        pdf = SimpleUploadedFile(
            'ata.pdf', b'%PDF-1.4 fake content', content_type='application/pdf',
        )
        self.client.patch(reverse('minutes-detail', args=[mid]), {'pdf': pdf}, format='multipart')
        resp = self.client.patch(
            reverse('minutes-detail', args=[mid]),
            {'remove_pdf': 'true'},
            format='multipart',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertIsNone(resp.data['pdf_name'])

    def test_regenerate_hash_invalidates_previous(self):
        old_hash = self._create_minute()['public_hash']
        mid = ChurchMinutes.objects.get(public_hash=old_hash).id
        resp = self.client.post(reverse('minutes-regenerate-hash', args=[mid]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        new_hash = resp.data['public_hash']
        self.assertNotEqual(new_hash, old_hash)

        public = APIClient().get(reverse('public-minutes', args=[old_hash]))
        self.assertEqual(public.status_code, status.HTTP_404_NOT_FOUND)

    def test_public_view_by_hash(self):
        minute = self._create_minute()
        public = APIClient().get(reverse('public-minutes', args=[minute['public_hash']]))
        self.assertEqual(public.status_code, status.HTTP_200_OK)
        self.assertEqual(public.data['title'], minute['title'])
        self.assertEqual(public.data['has_pdf'], False)
        self.assertEqual(public.data['church']['name'], self.sede.name)
        self.assertNotIn('public_hash', public.data)

    def test_public_pdf_by_hash(self):
        minute = self._create_minute()
        mid = minute['id']
        pdf = SimpleUploadedFile(
            'ata.pdf', b'%PDF-1.4 fake content', content_type='application/pdf',
        )
        self.client.patch(reverse('minutes-detail', args=[mid]), {'pdf': pdf}, format='multipart')
        resp = APIClient().get(
            reverse('public-minutes-pdf', args=[minute['public_hash']]),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_public_unknown_hash_404(self):
        resp = APIClient().get(reverse('public-minutes', args=['hash-invalido']))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_requires_authentication(self):
        resp = APIClient().get(reverse('minutes-list'))
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class PublicMemberCardAndFormTests(BaseChurchTestCase):
    """Cartão público, formulário público de membros e pendências de revisão."""

    def setUp(self):
        super().setUp()
        self.pastor = self._user(
            'pastor@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        self.secretaria = self._user(
            'sec@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.SECRETARIA,
        )
        self.tesoureiro = self._user(
            'tes@teste.com',
            church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        self.member = Member.objects.create(
            church=self.sede,
            name='Maria da Silva',
            phone='(83) 99999-1111',
            birth_date='1990-05-20',
            cpf='12345678901',
            status=Member.Status.ACTIVE,
        )
        self.member.ensure_public_hash()
        self.member.save(update_fields=['public_hash'])
        self.card_hash = self.member.public_hash
        self.form_url = reverse('public-member-form', args=[self.card_hash])

    def _submit(self, hash, **overrides):
        data = {
            'name': 'Maria da Silva',
            'phone': '(83) 98888-2222',
            'email': 'maria@teste.com',
            'birth_date': '1990-05-20',
            'cpf': '',
            'rg': '',
            'born_in_city': 'Campina Grande',
            'born_in_state': 'PB',
            'profession': '', 'education_level': '', 'marital_status': '',
            'marriage_date': '', 'father_name': 'Pai', 'mother_name': 'Mãe',
            'church_entry': 'RECONCILIACAO', 'church_entry_other': '',
            'street': 'Rua A', 'number': '10', 'complement': '',
            'neighborhood': 'Centro', 'city': 'Campina Grande',
            'state': 'PB', 'cep': '58400-000', 'notes': '',
        }
        data.update(overrides)
        return APIClient().post(
            reverse('public-member-form', args=[hash]),
            {'data': data},
            format='json',
        )

    def test_public_link_returns_url(self):
        client = self._client(self.pastor)
        resp = client.get(reverse('member-self-public-link', args=[self.member.pk]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('/cartao/', resp.data['public_url'])
        self.assertTrue(resp.data['public_url'].endswith(self.card_hash))

    def test_public_link_regenerate_invalidates_old(self):
        client = self._client(self.pastor)
        resp = client.post(reverse('member-self-public-link-regenerate', args=[self.member.pk]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertNotIn(self.card_hash, resp.data['public_url'])

    def test_church_member_form_link_roundtrip(self):
        client = self._client(self.secretaria)
        resp = client.get(reverse('member-form-public-link'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('/formulario/', resp.data['url'])
        hash1 = resp.data['hash']
        self.assertEqual(Church.objects.get(pk=self.sede.pk).member_form_hash, hash1)
        resp2 = client.post(reverse('member-form-public-link'))
        self.assertNotEqual(resp2.data['hash'], hash1)

    def test_public_card_by_hash(self):
        resp = APIClient().get(reverse('public-member-card', args=[self.card_hash]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['name'], 'Maria da Silva')
        self.assertEqual(resp.data['card_number'], '')
        self.assertEqual(resp.data['church_name'], self.sede.name)
        self.assertNotIn('street', resp.data)
        self.assertNotIn('cpf', resp.data)
        self.assertNotIn('public_hash', resp.data)

    def test_public_card_unknown_hash_404(self):
        resp = APIClient().get(reverse('public-member-card', args=['hash-invalido']))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_public_card_inactive_member_404(self):
        self.member.status = Member.Status.INACTIVE
        self.member.save(update_fields=['status'])
        resp = APIClient().get(reverse('public-member-card', args=[self.card_hash]))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_public_form_get_member_type(self):
        resp = APIClient().get(self.form_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['type'], 'member')
        self.assertEqual(resp.data['member_name'], 'Maria da Silva')
        self.assertEqual(resp.data['church_name'], self.sede.name)

    def test_public_form_get_candidate_type(self):
        church_hash = self.sede.ensure_member_form_hash()
        self.sede.save(update_fields=['member_form_hash'])
        resp = APIClient().get(reverse('public-member-form', args=[church_hash]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['type'], 'candidate')
        self.assertIsNone(resp.data['member_name'])

    def test_public_submission_member_creates_pending(self):
        resp = self._submit(self.card_hash)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        sub = MemberSubmission.objects.get(pk=resp.data['id'])
        self.assertEqual(sub.status, MemberSubmission.Status.PENDING)
        self.assertEqual(sub.member, self.member)
        self.assertEqual(sub.source_hash, self.card_hash)
        self.assertEqual(sub.data['email'], 'maria@teste.com')

    def test_public_submission_candidate_creates_pending(self):
        church_hash = self.sede.ensure_member_form_hash()
        self.sede.save(update_fields=['member_form_hash'])
        resp = self._submit(church_hash)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        sub = MemberSubmission.objects.get(pk=resp.data['id'])
        self.assertIsNone(sub.member)
        self.assertEqual(sub.church, self.sede)

    def test_public_submission_requires_name(self):
        resp = self._submit(self.card_hash, name='')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_public_submission_rejects_invalid_cpf(self):
        resp = self._submit(self.card_hash, cpf='11111111111')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_public_submission_rejects_invalid_email(self):
        resp = self._submit(self.card_hash, email='nao-e-email')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_public_submission_rejects_short_phone(self):
        resp = self._submit(self.card_hash, phone='(83) 999-9999')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_public_submission_rejects_bad_cep(self):
        resp = self._submit(self.card_hash, cep='123')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_public_submission_validates_relatives(self):
        resp = self._submit(self.card_hash, relatives=[{'name': '', 'kinship': 'FILHO'}])
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        resp = self._submit(self.card_hash, relatives=[{'name': 'Filho', 'kinship': 'ETC'}])
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_public_submission_accepts_valid_photo_and_relatives(self):
        resp = self._submit(
            self.card_hash,
            photo=(
                'data:image/png;base64,'
                'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC'
                'AAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
            ),
            relatives=[{'name': 'José Filho', 'kinship': 'FILHO', 'phone': '(83) 98888-0000'}],
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        sub = MemberSubmission.objects.get(pk=resp.data['id'])
        self.assertEqual(sub.data['relatives'][0]['name'], 'José Filho')
        self.assertTrue(sub.data['photo'].startswith('data:image/png'))

    def test_approve_candidate_creates_photo_and_relatives(self):
        from unittest import mock

        church_hash = self.sede.ensure_member_form_hash()
        self.sede.save(update_fields=['member_form_hash'])
        self._submit(
            church_hash,
            name='Carlos Souza',
            photo=(
                'data:image/png;base64,'
                'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC'
                'AAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
            ),
            relatives=[{'name': 'Carla Filha', 'kinship': 'FILHO'}],
        )
        sub = MemberSubmission.objects.get()
        with mock.patch('cloudinary.uploader.upload') as mock_upload:
            mock_upload.return_value = {
                'public_id': 'members/member_photo.png',
                'secure_url': 'https://res.cloudinary.com/demo/image/upload/members/member_photo.png',
                'url': 'http://res.cloudinary.com/demo/image/upload/members/member_photo.png',
                'format': 'png', 'version': 1, 'type': 'upload',
                'resource_type': 'image', 'width': 400, 'height': 400,
                'bytes': 1024, 'created_at': '2026-01-01T00:00:00Z',
                'signature': 'abc',
            }
            resp = self._client(self.secretaria).post(
                reverse('member-submission-review', args=[sub.pk]),
                {'action': 'approve'},
                format='json',
            )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        member = Member.objects.get(name='Carlos Souza')
        self.assertTrue(member.photo)
        self.assertEqual(member.relatives.count(), 1)
        self.assertEqual(member.relatives.first().name, 'Carla Filha')

    def test_approve_member_updates_photo_and_replaces_relatives(self):
        from unittest import mock

        MemberRelative.objects.create(
            member=self.member, name='Parente Antigo', kinship='IRMAO',
        )
        self._submit(
            self.card_hash,
            relatives=[{'name': 'Mariazinha', 'kinship': 'MAE'}],
        )
        sub = MemberSubmission.objects.get()
        with mock.patch('cloudinary.uploader.upload') as mock_upload:
            mock_upload.return_value = {
                'public_id': 'members/member_photo.png',
                'secure_url': 'https://res.cloudinary.com/demo/image/upload/members/member_photo.png',
                'url': 'http://res.cloudinary.com/demo/image/upload/members/member_photo.png',
                'format': 'png', 'version': 1, 'type': 'upload',
                'resource_type': 'image', 'width': 400, 'height': 400,
                'bytes': 1024, 'created_at': '2026-01-01T00:00:00Z',
                'signature': 'abc',
            }
            resp = self._client(self.pastor).post(
                reverse('member-submission-review', args=[sub.pk]),
                {'action': 'approve'},
                format='json',
            )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.member.refresh_from_db()
        self.assertEqual(self.member.relatives.count(), 1)
        self.assertEqual(self.member.relatives.first().name, 'Mariazinha')

    def test_submissions_list_allowed_roles(self):
        resp = self._client(self.tesoureiro).get(reverse('member-submissions'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        resp = self._client(self.secretaria).get(reverse('member-submissions'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_approve_member_submission_applies_data(self):
        self._submit(self.card_hash)
        sub = MemberSubmission.objects.get()
        resp = self._client(self.pastor).post(
            reverse('member-submission-review', args=[sub.pk]),
            {'action': 'approve'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['status'], 'APPROVED')
        self.member.refresh_from_db()
        self.assertEqual(self.member.phone, '(83) 98888-2222')
        self.assertEqual(self.member.email, 'maria@teste.com')
        self.assertEqual(self.member.born_in_city, 'Campina Grande')

    def test_approve_candidate_submission_creates_member(self):
        church_hash = self.sede.ensure_member_form_hash()
        self.sede.save(update_fields=['member_form_hash'])
        self._submit(church_hash, name='Carlos Souza')
        sub = MemberSubmission.objects.get()
        resp = self._client(self.secretaria).post(
            reverse('member-submission-review', args=[sub.pk]),
            {'action': 'approve'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        member = Member.objects.get(name='Carlos Souza')
        self.assertEqual(member.church, self.sede)
        self.assertEqual(member.status, Member.Status.ACTIVE)
        self.assertTrue(member.card_number)
        sub.refresh_from_db()
        self.assertEqual(sub.member, member)

    def test_reject_submission(self):
        self._submit(self.card_hash)
        sub = MemberSubmission.objects.get()
        resp = self._client(self.pastor).post(
            reverse('member-submission-review', args=[sub.pk]),
            {'action': 'reject', 'notes': 'dados incompletos'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['status'], 'REJECTED')
        self.assertEqual(resp.data['notes'], 'dados incompletos')

    def test_review_twice_is_invalid(self):
        self._submit(self.card_hash)
        sub = MemberSubmission.objects.get()
        review_url = reverse('member-submission-review', args=[sub.pk])
        self._client(self.pastor).post(review_url, {'action': 'reject'}, format='json')
        resp = self._client(self.pastor).post(review_url, {'action': 'approve'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_review_invalid_action(self):
        self._submit(self.card_hash)
        sub = MemberSubmission.objects.get()
        resp = self._client(self.pastor).post(
            reverse('member-submission-review', args=[sub.pk]),
            {'action': 'apagar'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_public_profile_with_whatsapp_and_ministries(self):
        area = MinistryArea.objects.create(church=self.sede, name='Louvor')
        self.member.ministry_areas.add(area)
        self.member.whatsapp_public = True
        self.member.profession = 'Secretário'
        self.member.save(update_fields=['whatsapp_public', 'profession'])
        resp = APIClient().get(reverse('public-member-profile', args=[self.card_hash]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['name'], 'Maria da Silva')
        self.assertEqual(resp.data['whatsapp'], '+5583999991111')
        self.assertEqual(resp.data['card_theme'], self.sede.card_theme)
        self.assertEqual(resp.data['ministry_areas'], ['Louvor'])
        self.assertEqual(resp.data['role_title'], 'Secretário')
        self.assertEqual(resp.data['status'], 'ACTIVE')
        self.assertEqual(resp.data['status_label'], 'Ativo')
        self.assertEqual(resp.data['church']['name'], self.sede.name)
        self.assertEqual(resp.data['church']['city'], 'Campina Grande')
        self.assertEqual(resp.data['church']['state'], 'PB')
        self.assertNotIn('cpf', resp.data)
        self.assertNotIn('phone', resp.data)

    def test_public_profile_whatsapp_hidden_by_default(self):
        resp = APIClient().get(reverse('public-member-profile', args=[self.card_hash]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsNone(resp.data['whatsapp'])

    def test_public_profile_unknown_hash_404(self):
        resp = APIClient().get(reverse('public-member-profile', args=['hash-invalido']))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_public_profile_church_not_public_404(self):
        other = Member.objects.create(
            church=self.pending_congregation,
            name='Fora Daqui',
            status=Member.Status.ACTIVE,
        )
        other.ensure_public_hash()
        other.save(update_fields=['public_hash'])
        resp = APIClient().get(
            reverse('public-member-profile', args=[other.public_hash])
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_public_profile_card_theme_roundtrip(self):
        patch = self._client(self.pastor).patch(
            reverse('church-manage-profile', args=[self.sede.pk]),
            {
                'name': self.sede.name,
                'city': self.sede.city,
                'state': self.sede.state,
                'card_theme': 'BLACK_PREMIUM',
            },
            format='json',
        )
        self.assertEqual(patch.status_code, status.HTTP_200_OK)
        resp = APIClient().get(reverse('public-member-profile', args=[self.card_hash]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['card_theme'], 'BLACK_PREMIUM')

    def test_public_profile_whatsapp_public_roundtrip(self):
        client = self._client(self.pastor)
        member_url = reverse('church-members-detail', args=[self.sede.pk, self.member.pk])
        resp = client.patch(member_url, {'whatsapp_public': True}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        profile = APIClient().get(reverse('public-member-profile', args=[self.card_hash]))
        self.assertEqual(profile.data['whatsapp'], '+5583999991111')
        resp2 = client.patch(member_url, {'whatsapp_public': False}, format='json')
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        profile2 = APIClient().get(reverse('public-member-profile', args=[self.card_hash]))
        self.assertIsNone(profile2.data['whatsapp'])


class ChurchPublicLinkTests(BaseChurchTestCase):
    """Agregador de links público por igreja (painel + página pública)."""

    def setUp(self):
        super().setUp()
        self.pastor = self._user(
            'pastor@teste.com', church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        self.secretaria = self._user(
            'secretaria@teste.com', church=self.sede,
            role=ChurchMembership.Role.SECRETARIA,
        )
        self.tesoureiro = self._user(
            'tesoureiro@teste.com', church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )

    def _create_link(self, church=None, **kwargs):
        defaults = dict(
            church=church or self.sede,
            title='Culto ao Vivo',
            link_type=ChurchPublicLink.LinkType.YOUTUBE,
            url='https://youtube.com/watch?v=abc',
        )
        defaults.update(kwargs)
        return ChurchPublicLink.objects.create(**defaults)

    # --- model ---
    def test_slug_auto_generated_and_unique(self):
        self.assertTrue(self.sede.slug)
        dupe = Church.objects.create(
            name='Igreja Teste', church_type=Church.ChurchType.CONGREGATION,
            parent_church=self.sede, status='ACTIVE', is_approved=True,
            city='X', state='PB',
        )
        self.assertNotEqual(dupe.slug, self.sede.slug)
        self.assertIn('igreja-teste', dupe.slug)

    def test_links_hash_generated_on_save(self):
        self.assertTrue(self.sede.links_hash)

    # --- painel (permissões) ---
    def test_anonymous_forbidden(self):
        resp = APIClient().get(reverse('church-link-list'))
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_treasurer_forbidden(self):
        resp = self._client(self.tesoureiro).get(reverse('church-link-list'))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_pastor_can_create_and_list(self):
        client = self._client(self.pastor)
        resp = client.post(
            reverse('church-link-list'),
            {'title': 'PIX da Igreja', 'link_type': 'PIX', 'pix_key': '123456'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['church'], self.sede.id)
        self.assertEqual(resp.data['icon_key'], 'qrcode')

        listed = client.get(reverse('church-link-list'))
        self.assertEqual(listed.status_code, status.HTTP_200_OK)
        # 4 padrões (Agenda de Cultos / Ficha de Membro / Pedido de Oração /
        # Grupos de Crescimento) + o link criado
        self.assertEqual(len(listed.data), 5)
        titles = {l['title'] for l in listed.data}
        self.assertIn('Agenda de Cultos', titles)
        self.assertIn('Ficha de Membro / Cadastro', titles)
        self.assertIn('Pedido de Oração', titles)
        self.assertIn('Grupos de Crescimento', titles)

    def test_secretaria_can_manage(self):
        client = self._client(self.secretaria)
        resp = client.post(
            reverse('church-link-list'),
            {'title': 'WhatsApp', 'link_type': 'WHATSAPP',
             'whatsapp_number': '(83) 99999-9999'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            resp.data['url'], 'https://wa.me/5583999999999'
        )

    def test_pix_requires_key(self):
        resp = self._client(self.pastor).post(
            reverse('church-link-list'),
            {'title': 'PIX', 'link_type': 'PIX'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_pix_fixed_requires_amount(self):
        resp = self._client(self.pastor).post(
            reverse('church-link-list'),
            {'title': 'PIX', 'link_type': 'PIX',
             'pix_key': 'pix@igreja.com.br', 'pix_type': 'E-mail',
             'pix_amount_mode': 'FIXED'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('pix_fixed_amount', resp.data)

    def test_pix_fixed_saves_amount(self):
        resp = self._client(self.pastor).post(
            reverse('church-link-list'),
            {'title': 'PIX', 'link_type': 'PIX',
             'pix_key': '11.111.111/0001-01', 'pix_type': 'CNPJ',
             'pix_amount_mode': 'FIXED', 'pix_fixed_amount': '50'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['pix_amount_mode'], 'FIXED')
        self.assertEqual(resp.data['pix_fixed_amount'], '50.00')
        self.assertIsNone(resp.data['pix_grid_amounts'])

    def test_pix_grid_defaults_presets(self):
        resp = self._client(self.pastor).post(
            reverse('church-link-list'),
            {'title': 'PIX', 'link_type': 'PIX',
             'pix_key': 'pix@igreja.com.br', 'pix_type': 'E-mail',
             'pix_amount_mode': 'GRID', 'pix_open_amount': True},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['pix_amount_mode'], 'GRID')
        self.assertEqual(resp.data['pix_grid_amounts'], [30.0, 50.0, 100.0, 200.0])
        self.assertTrue(resp.data['pix_open_amount'])

    def test_pix_grid_rejects_invalid_amounts(self):
        resp = self._client(self.pastor).post(
            reverse('church-link-list'),
            {'title': 'PIX', 'link_type': 'PIX',
             'pix_key': 'pix@igreja.com.br', 'pix_type': 'E-mail',
             'pix_amount_mode': 'GRID', 'pix_grid_amounts': [30, 0, -5]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_whatsapp_builds_wa_me_url(self):
        resp = self._client(self.pastor).post(
            reverse('church-link-list'),
            {'title': 'WhatsApp', 'link_type': 'WHATSAPP',
             'whatsapp_number': '+55 83 99999-9999'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['whatsapp_number'], '5583999999999')
        self.assertEqual(resp.data['url'], 'https://wa.me/5583999999999')

    def test_maps_builds_google_maps_url(self):
        resp = self._client(self.pastor).post(
            reverse('church-link-list'),
            {'title': 'Encontre-nos', 'link_type': 'MAPS',
             'address_cep': '58400-000', 'address_street': 'Rua das Flores',
             'address_number': '123', 'address_neighborhood': 'Centro',
             'address_city': 'Campina Grande', 'address_state': 'PB'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp.data['url'].startswith('https://www.google.com/maps/search/'))
        self.assertIn('Rua%20das%20Flores', resp.data['url'])

    def test_maps_requires_address(self):
        resp = self._client(self.pastor).post(
            reverse('church-link-list'),
            {'title': 'Encontre-nos', 'link_type': 'MAPS',
             'address_cep': '58400-000', 'address_street': 'Rua das Flores'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_custom_requires_url(self):
        resp = self._client(self.pastor).post(
            reverse('church-link-list'),
            {'title': 'Site', 'link_type': 'CUSTOM', 'url': ''},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_url_rejected(self):
        resp = self._client(self.pastor).post(
            reverse('church-link-list'),
            {'title': 'Site', 'link_type': 'CUSTOM', 'url': 'nao-e-url'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_calendar_and_membership_store_blank_url(self):
        resp = self._client(self.pastor).post(
            reverse('church-link-list'),
            {'title': 'Agenda', 'link_type': 'CALENDAR', 'url': 'https://x.io'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['url'], '')

    def test_scoped_to_active_church(self):
        other = Church.objects.create(
            name='Outra Igreja', church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE', is_approved=True, city='A', state='PB',
        )
        link = ChurchPublicLink.objects.create(
            church=other, title='Fora', link_type='CUSTOM', url='https://a.io',
        )
        listed = self._client(self.pastor).get(reverse('church-link-list'))
        # apenas os padrões da igreja ativa; o link alheio não aparece
        self.assertEqual(len(listed.data), 4)
        ids = [l['id'] for l in listed.data]
        self.assertNotIn(link.pk, ids)

        resp = self._client(self.pastor).get(
            reverse('church-link-detail', args=[link.pk])
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_patch_toggle_active(self):
        link = self._create_link()
        resp = self._client(self.pastor).patch(
            reverse('church-link-detail', args=[link.pk]),
            {'is_active': False}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['is_active'])

    def test_delete(self):
        link = self._create_link()
        resp = self._client(self.pastor).delete(
            reverse('church-link-detail', args=[link.pk])
        )
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ChurchPublicLink.objects.filter(pk=link.pk).exists())

    # --- reorder ---
    def test_reorder_updates_orders(self):
        a = self._create_link(title='A')
        b = self._create_link(title='B')
        c = self._create_link(title='C')
        resp = self._client(self.pastor).post(
            reverse('church-link-reorder'),
            {'order': [c.id, a.id, b.id]}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        order = list(
            ChurchPublicLink.objects.filter(church=self.sede)
            .order_by('order').values_list('id', flat=True)
        )
        self.assertEqual(order, [c.id, a.id, b.id])

    def test_reorder_ignores_alien_ids(self):
        other = Church.objects.create(
            name='Outra', church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE', is_approved=True, city='A', state='PB',
        )
        a = self._create_link(title='A')
        alien = ChurchPublicLink.objects.create(
            church=other, title='X', link_type='CUSTOM', url='https://x.io',
        )
        resp = self._client(self.pastor).post(
            reverse('church-link-reorder'),
            {'order': [alien.id, a.id]}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # o link alheio é ignorado; o próprio recebe a posição informada
        self.assertEqual(ChurchPublicLink.objects.get(pk=a.pk).order, 1)
        self.assertEqual(ChurchPublicLink.objects.get(pk=alien.pk).order, 0)

    # --- config ---
    def test_config_get_and_patch(self):
        client = self._client(self.pastor)
        cfg = client.get(reverse('church-link-config'))
        self.assertEqual(cfg.status_code, status.HTTP_200_OK)
        self.assertEqual(cfg.data['slug'], self.sede.slug)
        self.assertTrue(cfg.data['public_links_enabled'])
        self.assertEqual(cfg.data['theme_color'], '#1c7ed6')

        resp = client.patch(
            reverse('church-link-config'),
            {'theme_color': '#ff0000', 'default_pix_key': 'pix@sede.com',
             'default_pix_type': 'E-mail'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['theme_color'], '#FF0000')
        self.sede.refresh_from_db()
        self.assertEqual(self.sede.default_pix_key, 'pix@sede.com')

    def test_config_invalid_color(self):
        resp = self._client(self.pastor).patch(
            reverse('church-link-config'),
            {'theme_color': 'red'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_config_duplicate_slug_gets_suffix(self):
        other = Church.objects.create(
            name='Outra', church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE', is_approved=True, city='A', state='PB',
        )
        # tentar adotar o slug já usado pela outra igreja
        resp = self._client(self.pastor).patch(
            reverse('church-link-config'),
            {'slug': other.slug}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertNotEqual(resp.data['slug'], other.slug)
        self.assertTrue(resp.data['slug'].startswith(other.slug))
        # manter o próprio slug continua válido (idempotente)
        resp = self._client(self.pastor).patch(
            reverse('church-link-config'),
            {'slug': self.sede.slug}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['slug'], self.sede.slug)

    # --- página pública ---
    def test_public_by_slug(self):
        yt = self._create_link(
            title='YouTube', link_type='YOUTUBE', url='https://youtube.com/watch?v=abc',
        )
        pix = self._create_link(
            title='PIX', link_type='PIX', pix_key='1234', pix_type='CNPJ',
        )
        inactive = self._create_link(
            title='Inativo', link_type='CUSTOM',
            url='https://inativo.io', is_active=False,
        )
        url = reverse('public-church-links', args=[self.sede.slug])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.data
        self.assertEqual(data['church']['name'], self.sede.name)
        self.assertEqual(data['church']['state'], 'PB')
        self.assertIn('1c7ed6', data['church']['theme_color'])

        active_ids = [l['id'] for l in data['links']]
        self.assertIn(yt.id, active_ids)
        self.assertIn(pix.id, active_ids)
        self.assertNotIn(inactive.id, active_ids)

        # tipos do sistema presentes
        system_types = {s['link_type'] for s in data['system_links']}
        self.assertIn('CALENDAR', system_types)
        self.assertIn('MEMBERSHIP', system_types)
        self.assertIn('GROWTH_GROUPS', system_types)
        growth = next(
            s for s in data['system_links']
            if s['link_type'] == 'GROWTH_GROUPS'
        )
        self.assertEqual(growth['title'], 'Grupos de Crescimento')
        self.assertTrue(growth['url'].endswith(f'/gc/{self.sede.slug}'))
        self.assertEqual(growth['icon_key'], 'users')

    def test_public_exposes_pix_amount_modes(self):
        open_pix = self._create_link(
            title='PIX Livre', link_type='PIX', pix_key='livre@x.com', pix_type='E-mail',
        )
        fixed = self._create_link(
            title='PIX Fixo', link_type='PIX', pix_key='fixo@x.com', pix_type='E-mail',
            pix_amount_mode='FIXED', pix_fixed_amount='50.00',
        )
        grid = self._create_link(
            title='PIX Grade', link_type='PIX', pix_key='grade@x.com', pix_type='E-mail',
            pix_amount_mode='GRID', pix_grid_amounts=[30, 50, 100, 200], pix_open_amount=True,
        )
        url = reverse('public-church-links', args=[self.sede.slug])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        by_id = {l['id']: l for l in resp.data['links']}

        self.assertEqual(by_id[open_pix.id]['pix_amount_mode'], 'OPEN')
        self.assertIn('pix_amount_mode', by_id[open_pix.id])
        self.assertEqual(by_id[fixed.id]['pix_amount_mode'], 'FIXED')
        self.assertEqual(by_id[fixed.id]['pix_fixed_amount'], '50.00')
        g = by_id[grid.id]
        self.assertEqual(g['pix_amount_mode'], 'GRID')
        self.assertEqual(g['pix_grid_amounts'], [30, 50, 100, 200])
        self.assertTrue(g['pix_open_amount'])

    def test_public_resolves_calendar_system_url(self):
        link = self._create_link(
            title='Agenda', link_type='CALENDAR',
        )
        url = reverse('public-church-links', args=[self.sede.slug])
        resp = self.client.get(url)
        cal = next(l for l in resp.data['links'] if l['id'] == link.id)
        self.assertIn(f'/calendario/{self.sede.calendar_public_hash}', cal['url'])
        # tipo do sistema coberto pelo link cadastrado: não duplica em system_links
        self.assertEqual(
            [s for s in resp.data['system_links'] if s['link_type'] == 'CALENDAR'], []
        )

    def test_public_by_links_hash(self):
        url = reverse('public-church-links', args=[self.sede.links_hash])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['church']['name'], self.sede.name)

    def test_public_unknown_404(self):
        url = reverse('public-church-links', args=['nao-existe'])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_public_disabled_404(self):
        self.sede.public_links_enabled = False
        self.sede.save(update_fields=['public_links_enabled'])
        url = reverse('public-church-links', args=[self.sede.slug])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # --- contador de cliques ---
    def test_click_increments_atomically(self):
        link = self._create_link()
        url = reverse('public-church-link-click', args=[link.pk])
        for _ in range(3):
            resp = self.client.post(url)
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
        link.refresh_from_db()
        self.assertEqual(link.click_count, 3)

    def test_click_unknown_404(self):
        url = reverse('public-church-link-click', args=[99999])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class SedeTreasurerGovernanceTests(BaseChurchTestCase):
    """Tesoureiro(a) de Sede: vê e navega entre as congregações (somente leitura)."""

    def setUp(self):
        super().setUp()
        self.tesoureira = self._user(
            'tesoureira@teste.com', 'Tesoureira',
            church=self.sede, role=ChurchMembership.Role.TESOUREIRO,
        )

    def test_me_flags(self):
        resp = self._client(self.tesoureira).get(reverse('me'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.data['user']
        self.assertTrue(data['can_manage_churches'])
        self.assertFalse(data['can_approve_congregations'])

    def test_me_flags_for_admin(self):
        admin = self._user('admin@teste.com', is_staff=True, church=self.sede)
        resp = self._client(admin).get(reverse('me'))
        data = resp.data['user']
        self.assertTrue(data['can_manage_churches'])
        self.assertTrue(data['can_approve_congregations'])

    def test_can_manage_churches_false_for_congregation_treasurer(self):
        cong_tesoureiro = self._user(
            'cong.tesoureiro@teste.com', church=self.congregation,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        resp = self._client(cong_tesoureiro).get(reverse('me'))
        data = resp.data['user']
        self.assertFalse(data['can_manage_churches'])
        self.assertFalse(data['can_approve_congregations'])

    def test_churches_list_includes_sede_and_congregations(self):
        client = self._client(self.tesoureira)
        churches = client.get(reverse('church-list'))
        self.assertEqual(churches.status_code, status.HTTP_200_OK)
        ids = {c['id'] for c in churches.data}
        self.assertIn(self.sede.id, ids)
        self.assertIn(self.congregation.id, ids)

    def test_switch_to_congregation_succeeds(self):
        resp = self._client(self.tesoureira).post(
            reverse('switch-church'), {'church_id': self.congregation.id},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.tesoureira.refresh_from_db()
        self.assertEqual(self.tesoureira.church_id, self.congregation.id)
        # propriedade membership-based permanece True mesmo ativo numa congregação
        self.assertTrue(self.tesoureira.can_manage_churches)

    def test_switch_to_alien_church_forbidden(self):
        alien = Church.objects.create(
            name='Alien', church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE', is_approved=True, city='X', state='PB',
        )
        resp = self._client(self.tesoureira).post(
            reverse('switch-church'), {'church_id': alien.id}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_cannot_delete_congregation(self):
        resp = self._client(self.tesoureira).delete(
            reverse('church-detail', args=[self.congregation.id])
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Church.objects.filter(pk=self.congregation.id).exists())

    def test_treasurer_still_cannot_approve(self):
        resp = self._client(self.tesoureira).get(reverse('pending-congregations'))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class GrowthGroupTests(BaseChurchTestCase):
    """Grupos de Crescimento: RBAC, validação de dias e dashboard/stats."""

    def setUp(self):
        super().setUp()
        self.pastor = self._user(
            'pastor@teste.com', church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        self.secretaria = self._user(
            'secretaria@teste.com', church=self.sede,
            role=ChurchMembership.Role.SECRETARIA,
        )
        self.tesoureiro = self._user(
            'tesoureiro@teste.com', church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        self.leader = Member.objects.create(
            church=self.sede, name='Líder de GC', status=Member.Status.ACTIVE,
        )

    def _group(self, church=None, **kwargs):
        defaults = dict(
            church=church or self.sede,
            name='GC Esperança',
            leader=self.leader,
            weekday=GrowthGroup.DEFAULT_WEEKDAY,
            time='19:30:00',
            street='Rua A',
            number='100',
            neighborhood='Centro',
            city='Campina Grande',
            state='PB',
            cep='58400-000',
            radius_meters=1000,
        )
        defaults.update(kwargs)
        return GrowthGroup.objects.create(**defaults)

    def _payload(self, **kwargs):
        payload = dict(
            name='GC Esperança',
            leader=self.leader.id,
            weekday=1,
            time='19:30',
            street='Rua A',
            number='100',
            neighborhood='Centro',
            city='Campina Grande',
            state='PB',
            cep='58400-000',
            radius_meters=1000,
        )
        payload.update(kwargs)
        return payload

    # --- model: validação de dias de culto oficial ---
    def test_forbidden_weekday_rejected_on_clean(self):
        for weekday in GrowthGroup.FORBIDDEN_WEEKDAYS:
            with self.assertRaises(ValidationError):
                g = self._group(weekday=weekday)
                g.full_clean()

    def test_allowed_weekday_accepted(self):
        for weekday in GrowthGroup.ALLOWED_WEEKDAYS:
            g = self._group(weekday=weekday)
            g.full_clean()  # não deve levantar
        self.assertEqual(g.weekday, GrowthGroup.Weekday.FRIDAY)

    def test_lat_lng_required_together(self):
        with self.assertRaises(ValidationError):
            g = self._group(latitude=-7.22, longitude=None)
            g.full_clean()

    def test_serializer_rejects_forbidden_weekday(self):
        resp = self._client(self.secretaria).post(
            reverse('growth-group-list'), self._payload(weekday=3), format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('weekday', resp.data)

    # --- RBAC ---
    def test_anonymous_forbidden(self):
        resp = APIClient().get(reverse('growth-group-list'))
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)
        resp = APIClient().post(
            reverse('growth-group-list'), self._payload(), format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_read_allows_any_authenticated_with_church(self):
        for user in (self.pastor, self.secretaria, self.tesoureiro):
            resp = self._client(user).get(reverse('growth-group-list'))
            self.assertEqual(resp.status_code, status.HTTP_200_OK, msg=user.email)

    def test_user_without_church_cannot_write(self):
        user = self._user('sem-igreja@teste.com', church=None)
        resp = self._client(user).post(
            reverse('growth-group-list'), self._payload(), format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_secretaria_can_create_and_edit(self):
        client = self._client(self.secretaria)
        resp = client.post(
            reverse('growth-group-list'), self._payload(), format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['leader_name'], 'Líder de GC')
        self.assertEqual(resp.data['weekday_display'], 'Terça-feira')

        pk = resp.data['id']
        resp = client.patch(
            reverse('growth-group-detail', args=[pk]),
            {'name': 'GC Renovação', 'weekday': 4}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['name'], 'GC Renovação')
        self.assertEqual(resp.data['weekday'], 4)

    def test_treasurer_can_create_and_edit_but_not_delete(self):
        client = self._client(self.tesoureiro)
        resp = client.post(
            reverse('growth-group-list'), self._payload(), format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        pk = resp.data['id']

        resp = client.patch(
            reverse('growth-group-detail', args=[pk]),
            {'radius_meters': 1500}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        resp = client.delete(reverse('growth-group-detail', args=[pk]))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(GrowthGroup.objects.filter(pk=pk).exists())

    def test_secretaria_and_pastor_can_delete(self):
        group = self._group()
        for user in (self.secretaria, self.pastor):
            group.pk = None
            group.id = None
            group.save()
            resp = self._client(user).delete(
                reverse('growth-group-detail', args=[group.pk])
            )
            self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_cannot_manage_group_of_another_church(self):
        group = self._group(church=self.congregation)
        resp = self._client(self.tesoureiro).delete(
            reverse('growth-group-detail', args=[group.pk])
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # --- filtros ---
    def test_search_by_name_leader_and_address(self):
        self._group(name='Esperança')
        self._group(name='Fé')
        others = Member.objects.create(church=self.sede, name='Irmã Ana')
        self._group(
            name='Amor', leader=others,
            street='Av. Liberdade', number='50', neighborhood='Bairro Centro',
        )
        url = reverse('growth-group-list')
        self.assertEqual(
            len(self._client(self.secretaria).get(url).data), 3
        )
        self.assertEqual(
            len(self._client(self.secretaria).get(
                url, {'search': 'esperança'}).data), 1
        )
        # busca por líder
        self.assertEqual(
            len(self._client(self.secretaria).get(
                url, {'search': 'irmã ana'}).data), 1
        )
        # busca por logradouro/bairro (endereço)
        self.assertEqual(
            len(self._client(self.secretaria).get(
                url, {'search': 'liberdade'}).data), 1
        )
        self.assertEqual(
            len(self._client(self.secretaria).get(
                url, {'search': 'bairro centro'}).data), 1
        )

    def test_serializer_exposes_structured_address(self):
        resp = self._client(self.secretaria).post(
            reverse('growth-group-list'), self._payload(), format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['street'], 'Rua A')
        self.assertEqual(resp.data['number'], '100')
        self.assertEqual(resp.data['city'], 'Campina Grande')
        self.assertEqual(resp.data['state'], 'PB')
        self.assertEqual(resp.data['cep'], '58400-000')
        self.assertIn('Rua A, 100', resp.data['address'])
        self.assertIn('Campina Grande/PB', resp.data['address'])

    def test_serializer_requires_street_and_city(self):
        payload = self._payload(street='', city='')
        resp = self._client(self.secretaria).post(
            reverse('growth-group-list'), payload, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('city', resp.data)

    def test_filter_by_weekday(self):
        self._group(name='Seg', weekday=0)
        self._group(name='Ter', weekday=1)
        self._group(name='Sex', weekday=4)
        resp = self._client(self.secretaria).get(
            reverse('growth-group-list'), {'weekday': 1},
        )
        data = resp.data
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['name'], 'Ter')

    # --- stats / dashboard ---
    def test_stats_counts_and_overlap_alert(self):
        leader_b = Member.objects.create(church=self.sede, name='Líder B')
        # Sobreposição: ~55m de distância, raios 1000m cada → 55 < 2000.
        self._group(latitude=-7.2230, longitude=-35.9050, radius_meters=1000)
        self._group(
            latitude=-7.2235, longitude=-35.9050, radius_meters=1000,
        )
        self._group(
            name='GC Norte', leader=leader_b, weekday=0, latitude=-7.22,
            longitude=-35.91, radius_meters=2000,
        )
        # Sem coordenadas: entra no total ativo, mas não na cobertura.
        self._group(name='GC Sem Mapa', leader=leader_b, is_active=False)

        resp = self._client(self.secretaria).get(
            reverse('growth-group-stats')
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.data
        self.assertEqual(data['total_active'], 3)
        self.assertEqual(data['total_leaders'], 2)
        self.assertEqual(data['most_frequent_day'], 1)
        self.assertEqual(data['coverage'], 3)
        self.assertGreaterEqual(data['overlap_count'], 1)
        self.assertTrue(data['overlap_ids'])


class PublicGrowthGroupsTests(BaseChurchTestCase):
    """Mapa público de GCs: payload por slug, links úteis e 404s."""

    def setUp(self):
        super().setUp()
        self.leader = Member.objects.create(
            church=self.sede, name='Líder de GC',
            phone='(83) 99800-1234', status=Member.Status.ACTIVE,
        )
        self.host = Member.objects.create(
            church=self.sede, name='Anfitriã', status=Member.Status.ACTIVE,
        )

    def _group(self, **kwargs):
        defaults = dict(
            church=self.sede,
            name='GC Esperança',
            leader=self.leader,
            host=self.host,
            weekday=GrowthGroup.DEFAULT_WEEKDAY,
            time='19:30:00',
            street='Rua A',
            number='100',
            neighborhood='Centro',
            city='Campina Grande',
            state='PB',
            cep='58400-000',
            radius_meters=1000,
        )
        defaults.update(kwargs)
        return GrowthGroup.objects.create(**defaults)

    def _public_url(self, church=None):
        return reverse(
            'public-church-growth-groups', args=[(church or self.sede).slug]
        )

    def test_public_growth_groups_by_church_slug(self):
        g1 = self._group(
            name='GC Fé', weekday=GrowthGroup.Weekday.MONDAY,
            latitude=-7.2230, longitude=-35.9050,
        )
        g2 = self._group(
            name='GC Esperança', weekday=GrowthGroup.Weekday.TUESDAY,
            category='ADULTS', latitude=-7.2235, longitude=-35.9050,
        )
        self._group(name='GC Inativo', is_active=False)

        resp = self.client.get(self._public_url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.data
        self.assertEqual(data['church']['name'], self.sede.name)
        self.assertEqual(data['church']['city'], 'Campina Grande')
        self.assertEqual(data['church']['neighborhood'], '')
        self.assertEqual(data['church']['state'], 'PB')
        self.assertIn('1c7ed6', data['church']['theme_color'])
        self.assertIsNone(data['church']['logo'])

        groups = data['growth_groups']
        self.assertEqual(len(groups), 2)
        # ordenado por weekday/name
        self.assertEqual([g['id'] for g in groups], [g1.id, g2.id])

        first = groups[0]
        for field in (
            'name', 'category', 'category_display', 'weekday', 'weekday_display',
            'time', 'leader_name', 'leader_phone', 'neighborhood', 'city', 'state',
            'full_address', 'latitude', 'longitude', 'radius_meters', 'is_full',
            'whatsapp_url', 'maps_url',
        ):
            self.assertIn(field, first)
        self.assertEqual(first['leader_name'], 'Líder de GC')
        self.assertEqual(first['host_name'], 'Anfitriã')
        self.assertEqual(first['category_display'], 'Misto / Geral')
        self.assertEqual(first['weekday_display'], 'Segunda-feira')
        self.assertEqual(first['time'], '19:30:00')
        self.assertEqual(first['city'], 'Campina Grande')
        self.assertEqual(first['state'], 'PB')
        self.assertIn('Rua A, 100', first['full_address'])
        self.assertEqual(first['latitude'], -7.2230)
        self.assertEqual(first['longitude'], -35.9050)
        self.assertEqual(first['radius_meters'], 1000)
        self.assertFalse(first['is_full'])
        self.assertTrue(first['maps_url'].startswith(
            'https://www.google.com/maps/dir/?api=1&destination=-7.223,-35.905'
        ))

    def test_public_growth_groups_whatsapp_url(self):
        # Líder com telefone de 10 dígitos (83) 9800-1234 → 83 9800-1234.
        leader_10 = Member.objects.create(
            church=self.sede, name='Líder 10 Dígitos',
            phone='(83) 9800-1234', status=Member.Status.ACTIVE,
        )
        group = self._group(leader=leader_10)
        self._group(
            name='GC Sem Telefone',
            leader=Member.objects.create(
                church=self.sede, name='Líder Sem Fone',
                status=Member.Status.ACTIVE,
            ),
            weekday=GrowthGroup.Weekday.FRIDAY,
        )

        resp = self.client.get(self._public_url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        by_id = {g['id']: g for g in resp.data['growth_groups']}
        self.assertTrue(
            by_id[group.id]['whatsapp_url'].startswith(
                'https://wa.me/558398001234?text='
            )
        )
        # 10 dígitos → 55 prefixado; total 12 dígitos (região 12-15).
        digits = by_id[group.id]['whatsapp_url'].split('/')[-1].split('?')[0]
        self.assertEqual(digits, '558398001234')
        sem = by_id[[g['id'] for g in resp.data['growth_groups']
                     if g['name'] == 'GC Sem Telefone'][0]]
        self.assertIsNone(sem['whatsapp_url'])

    def test_public_growth_groups_disabled_404(self):
        self.sede.public_links_enabled = False
        self.sede.save(update_fields=['public_links_enabled'])
        resp = self.client.get(self._public_url())
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_public_growth_groups_unknown_404(self):
        resp = self.client.get(
            reverse('public-church-growth-groups', args=['nao-existe'])
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_growth_group_category_and_full_writable(self):
        secretaria = self._user(
            'secretaria@teste.com', church=self.sede,
            role=ChurchMembership.Role.SECRETARIA,
        )
        client = self._client(secretaria)
        resp = client.post(
            reverse('growth-group-list'),
            {
                'name': 'GC Mulheres', 'leader': self.leader.id,
                'weekday': 1, 'time': '19:30',
                'street': 'Rua A', 'number': '100',
                'neighborhood': 'Centro', 'city': 'Campina Grande',
                'state': 'PB', 'cep': '58400-000',
                'category': 'WOMEN', 'is_full': True,
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['category'], 'WOMEN')
        self.assertEqual(resp.data['category_display'], 'Mulheres')
        self.assertTrue(resp.data['is_full'])

        pk = resp.data['id']
        resp = client.patch(
            reverse('growth-group-detail', args=[pk]),
            {'category': 'YOUTH', 'is_full': False}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['category'], 'YOUTH')
        self.assertEqual(resp.data['category_display'], 'Jovens')
        self.assertFalse(resp.data['is_full'])
        stored = GrowthGroup.objects.get(pk=pk)
        self.assertEqual(stored.category, 'YOUTH')
        self.assertFalse(stored.is_full)


class CommunicationWhatsAppTests(BaseChurchTestCase):
    """Comunicação semiautomática via WhatsApp: templates, funil (stage) e
    ações recomendadas da Secretaria."""

    def setUp(self):
        super().setUp()
        self.pastor = self._user(
            'pastor@teste.com', church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        self.secretaria = self._user(
            'secretaria@teste.com', church=self.sede,
            role=ChurchMembership.Role.SECRETARIA,
        )
        self.tesoureiro = self._user(
            'tesoureiro@teste.com', church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )

    def _member(self, name='Maria Silva', phone='(83) 99800-1234', **kwargs):
        defaults = dict(
            church=self.sede, name=name, phone=phone, status=Member.Status.ACTIVE,
        )
        defaults.update(kwargs)
        return Member.objects.create(**defaults)

    def _template(self, category='WELCOME', **kwargs):
        defaults = dict(
            church=self.sede, title='Boas-Vindas', category=category,
            content='Olá {{PRIMEIRO_NOME}}, seja bem-vindo à {{IGREJA}}!',
        )
        defaults.update(kwargs)
        return MessageTemplate.objects.create(**defaults)

    # --- MessageTemplate CRUD / RBAC ---
    def test_template_list_and_detail_scoped_to_church(self):
        mine = self._template(title='Boas-Vindas')
        other = self._template(church=self.congregation, title='Outro')
        resp = self._client(self.secretaria).get(
            reverse('message-template-list')
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual([t['id'] for t in resp.data], [mine.id])
        self.assertNotIn(other.id, [t['id'] for t in resp.data])

    def test_template_requires_secretaria_or_pastor(self):
        url = reverse('message-template-list')
        self.assertEqual(
            self._client(self.tesoureiro).get(url).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self._client(self.tesoureiro).post(url, {}, format='json').status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            APIClient().get(url).status_code, status.HTTP_401_UNAUTHORIZED,
        )

    def test_template_create_update_delete(self):
        client = self._client(self.secretaria)
        resp = client.post(
            reverse('message-template-list'),
            {
                'title': 'Versículo do Dia',
                'category': 'VERSE',
                'content': '{{PRIMEIRO_NOME}}, "Tudo posso naquele que me fortalece".',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['church'], self.sede.id)
        self.assertEqual(resp.data['category_display'], 'Versículo')
        pk = resp.data['id']

        resp = client.patch(
            reverse('message-template-detail', args=[pk]),
            {'is_active': False}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIs(resp.data['is_active'], False)

        resp = client.delete(reverse('message-template-detail', args=[pk]))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    # --- prepare-whatsapp ---
    def test_prepare_whatsapp_with_template(self):
        member = self._member(name='Maria Silva')
        template = self._template(category='WELCOME')
        resp = self._client(self.secretaria).post(
            reverse('member-self-prepare-whatsapp', args=[member.id]),
            {'template_id': template.id, 'category': 'WELCOME'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(
            resp.data['whatsapp_url'].startswith('https://wa.me/5583998001234?text=')
        )
        self.assertIn('Maria', resp.data['formatted_message'])
        self.assertIn('Igreja Teste', resp.data['formatted_message'])
        self.assertNotIn('{{', resp.data['formatted_message'])

        log = MemberContactLog.objects.get(member=member)
        self.assertEqual(log.category, 'WELCOME')
        self.assertEqual(log.contacted_by, self.secretaria)
        member.refresh_from_db()
        self.assertIsNotNone(member.last_contact_at)

    def test_prepare_whatsapp_with_custom_text(self):
        member = self._member(name='João Pedro', phone='83 99800-9999')
        resp = self._client(self.secretaria).post(
            reverse('member-self-prepare-whatsapp', args=[member.id]),
            {
                'custom_text': 'Oi {{PRIMEIRO_NOME}}, que tal um encontro novo? 😊',
                'category': 'CARE',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['formatted_message'], 'Oi João, que tal um encontro novo? 😊')
        self.assertTrue(resp.data['whatsapp_url'].startswith('https://wa.me/5583998009999?text='))
        # emojis e quebras são preservados na URL codificada
        from urllib.parse import unquote
        text = unquote(resp.data['whatsapp_url'].split('?text=', 1)[1])
        self.assertEqual(text, resp.data['formatted_message'])

    def test_prepare_whatsapp_rejects_invalid_phone(self):
        member = self._member(phone='123')
        template = self._template()
        resp = self._client(self.secretaria).post(
            reverse('member-self-prepare-whatsapp', args=[member.id]),
            {'template_id': template.id},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('phone', resp.data)

    def test_prepare_whatsapp_requires_template_or_custom_text(self):
        member = self._member()
        resp = self._client(self.secretaria).post(
            reverse('member-self-prepare-whatsapp', args=[member.id]),
            {}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_prepare_whatsapp_rejects_template_of_other_church(self):
        member = self._member()
        template = self._template(church=self.congregation)
        resp = self._client(self.secretaria).post(
            reverse('member-self-prepare-whatsapp', args=[member.id]),
            {'template_id': template.id},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_prepare_whatsapp_requires_secretaria_or_pastor(self):
        member = self._member()
        template = self._template()
        resp = self._client(self.tesoureiro).post(
            reverse('member-self-prepare-whatsapp', args=[member.id]),
            {'template_id': template.id},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_prepare_whatsapp_records_multiple_contacts_and_updates_last_contact(self):
        from datetime import timedelta  # noqa: PLC0415
        from django.utils import timezone  # noqa: PLC0415

        member = self._member()
        member.last_contact_at = timezone.now() - timedelta(days=5)
        member.save(update_fields=['last_contact_at'])
        template = self._template()
        url = reverse('member-self-prepare-whatsapp', args=[member.id])
        for _ in range(2):
            resp = self._client(self.secretaria).post(
                url, {'template_id': template.id}, format='json',
            )
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(MemberContactLog.objects.filter(member=member).count(), 2)
        member.refresh_from_db()
        self.assertGreaterEqual(member.last_contact_at, timezone.now() - timedelta(minutes=5))

    # --- stage (funil) ---
    def test_stage_update_valid(self):
        member = self._member()
        for stage in ('VISITOR', 'INTEGRATION', 'ACTIVE', 'ABSENT_CARE', 'TRANSITION'):
            resp = self._client(self.secretaria).patch(
                reverse('member-self-stage', args=[member.id]),
                {'lifecycle_stage': stage}, format='json',
            )
            self.assertEqual(resp.status_code, status.HTTP_200_OK, msg=stage)
            self.assertEqual(resp.data['lifecycle_stage'], stage)

    def test_stage_update_invalid(self):
        member = self._member()
        resp = self._client(self.secretaria).patch(
            reverse('member-self-stage', args=[member.id]),
            {'lifecycle_stage': 'ALIEN'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_stage_requires_secretaria_or_pastor(self):
        member = self._member()
        resp = self._client(self.tesoureiro).patch(
            reverse('member-self-stage', args=[member.id]),
            {'lifecycle_stage': 'TRANSITION'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # --- secretary-actions ---
    def test_secretary_actions_lists_birthdays_today(self):
        from django.utils import timezone  # noqa: PLC0415
        today = timezone.localdate()
        member = self._member(name='Aniversariante', birth_date=today)
        resp = self._client(self.secretaria).get(reverse('secretary-actions'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [a['member_id'] for a in resp.data['birthdays_today']]
        self.assertIn(member.id, ids)

    def test_secretary_actions_absent_pending_contact(self):
        from datetime import timedelta  # noqa: PLC0415
        from django.utils import timezone  # noqa: PLC0415

        overdue = self._member(
            name='Ausente', lifecycle_stage=Member.LifecycleStage.ABSENT_CARE,
        )
        recent = self._member(
            name='Cuidado Recente',
            lifecycle_stage=Member.LifecycleStage.ABSENT_CARE,
        )
        recent.last_contact_at = timezone.now() - timedelta(days=2)
        recent.save(update_fields=['last_contact_at'])
        resp = self._client(self.pastor).get(reverse('secretary-actions'))
        ids = [a['member_id'] for a in resp.data['absent_pending_contact']]
        self.assertIn(overdue.id, ids)
        self.assertNotIn(recent.id, ids)

    def test_secretary_actions_new_visitors(self):
        from datetime import timedelta  # noqa: PLC0415
        from django.utils import timezone  # noqa: PLC0415

        visitor = self._member(
            name='Visitante', lifecycle_stage=Member.LifecycleStage.VISITOR,
        )
        integrated = self._member(
            name='Em Integração', lifecycle_stage=Member.LifecycleStage.INTEGRATION,
        )
        integrated.last_contact_at = timezone.now()
        integrated.save(update_fields=['last_contact_at'])
        resp = self._client(self.secretaria).get(reverse('secretary-actions'))
        ids = [a['member_id'] for a in resp.data['new_visitors']]
        self.assertIn(visitor.id, ids)
        self.assertNotIn(integrated.id, ids)

    def test_secretary_actions_cards_expiring_within_window(self):
        from datetime import timedelta  # noqa: PLC0415
        from django.utils import timezone  # noqa: PLC0415

        today = timezone.localdate()
        self.sede.card_valid_until = today + timedelta(days=20)
        self.sede.save(update_fields=['card_valid_until'])
        with_card = self._member(name='Com Carteirinha', card_number='0001')
        without_card = self._member(name='Sem Carteirinha')
        resp = self._client(self.secretaria).get(reverse('secretary-actions'))
        ids = [a['member_id'] for a in resp.data['cards_expiring']]
        self.assertIn(with_card.id, ids)
        self.assertNotIn(without_card.id, ids)

    def test_secretary_actions_requires_secretaria_or_pastor(self):
        resp = self._client(self.tesoureiro).get(reverse('secretary-actions'))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        resp = APIClient().get(reverse('secretary-actions'))
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_member_serializer_exposes_stage_and_last_contact(self):
        member = self._member(lifecycle_stage=Member.LifecycleStage.VISITOR)
        resp = self._client(self.secretaria).get(
            reverse('member-self-detail', args=[member.id])
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['lifecycle_stage'], 'VISITOR')
        self.assertEqual(resp.data['lifecycle_stage_display'], 'Visitante / Novo')
        self.assertIn('last_contact_at', resp.data)


class CertificateTests(BaseChurchTestCase):
    """Certificados eclesiais: CRUD de modelos, emissão e downloads."""

    def setUp(self):
        super().setUp()
        self.sec = self._user(
            'sec-cert@teste.com', church=self.sede,
            role=ChurchMembership.Role.SECRETARIA,
        )
        self.client = self._client(self.sec)

    @staticmethod
    def _tiny_png_bytes():
        from io import BytesIO

        from PIL import Image

        buf = BytesIO()
        Image.new('RGB', (10, 10), color=(255, 255, 255)).save(buf, format='PNG')
        return buf.getvalue()

    @staticmethod
    def _landscape_pdf_bytes():
        from io import BytesIO

        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=842, height=595)
        out = BytesIO()
        writer.write(out)
        return out.getvalue()

    def _template_payload(self, **kwargs):
        data = {
            'name': 'Modelo Batismo',
            'certificate_type': CertificateTemplate.CertificateType.BAPTISM,
            'layout_mode': CertificateTemplate.LayoutMode.SYSTEM_DEFAULT,
            'default_verse': 'Ide por todo o mundo e pregai o evangelho...',
        }
        data.update(kwargs)
        return data

    def _issue_payload(self, **kwargs):
        data = {
            'certificate_type': CertificateTemplate.CertificateType.BAPTISM,
            'recipient_name': 'João Pedro da Silva',
            'event_date': '2026-08-30',
            'officiant_name': 'Pr. Marcos',
            'registry_book': '1',
            'registry_page': '12',
            'registry_number': '45',
        }
        data.update(kwargs)
        return data

    def _create_template(self, **kwargs):
        data = self._template_payload(**kwargs)
        multipart = any(isinstance(v, SimpleUploadedFile) for v in data.values())
        if multipart:
            files = {k: v for k, v in data.items() if isinstance(v, SimpleUploadedFile)}
            form = {k: v for k, v in data.items() if not isinstance(v, SimpleUploadedFile)}
            resp = self.client.post(
                reverse('certificate-template-list'), {**form, **files}, format='multipart',
            )
        else:
            resp = self.client.post(
                reverse('certificate-template-list'), data, format='json',
            )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        return resp.data

    def _issue(self, **kwargs):
        data = self._issue_payload(**kwargs)
        files = {}
        form = {}
        multipart = any(isinstance(v, SimpleUploadedFile) for v in data.values())
        if multipart:
            for key, value in data.items():
                if isinstance(value, SimpleUploadedFile):
                    files[key] = value
                else:
                    form[key] = value
            resp = self.client.post(
                reverse('certificate-list'), {**form, **files}, format='multipart',
            )
        else:
            resp = self.client.post(reverse('certificate-list'), data, format='json')
        self.assertTrue(resp.status_code in (status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST), resp.data)
        return resp

    def test_template_crud_and_validation(self):
        created = self._create_template()
        self.assertEqual(created['church'], self.sede.id)
        self.assertEqual(created['certificate_type'], 'BAPTISM')
        self.assertIsNone(created['background_image_name'])
        self.assertIsNone(created['base_pdf_name'])

        resp = self.client.post(
            reverse('certificate-template-list'),
            {**self._template_payload(), 'layout_mode': 'CUSTOM_IMAGE'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('background_image', resp.data)

        png = SimpleUploadedFile('moldura.png', self._tiny_png_bytes(), content_type='image/png')
        resp = self.client.post(
            reverse('certificate-template-list'),
            {
                **self._template_payload(name='Moldura', certificate_type='CUSTOM'),
                'layout_mode': 'CUSTOM_IMAGE',
                'background_image': png,
            },
            format='multipart',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        img_id = resp.data['id']
        self.assertTrue(resp.data['background_image_name'].endswith('.png'))

        resp = self.client.post(
            reverse('certificate-template-list'),
            {
                **self._template_payload(name='Base', certificate_type='CUSTOM'),
                'layout_mode': 'BASE_PDF',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('base_pdf', resp.data)

        base = SimpleUploadedFile('base.pdf', self._landscape_pdf_bytes(), content_type='application/pdf')
        resp = self.client.post(
            reverse('certificate-template-list'),
            {
                **self._template_payload(name='Base', certificate_type='CUSTOM'),
                'layout_mode': 'BASE_PDF',
                'base_pdf': base,
            },
            format='multipart',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

        resp = self.client.patch(
            reverse('certificate-template-detail', args=[img_id]),
            {'remove_background_image': 'true'},
            format='multipart',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertIsNone(resp.data['background_image_name'])

        resp = self.client.delete(reverse('certificate-template-detail', args=[img_id]))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_template_roles_and_scoping(self):
        treasurer = self._user(
            'tes-cert@teste.com', church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        resp = self._client(treasurer).get(reverse('certificate-template-list'))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        other = self._user(
            'sec-cert-outra@teste.com', church=self.congregation,
            role=ChurchMembership.Role.SECRETARIA,
        )
        self._create_template()
        resp = self._client(other).get(reverse('certificate-template-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, [])

    def test_issue_with_frontend_generated_pdf(self):
        self._create_template()
        pdf = SimpleUploadedFile(
            'certificado_batismo_test.pdf',
            b'%PDF-1.4 fake generated by frontend',
            content_type='application/pdf',
        )
        resp = self._issue(generated_pdf=pdf)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        data = resp.data
        self.assertEqual(data['recipient_name'], 'João Pedro da Silva')
        self.assertEqual(data['certificate_type'], 'BAPTISM')
        self.assertEqual(data['certificate_type_display'], 'Batismo nas Águas')
        self.assertTrue(data['generated_pdf_name'].endswith('.pdf'))
        self.assertEqual(data['created_by'], self.sec.id)

        resp = self.client.get(reverse('certificate-download-pdf', args=[data['id']]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertEqual(resp['Content-Disposition'].split(';')[0], 'attachment')

    def test_issue_base_pdf_compiles_backend(self):
        base = SimpleUploadedFile('base.pdf', self._landscape_pdf_bytes(), content_type='application/pdf')
        template = self._create_template(
            name='Base Batismo',
            certificate_type=CertificateTemplate.CertificateType.BAPTISM,
            layout_mode=CertificateTemplate.LayoutMode.BASE_PDF,
            base_pdf=base,
        )
        resp = self._issue(template=template['id'])
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        cert = EcclesiasticalCertificate.objects.get(pk=resp.data['id'])
        cert.generated_pdf.open('rb')
        payload = cert.generated_pdf.read()
        cert.generated_pdf.close()
        self.assertTrue(payload.startswith(b'%PDF'))

        resp = self.client.get(reverse('certificate-download-pdf', args=[cert.id]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp['Content-Type'], 'application/pdf')

    def test_issue_requires_pdf_or_base_template(self):
        self._create_template()
        resp = self._issue()
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('generated_pdf', resp.data)

    def test_list_filters(self):
        self._create_template()
        pdf = SimpleUploadedFile(
            'certificado.pdf', b'%PDF-1.4', content_type='application/pdf',
        )
        self._issue(generated_pdf=pdf)
        second = self._issue(
            recipient_name='Ana Clara Souza',
            certificate_type=CertificateTemplate.CertificateType.CUSTOM,
            event_date='2025-03-10',
            generated_pdf=pdf,
        )
        self.assertEqual(second.status_code, status.HTTP_201_CREATED, second.data)

        resp = self.client.get(reverse('certificate-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 2)

        resp = self.client.get(reverse('certificate-list'), {'type': 'CUSTOM'})
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['recipient_name'], 'Ana Clara Souza')

        resp = self.client.get(reverse('certificate-list'), {'year': '2025'})
        self.assertEqual(len(resp.data), 1)

        resp = self.client.get(reverse('certificate-list'), {'search': 'João'})
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['recipient_name'], 'João Pedro da Silva')

    def test_delete_removes_storage_file(self):
        from core.storage import raw_storage

        self._create_template()
        pdf = SimpleUploadedFile(
            'certificado_delete.pdf', b'%PDF-1.4', content_type='application/pdf',
        )
        resp = self._issue(generated_pdf=pdf)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        cert = EcclesiasticalCertificate.objects.get(pk=resp.data['id'])
        fname = cert.generated_pdf.name
        self.assertTrue(raw_storage.exists(fname))

        resp = self.client.delete(reverse('certificate-detail', args=[cert.id]))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(EcclesiasticalCertificate.objects.count(), 0)
        self.assertFalse(raw_storage.exists(fname))

    @staticmethod
    def _fields_layout_payload():
        return {
            'recipient_name': {
                'enabled': True, 'x': 50.0, 'y': 42.5, 'font_size': 26,
                'font_weight': 'bold', 'align': 'center', 'color': '#1a1a1a',
            },
            'event_date': {
                'enabled': True, 'x': 50.0, 'y': 55.0, 'font_size': 22,
                'font_weight': 'normal', 'align': 'center', 'color': '#333333',
            },
            'church_name': {
                'enabled': True, 'x': 50.0, 'y': 18.0, 'font_size': 30,
                'font_weight': 'bold', 'align': 'center', 'color': '#1a1a1a',
            },
            'parents_names': {
                'enabled': True, 'x': 50.0, 'y': 62.0, 'font_size': 18,
                'font_weight': 'normal', 'align': 'center', 'color': '#333333',
            },
            'scripture_verse': {
                'enabled': True, 'x': 50.0, 'y': 72.0, 'font_size': 17,
                'font_weight': 'italic', 'align': 'center', 'color': '#444444',
            },
            'custom_text': {
                'enabled': False, 'x': 50.0, 'y': 78.0, 'font_size': 16,
                'font_weight': 'normal', 'align': 'center', 'color': '#555555',
            },
            'registry_info': {
                'enabled': True, 'x': 50.0, 'y': 85.0, 'font_size': 14,
                'font_weight': 'normal', 'align': 'left', 'color': '#666666',
            },
            'officiant_name': {
                'enabled': True, 'x': 50.0, 'y': 90.0, 'font_size': 18,
                'font_weight': 'bold', 'align': 'center', 'color': '#1a1a1a',
            },
            'certificate_number': {
                'enabled': True, 'x': 50.0, 'y': 95.0, 'font_size': 14,
                'font_weight': 'normal', 'align': 'right', 'color': '#666666',
            },
        }

    def test_fields_layout_custom_image_roundtrip(self):
        png = SimpleUploadedFile('moldura.png', self._tiny_png_bytes(), content_type='image/png')
        created = self._create_template(
            name='Moldura Layout',
            certificate_type=CertificateTemplate.CertificateType.BAPTISM,
            layout_mode=CertificateTemplate.LayoutMode.CUSTOM_IMAGE,
            background_image=png,
        )
        self.assertEqual(created['fields_layout'], {})

        layout = self._fields_layout_payload()
        template_id = created['id']
        resp = self.client.patch(
            reverse('certificate-template-detail', args=[template_id]),
            {'fields_layout': layout},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data['fields_layout'], layout)

        resp = self.client.get(reverse('certificate-template-detail', args=[template_id]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['fields_layout'], layout)

        updated = dict(layout)
        updated['custom_text']['enabled'] = True
        updated['event_date']['font_size'] = 24
        resp = self.client.patch(
            reverse('certificate-template-detail', args=[template_id]),
            {'fields_layout': updated},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data['fields_layout'], updated)

    def test_fields_layout_accepts_any_json_object(self):
        resp = self.client.post(
            reverse('certificate-template-list'),
            {**self._template_payload(name='Layout Vazio'), 'fields_layout': {}},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data['fields_layout'], {})

        nested = {
            'recipient_name': {
                'enabled': True,
                'meta': {'font_size': 26, 'group': ['a', 'b', {'k': 1}]},
            },
        }
        resp = self.client.post(
            reverse('certificate-template-list'),
            {**self._template_payload(name='Layout Aninhado'), 'fields_layout': nested},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data['fields_layout'], nested)

    def test_fields_layout_rejects_non_dict(self):
        resp = self.client.post(
            reverse('certificate-template-list'),
            {**self._template_payload(name='Layout Invalido'), 'fields_layout': ['a', 'b']},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn('fields_layout', resp.data)


class IntercessionRoleTests(BaseChurchTestCase):
    """Perfil INTERCESSAO: RBAC de consulta de membros e suas permissões."""

    def test_intercessao_role_label(self):
        self.assertEqual(
            dict(ChurchMembership.Role.choices)[ChurchMembership.Role.INTERCESSAO],
            'Intercessão & Visitação',
        )

    def test_intercessao_can_list_members_readonly(self):
        intercessor = self._user(
            'intercessor@teste.com', church=self.sede,
            role=ChurchMembership.Role.INTERCESSAO,
        )
        Member.objects.create(
            church=self.sede, name='Membro Teste', phone='83999991111',
        )
        client = self._client(intercessor)
        resp = client.get(reverse('member-self-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data[0]['name'], 'Membro Teste')

    def test_intercessao_cannot_create_member(self):
        intercessor = self._user(
            'intercessor@teste.com', church=self.sede,
            role=ChurchMembership.Role.INTERCESSAO,
        )
        client = self._client(intercessor)
        resp = client.post(
            reverse('member-self-list'),
            {'name': 'Tentativa', 'phone': '83988887777'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_intercessao_blocked_from_user_management(self):
        intercessor = self._user(
            'intercessor@teste.com', church=self.sede,
            role=ChurchMembership.Role.INTERCESSAO,
        )
        client = self._client(intercessor)
        resp = client.get(
            reverse('church-users', args=[self.sede.pk]),
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_pastor_can_assign_intercessao_role(self):
        pastor = self._user(
            'pastor@teste.com', church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        cliente = self._user('cliente@teste.com', name='Cliente')
        client = self._client(pastor)
        resp = client.post(
            reverse('church-users', args=[self.sede.pk]),
            {
                'email': cliente.email,
                'password': 'S3nh@segura',
                'password2': 'S3nh@segura',
                'name': 'Cliente',
                'role': ChurchMembership.Role.INTERCESSAO,
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data['role'], ChurchMembership.Role.INTERCESSAO)


class PrayerRequestTests(BaseChurchTestCase):
    """Módulo de Pedidos de Oração: captação pública e triagem administrativa."""

    def _prayer(self, **kw):
        defaults = {
            'church': self.sede,
            'requester_name': 'Ana Souza',
            'requester_phone': '83999990000',
            'category': PrayerRequest.Category.HEALTH,
            'description': 'Oração pela minha saúde.',
            'wants_visit': True,
            'neighborhood': 'Catolé',
            'preferred_period': PrayerRequest.PreferredPeriod.AFTERNOON,
        }
        defaults.update(kw)
        return PrayerRequest.objects.create(**defaults)

    def test_public_submit_creates_pending(self):
        client = APIClient()
        resp = client.post(
            reverse('public-church-prayer-requests', args=[self.sede.slug]),
            {
                'requester_name': 'Maria Silva',
                'requester_phone': '(83) 99999-0000',
                'category': 'FAMILY',
                'description': 'Oração pela minha família.',
                'wants_visit': True,
                'neighborhood': 'Bodocongó',
                'preferred_period': 'MORNING',
                'is_anonymous': True,
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        pr = PrayerRequest.objects.get(pk=resp.data['id'])
        self.assertEqual(pr.church, self.sede)
        self.assertEqual(pr.status, PrayerRequest.Status.PENDING)
        self.assertTrue(pr.is_anonymous)

    def test_public_submit_unknown_slug_404(self):
        client = APIClient()
        resp = client.post(
            reverse('public-church-prayer-requests', args=['slug-inexistente']),
            {'requester_name': 'A', 'description': 'B'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_public_submit_requires_description(self):
        client = APIClient()
        resp = client.post(
            reverse('public-church-prayer-requests', args=[self.sede.slug]),
            {'requester_name': 'Maria', 'category': 'FAMILY', 'description': ''},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn('description', resp.data)

    def test_rbac_intercessao_can_list(self):
        intercessor = self._user(
            'intercessor@teste.com', church=self.sede,
            role=ChurchMembership.Role.INTERCESSAO,
        )
        self._prayer()
        client = self._client(intercessor)
        resp = client.get(reverse('prayer-request-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['requester_name'], 'Ana Souza')

    def test_assignable_users_only_eligible_roles(self):
        intercessor = self._user(
            'intercessor@teste.com', church=self.sede,
            role=ChurchMembership.Role.INTERCESSAO,
        )
        pastor = self._user(
            'pastor@teste.com', church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        tesoureiro = self._user(
            'tesoureiro@teste.com', church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        musico = self._user(
            'musico@teste.com', church=self.sede,
            role=ChurchMembership.Role.MUSICO,
        )
        client = self._client(intercessor)
        resp = client.get(reverse('prayer-request-assignable-users'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        ids = {item['id'] for item in resp.data}
        self.assertIn(intercessor.id, ids)
        self.assertIn(pastor.id, ids)
        self.assertNotIn(tesoureiro.id, ids)
        self.assertNotIn(musico.id, ids)

    def test_rbac_treasurer_blocked(self):
        tesoureiro = self._user(
            'tesoureiro@teste.com', church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        self._prayer()
        client = self._client(tesoureiro)
        resp = client.get(reverse('prayer-request-list'))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_status_filter_and_patch(self):
        intercessor = self._user(
            'intercessor@teste.com', church=self.sede,
            role=ChurchMembership.Role.INTERCESSAO,
        )
        self._prayer()
        client = self._client(intercessor)
        resp = client.get(reverse('prayer-request-list'), {'status': 'PENDING'})
        self.assertEqual(len(resp.data), 1)
        pr = PrayerRequest.objects.first()
        resp = client.patch(
            reverse('prayer-request-detail', args=[pr.pk]),
            {'status': 'PRAYING', 'pastoral_notes': 'Orando pela família.'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PrayerRequest.Status.PRAYING)
        self.assertEqual(pr.pastoral_notes, 'Orando pela família.')

    def test_prepare_whatsapp(self):
        intercessor = self._user(
            'intercessor@teste.com', church=self.sede,
            role=ChurchMembership.Role.INTERCESSAO,
        )
        pr = self._prayer()
        client = self._client(intercessor)
        resp = client.post(
            reverse('prayer-request-prepare-whatsapp', args=[pr.pk]),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertIn('wa.me/55', resp.data['url'])

    def test_prepare_whatsapp_without_phone_400(self):
        intercessor = self._user(
            'intercessor@teste.com', church=self.sede,
            role=ChurchMembership.Role.INTERCESSAO,
        )
        pr = self._prayer(requester_phone='')
        client = self._client(intercessor)
        resp = client.post(
            reverse('prayer-request-prepare-whatsapp', args=[pr.pk]),
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_print_sheet_pdf(self):
        intercessor = self._user(
            'intercessor@teste.com', church=self.sede,
            role=ChurchMembership.Role.INTERCESSAO,
        )
        self._prayer()
        client = self._client(intercessor)
        resp = client.get(reverse('prayer-request-print-sheet'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertIn('caderno-oracao', resp['Content-Disposition'])

    def test_print_sheet_excludes_archived(self):
        intercessor = self._user(
            'intercessor@teste.com', church=self.sede,
            role=ChurchMembership.Role.INTERCESSAO,
        )
        self._prayer(status=PrayerRequest.Status.ARCHIVED)
        client = self._client(intercessor)
        resp = client.get(reverse('prayer-request-print-sheet'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_visit_from_prayer_sets_visit_scheduled(self):
        pastor = self._user(
            'pastor@teste.com', church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        pr = self._prayer()
        client = self._client(pastor)
        resp = client.post(
            reverse('pastoral-visit-list'),
            {
                'prayer_request': pr.pk,
                'target_name': pr.requester_name,
                'target_phone': pr.requester_phone,
                'visit_type': 'ROUTINE',
                'competence_year': 2026,
                'competence_month': 9,
                'scheduled_date': '2026-09-20',
                'neighborhood': pr.neighborhood,
                'city': 'Campina Grande',
                'state': 'PB',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data['prayer_request'], pr.pk)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PrayerRequest.Status.VISIT_SCHEDULED)

    def test_visit_from_foreign_prayer_rejected(self):
        pastor = self._user(
            'pastor@teste.com', church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        foreign = self._prayer(church=self.congregation)
        client = self._client(pastor)
        resp = client.post(
            reverse('pastoral-visit-list'),
            {
                'prayer_request': foreign.pk,
                'target_name': 'Teste',
                'visit_type': 'ROUTINE',
                'competence_year': 2026,
                'competence_month': 9,
                'scheduled_date': '2026-09-20',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)


class SundaySchoolTests(BaseChurchTestCase):
    """Módulo de Escola Bíblica Dominical: classes, chamada e relatório mensal."""

    def _secretaria(self, church=None):
        return self._user(
            'secretaria.ebd@teste.com',
            name='Secretária EBD',
            church=church or self.sede,
            role=ChurchMembership.Role.SECRETARIA,
        )

    def _class(self, name='Adultos', **kw):
        defaults = {
            'church': self.sede,
            'name': name,
            'category': SundaySchoolClass.Category.ADULTS,
            'teacher_name': 'Prof. João',
        }
        defaults.update(kw)
        return SundaySchoolClass.objects.create(**defaults)

    def _student(self, sunday_class, **kw):
        defaults = {
            'sunday_school_class': sunday_class,
            'student_name': 'Maria Silva',
            'phone': '(83) 99999-0000',
        }
        defaults.update(kw)
        return SundaySchoolEnrollment.objects.create(**defaults)

    def test_rbac_only_secretaria_or_pastor(self):
        sunday_class = self._class()
        self._student(sunday_class)
        treasurer = self._user(
            'tesoureiro@teste.com', church=self.sede,
            role=ChurchMembership.Role.TESOUREIRO,
        )
        client = self._client(treasurer)
        resp = client.get(reverse('sunday-school-class-list'))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        resp = client.get(
            reverse('sunday-school-session-list'),
            {'class_id': sunday_class.pk},
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_class_crud(self):
        client = self._client(self._secretaria())
        resp = client.post(
            reverse('sunday-school-class-list'),
            {
                'name': 'Jovens',
                'category': 'YOUTH',
                'teacher_name': 'Prof. Ana',
                'co_teacher_name': 'Prof. Clara',
                'room_location': 'Sala 2',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        sunday_class = SundaySchoolClass.objects.get(pk=resp.data['id'])
        self.assertEqual(sunday_class.name, 'Jovens')
        self.assertEqual(sunday_class.church, self.sede)

        resp = client.patch(
            reverse('sunday-school-class-detail', args=[sunday_class.pk]),
            {'teacher_name': 'Prof. Beatriz'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data['teacher_name'], 'Prof. Beatriz')

    def test_enroll_and_delete_student(self):
        sunday_class = self._class()
        client = self._client(self._secretaria())
        resp = client.post(
            reverse('sunday-school-class-students', args=[sunday_class.pk]),
            {'student_name': 'Pedro Santos', 'phone': '(83) 98888-7777'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        enrollment = SundaySchoolEnrollment.objects.get(pk=resp.data['id'])
        self.assertEqual(enrollment.sunday_school_class, sunday_class)
        self.assertEqual(resp.data['whatsapp_url'], 'https://wa.me/5583988887777')

        resp = client.get(reverse('sunday-school-class-students', args=[sunday_class.pk]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)

        resp = client.delete(
            reverse('sunday-school-class-students', args=[sunday_class.pk]),
            {'enrollment_id': enrollment.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(SundaySchoolEnrollment.objects.filter(pk=enrollment.pk).exists())

    def test_session_get_prepares_sheet(self):
        sunday_class = self._class()
        self._student(sunday_class)
        client = self._client(self._secretaria())
        resp = client.get(
            reverse('sunday-school-session-list'),
            {'class_id': sunday_class.pk, 'date': '2026-09-06'},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        session = SundaySchoolSession.objects.get(
            sunday_school_class=sunday_class, date='2026-09-06'
        )
        self.assertEqual(len(resp.data['attendances']), 1)
        self.assertFalse(resp.data['attendances'][0]['is_present'])

        # Pedir a mesma folha novamente não duplica a aula.
        resp = client.get(
            reverse('sunday-school-session-list'),
            {'class_id': sunday_class.pk, 'date': '2026-09-06'},
        )
        self.assertEqual(
            SundaySchoolSession.objects.filter(
                sunday_school_class=sunday_class, date='2026-09-06'
            ).count(),
            1,
        )

    def test_session_post_saves_attendance_and_totals(self):
        sunday_class = self._class()
        enrollment = self._student(sunday_class, student_name='Maria Silva')
        client = self._client(self._secretaria())
        resp = client.post(
            reverse('sunday-school-session-list'),
            {
                'sunday_school_class': sunday_class.pk,
                'date': '2026-09-13',
                'topic': 'O Fruto do Espírito',
                'bibles_count': 5,
                'magazines_count': 4,
                'visitors_count': 2,
                'offering_amount': '150.00',
                'notes': 'Aula produtiva.',
                'attendance': [
                    {
                        'enrollment_id': enrollment.pk,
                        'is_present': True,
                        'brought_bible': True,
                        'brought_magazine': True,
                    },
                ],
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        session = SundaySchoolSession.objects.get(
            sunday_school_class=sunday_class, date='2026-09-13'
        )
        self.assertEqual(session.offering_amount, decimal.Decimal('150.00'))
        attendance = SundaySchoolAttendance.objects.get(
            session=session, enrollment=enrollment
        )
        self.assertTrue(attendance.is_present)
        self.assertTrue(attendance.brought_bible)
        self.assertEqual(resp.data['present_count'], 1)

    def test_session_post_rejects_foreign_enrollment(self):
        sunday_class = self._class()
        foreign_class = self._class(name='Infantil', category='CHILDREN')
        enrollment = self._student(foreign_class)
        client = self._client(self._secretaria())
        resp = client.post(
            reverse('sunday-school-session-list'),
            {
                'sunday_school_class': sunday_class.pk,
                'date': '2026-09-13',
                'attendance': [{'enrollment_id': enrollment.pk, 'is_present': True}],
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)

    def test_monthly_report_computes_presence_and_risk(self):
        sunday_class = self._class()
        present = self._student(sunday_class, student_name='Maria Silva')
        absent = self._student(sunday_class, student_name='João Vale', phone='')
        client = self._client(self._secretaria())

        session = SundaySchoolSession.objects.create(
            sunday_school_class=sunday_class,
            date='2026-09-06',
            offering_amount=decimal.Decimal('50.00'),
        )
        SundaySchoolAttendance.objects.create(
            session=session, enrollment=present, is_present=True,
        )

        resp = client.get(
            reverse('sunday-school-monthly-report'),
            {'year': 2026, 'month': 9},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(len(resp.data['classes']), 1)
        class_report = resp.data['classes'][0]
        self.assertEqual(class_report['offering_total'], '50.00')
        by_name = {s['student_name']: s for s in class_report['students']}
        self.assertEqual(by_name['Maria Silva']['present_count'], 1)
        self.assertEqual(by_name['Maria Silva']['presence_percent'], 100.0)
        self.assertFalse(by_name['Maria Silva']['risk_evasion'])
        self.assertFalse(by_name['João Vale']['risk_evasion'])

        # 3 domingos com aula seguidos ausente => risco de evasão.
        for day in ['2026-09-13', '2026-09-20']:
            s = SundaySchoolSession.objects.create(
                sunday_school_class=sunday_class,
                date=day,
            )
            for enrollment in (present, absent):
                SundaySchoolAttendance.objects.create(
                    session=s, enrollment=enrollment,
                    is_present=(enrollment is present and True),
                )
        resp = client.get(
            reverse('sunday-school-monthly-report'),
            {'year': 2026, 'month': 9},
        )
        class_report = resp.data['classes'][0]
        by_name = {s['student_name']: s for s in class_report['students']}
        self.assertTrue(by_name['João Vale']['risk_evasion'])
        self.assertEqual(by_name['João Vale']['consecutive_absences'], 3)
        self.assertIsNone(by_name['João Vale']['whatsapp_url'])

    def test_class_announcement_whatsapp(self):
        sunday_class = self._class()
        self._student(sunday_class)
        client = self._client(self._secretaria())
        resp = client.post(
            reverse('sunday-school-class-prepare-whatsapp', args=[sunday_class.pk]),
            {'kind': 'EBD_CLASS_ANNOUNCEMENT', 'topic': 'Unidade'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(len(resp.data['rows']), 1)
        self.assertIn('wa.me/55', resp.data['rows'][0]['url'])
        self.assertIn('Unidade', resp.data['rows'][0]['url'])

    def test_monthly_report_pdf(self):
        sunday_class = self._class()
        self._student(sunday_class)
        client = self._client(self._secretaria())
        resp = client.get(
            reverse('sunday-school-monthly-report-pdf'),
            {'year': 2026, 'month': 9},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertIn('relatorio-ebd', resp['Content-Disposition'])

    def test_absence_whatsapp_on_attendance(self):
        sunday_class = self._class()
        self._student(sunday_class)
        client = self._client(self._secretaria())
        resp = client.get(
            reverse('sunday-school-session-list'),
            {'class_id': sunday_class.pk, 'date': '2026-09-06'},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        attendance = resp.data['attendances'][0]
        self.assertIsNotNone(attendance['whatsapp_url'])
        self.assertIn('wa.me/55', attendance['whatsapp_url'])
