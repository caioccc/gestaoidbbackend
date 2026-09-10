from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Church, ChurchMembership, Member

from .models import CalendarEvent, DepartmentCategory, FinancialEntry, FinancialExit
from .services import (
    build_validation_checks,
)

User = get_user_model()


class RepasseTestCase(TestCase):
    """Dados comuns: Sede, duas congregações e usuários de cada perfil."""

    def setUp(self):
        self.sede = Church.objects.create(
            name='Sede Teste',
            church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE',
            is_approved=True,
            city='Campina Grande',
            state='PB',
        )
        self.cong = Church.objects.create(
            name='Congregação A',
            church_type=Church.ChurchType.CONGREGATION,
            parent_church=self.sede,
            status='ACTIVE',
            is_approved=True,
            accounting_category='DIZIMO',
            city='Campina Grande',
            state='PB',
        )
        self.cong_b = Church.objects.create(
            name='Congregação B',
            church_type=Church.ChurchType.CONGREGATION,
            parent_church=self.sede,
            status='ACTIVE',
            is_approved=True,
            accounting_category='CONSTRUCAO',
            city='João Pessoa',
            state='PB',
        )
        self.outra_sede = Church.objects.create(
            name='Sede Alheia',
            church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE',
            is_approved=True,
            city='Recife',
            state='PE',
        )

    def _user(self, email, church, role):
        user = User.objects.create(email=email, name='Usuário', church=church)
        user.set_password('S3nh@segura')
        user.save(update_fields=['password'])
        ChurchMembership.objects.create(user=user, church=church, role=role)
        return user

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def _entry(self, church, category, amount, day=5):
        return FinancialEntry.objects.create(
            church=church,
            date=date(2026, 6, day),
            service_description='Culto de Teste',
            category=category,
            amount=amount,
        )


class RepasseEndpointTests(RepasseTestCase):
    def test_regional_report_omits_congregation_remittances(self):
        # As congregações mantêm contas próprias; a Sede apenas gerencia.
        self._entry(self.cong, DepartmentCategory.DIZIMO, '1000.00')
        self._entry(self.cong, DepartmentCategory.MULHERES, '500.00')

        pastor = self._user('pastor@teste.com', self.sede, ChurchMembership.Role.PASTOR)
        resp = self._client(pastor).get(
            reverse('reports-regional'), {'year': 2026, 'month': 6}
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # A Sede não possui lançamentos próprios: remessa própria zerada.
        self.assertEqual(resp.data['remittance']['dizimos']['total_dizimos'], 0)
        # Sem bloco de repasses das congregações no relatório.
        self.assertNotIn('congregations', resp.data)

    def test_regional_report_denied_for_congregation_user(self):
        tesoureiro = self._user('tesoureiro@teste.com', self.cong, ChurchMembership.Role.TESOUREIRO)
        client = self._client(tesoureiro)
        resp = client.get(
            reverse('reports-regional'), {'year': 2026, 'month': 6}
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Regional', resp.data['detail'])

        resp_pdf = client.get(
            reverse('reports-regional-pdf'), {'year': 2026, 'month': 6}
        )
        self.assertEqual(resp_pdf.status_code, status.HTTP_400_BAD_REQUEST)

        resp_xls = client.get(
            reverse('reports-regional-xls'), {'year': 2026, 'month': 6}
        )
        self.assertEqual(resp_xls.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_regional_denied_for_congregation(self):
        pastor = self._user('pastor@teste.com', self.sede, ChurchMembership.Role.PASTOR)
        client = self._client(pastor)
        resp = client.get(
            reverse('admin-church-reports-regional', args=[self.cong.id]),
            {'year': 2026, 'month': 6},
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Regional', resp.data['detail'])

        resp_pdf = client.get(
            reverse('admin-church-reports-regional-pdf', args=[self.cong.id]),
            {'year': 2026, 'month': 6},
        )
        self.assertEqual(resp_pdf.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_regional_still_works_for_sede(self):
        self._entry(self.cong, DepartmentCategory.DIZIMO, '1000.00')
        pastor = self._user('pastor@teste.com', self.sede, ChurchMembership.Role.PASTOR)
        resp = self._client(pastor).get(
            reverse('admin-church-reports-regional', args=[self.sede.id]),
            {'year': 2026, 'month': 6},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertNotIn('congregations', resp.data)

    def test_monthly_closings_grand_total_ignores_congregations(self):
        self._entry(self.cong, DepartmentCategory.DIZIMO, '1000.00')
        pastor = self._user('pastor@teste.com', self.sede, ChurchMembership.Role.PASTOR)
        resp = self._client(pastor).get(
            reverse('monthly-closings'), {'year': 2026}
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['grand_total']['total_entries'], '0.00')
        self.assertNotIn('congregations', resp.data)

    def test_dre_summary_has_no_congregation_block(self):
        self._entry(self.cong, DepartmentCategory.DIZIMO, '1000.00')
        pastor = self._user('pastor@teste.com', self.sede, ChurchMembership.Role.PASTOR)
        resp = self._client(pastor).get(reverse('dre-summary'), {'year': 2026})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertNotIn('congregations', resp.data)

        tesoureiro = self._user('tesoureiro@teste.com', self.cong, ChurchMembership.Role.TESOUREIRO)
        resp_cong = self._client(tesoureiro).get(reverse('dre-summary'), {'year': 2026})
        self.assertEqual(resp_cong.status_code, status.HTTP_200_OK)
        self.assertNotIn('congregations', resp_cong.data)


class TargetChurchIsolationTests(RepasseTestCase):
    def test_sede_pastor_accesses_own_congregation_entries(self):
        pastor = self._user('pastor@teste.com', self.sede, ChurchMembership.Role.PASTOR)
        resp = self._client(pastor).get(
            reverse('admin-church-entries-list', args=[self.cong.id])
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_other_sede_pastor_is_forbidden(self):
        outsider = self._user('forasteiro@teste.com', self.outra_sede, ChurchMembership.Role.PASTOR)
        resp = self._client(outsider).get(
            reverse('admin-church-entries-list', args=[self.cong.id])
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_congregation_user_cannot_access_other_church(self):
        tesoureiro = self._user('tesoureiro@teste.com', self.cong, ChurchMembership.Role.TESOUREIRO)
        resp = self._client(tesoureiro).get(
            reverse('admin-church-entries-list', args=[self.cong_b.id])
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_congregation_user_sees_only_own_entries(self):
        self._entry(self.cong, DepartmentCategory.DIZIMO, '1000.00')
        self._entry(self.cong_b, DepartmentCategory.DIZIMO, '9999.00')
        tesoureiro = self._user('tesoureiro@teste.com', self.cong, ChurchMembership.Role.TESOUREIRO)
        resp = self._client(tesoureiro).get(reverse('entry-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [e['id'] for e in resp.data['results']]
        self.assertCountEqual(
            ids,
            list(FinancialEntry.objects.filter(church=self.cong).values_list('id', flat=True)),
        )


class CalendarEventUnifiedTests(RepasseTestCase):
    """Calendário geral: audiência GENERAL (secretaria) vs FINANCE (tesouro)."""

    def setUp(self):
        super().setUp()
        self.sec = self._user('sec@teste.com', self.sede, ChurchMembership.Role.SECRETARIA)
        self.tes = self._user('tes@teste.com', self.sede, ChurchMembership.Role.TESOUREIRO)
        self.pastor = self._user('pastor@teste.com', self.sede, ChurchMembership.Role.PASTOR)
        self.member = Member.objects.create(
            church=self.sede, name='Membro A', birth_date='1990-01-01',
        )
        self.other_member = Member.objects.create(
            church=self.cong, name='Membro B', birth_date='1991-01-01',
        )

    def url(self, pk=None):
        base = reverse('calendar-event-list')
        return base if pk is None else reverse('calendar-event-detail', args=[pk])

    def _create(self, client, **extra):
        payload = {
            'title': 'Evento Teste',
            'date': '2026-10-15',
            'repeat_monthly': False,
            **extra,
        }
        return client.post(self.url(), payload, format='json')

    def test_secretary_creates_general_event(self):
        resp = self._create(self._client(self.sec), title='Culto de Jovens')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['audience'], 'GENERAL')
        self.assertEqual(resp.data['created_by'], self.sec.id)

    def test_treasurer_creates_finance_event(self):
        resp = self._create(self._client(self.tes), title='Conta de Luz')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['audience'], 'FINANCE')

    def test_pastor_can_choose_audience(self):
        resp = self._create(self._client(self.pastor), audience='GENERAL')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['audience'], 'GENERAL')

    def test_secretary_only_sees_general_events(self):
        self._create(self._client(self.tes), title='Tesouro Privado')
        self._create(self._client(self.sec), title='Secretaria Aberto')
        resp = self._client(self.sec).get(self.url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        titles = [e['title'] for e in resp.data]
        self.assertIn('Secretaria Aberto', titles)
        self.assertNotIn('Tesouro Privado', titles)

    def test_treasurer_sees_everything(self):
        self._create(self._client(self.tes), title='Tesouro Privado')
        self._create(self._client(self.sec), title='Secretaria Aberto')
        resp = self._client(self.tes).get(self.url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        titles = [e['title'] for e in resp.data]
        self.assertEqual(set(titles), {'Tesouro Privado', 'Secretaria Aberto'})

    def test_treasurer_cannot_edit_general_event(self):
        ev = CalendarEvent.objects.create(
            church=self.sede, audience='GENERAL', title='Agenda', date='2026-10-15',
        )
        resp = self._client(self.tes).patch(
            self.url(ev.pk), {'title': 'Alterado'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        resp = self._client(self.tes).delete(self.url(ev.pk))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_secretary_cannot_see_finance_event(self):
        ev = CalendarEvent.objects.create(
            church=self.sede, audience='FINANCE', title='Conta', date='2026-10-15',
        )
        resp = self._client(self.sec).patch(
            self.url(ev.pk), {'title': 'Alterado'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_secretary_edits_general_event(self):
        ev = CalendarEvent.objects.create(
            church=self.sede, audience='GENERAL', title='Antigo', date='2026-10-15',
        )
        resp = self._client(self.sec).patch(
            self.url(ev.pk), {'title': 'Novo'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ev.refresh_from_db()
        self.assertEqual(ev.title, 'Novo')

    def test_treasurer_edits_finance_event(self):
        ev = CalendarEvent.objects.create(
            church=self.sede, audience='FINANCE', title='Conta', date='2026-10-15',
        )
        resp = self._client(self.tes).patch(
            self.url(ev.pk), {'title': 'Energia'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ev.refresh_from_db()
        self.assertEqual(ev.title, 'Energia')

    def test_secretary_rejects_cross_church_members(self):
        resp = self._create(self._client(self.sec), members=[self.other_member.id])
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_secretary_accepts_same_church_members(self):
        resp = self._create(self._client(self.sec), members=[self.member.id])
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            resp.data['members_names'], [{'id': self.member.id, 'name': 'Membro A'}],
        )

    def test_pastor_manages_all_audiences(self):
        gen = CalendarEvent.objects.create(
            church=self.sede, audience='GENERAL', title='Agenda', date='2026-10-15',
        )
        fin = CalendarEvent.objects.create(
            church=self.sede, audience='FINANCE', title='Conta', date='2026-10-15',
        )
        self.assertEqual(
            self._client(self.pastor).patch(
                self.url(gen.pk), {'title': 'G+'}, format='json',
            ).status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            self._client(self.pastor).patch(
                self.url(fin.pk), {'title': 'F+'}, format='json',
            ).status_code,
            status.HTTP_200_OK,
        )

    def test_admin_staff_sees_and_manages_all(self):
        admin = User.objects.create(
            email='admin@teste.com', name='Admin', is_staff=True, is_superuser=True,
        )
        admin.set_password('S3nh@segura')
        admin.save(update_fields=['password'])
        resp = self._client(admin).get(self.url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, [])

    def test_public_endpoint_only_general_events(self):
        self._create(self._client(self.sec), title='Evento Público')
        self._create(self._client(self.tes), title='Evento Secreto')
        self.sede.ensure_public_hash()
        self.sede.save(update_fields=['calendar_public_hash'])
        client = APIClient()
        resp = client.get(reverse('public-calendar', args=[self.sede.calendar_public_hash]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['church']['name'], self.sede.name)
        titles = [e['title'] for e in resp.data['events']]
        self.assertEqual(titles, ['Evento Público'])
        self.assertNotIn('members', resp.data['events'][0])
        self.assertNotIn('created_by', resp.data['events'][0])

    def test_public_endpoint_unknown_hash_404(self):
        resp = APIClient().get(reverse('public-calendar', args=['a' * 32]))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class ValidationChecksCongregationTests(RepasseTestCase):
    """Congregações não possuem prebenda pastoral (paga pela Sede)."""

    def _exit(self, church):
        return FinancialExit.objects.create(
            church=church,
            date=date(2026, 6, 5),
            description='Prebenda Pastoral',
            category=DepartmentCategory.OFERTA,
            amount='500.00',
        )

    def test_congregation_checks_omit_prebenda(self):
        self._exit(self.cong)
        checks = build_validation_checks(self.cong, 2026, 6)
        self.assertNotIn('prebenda', checks)
        natures = [n['nature'] for n in checks['nature_summary']]
        self.assertNotIn('PREBENDA', natures)

    def test_sede_checks_include_prebenda(self):
        self._exit(self.sede)
        checks = build_validation_checks(self.sede, 2026, 6)
        self.assertIn('prebenda', checks)
        natures = [n['nature'] for n in checks['nature_summary']]
        self.assertIn('PREBENDA', natures)

    def test_congregation_monthly_validation_endpoint_omits_prebenda(self):
        self._exit(self.cong)
        tesoureiro = self._user('tesoureiro@teste.com', self.cong, ChurchMembership.Role.TESOUREIRO)
        resp = self._client(tesoureiro).get(
            reverse('monthly-validation'), {'year': 2026, 'month': 6}
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertNotIn('prebenda', resp.data['checks'])
        natures = [n['nature'] for n in resp.data['checks']['nature_summary']]
        self.assertNotIn('PREBENDA', natures)