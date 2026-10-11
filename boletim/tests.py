"""Testes do app boletim (Folder / Boletim)."""
from datetime import date

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Church, ChurchMembership, Member, User
from finance.models import CalendarEvent

from .models import FolderCulto, FolderCultoRow


class BaseFolderTestCase(TestCase):
    def setUp(self):
        self.church = Church.objects.create(
            name='Igreja Teste',
            church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE',
            is_approved=True,
            city='Campina Grande',
            state='PB',
        )
        self.other = Church.objects.create(
            name='Outra Igreja',
            church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE',
            is_approved=True,
            city='João Pessoa',
            state='PB',
        )

    def _user(self, email, role=None, church=None, is_staff=False):
        user = User.objects.create(
            email=email,
            name='Usuário',
            church=church or self.church,
            is_staff=is_staff,
            is_active=True,
        )
        user.set_password('S3nh@segura')
        user.save(update_fields=['password'])
        if role is not None:
            ChurchMembership.objects.create(
                user=user, church=church or self.church, role=role,
            )
        return user

    def _client(self, user=None):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        return client

    def _folder_payload(self, **overrides):
        payload = {
            'tipo': 'SEMANAL',
            'title': 'Boletim da Semana',
            'subtitle': 'Domingo, 11 de outubro',
            'cover_template': 1,
            'theme_color': '#0f766e',
            'mensagem_pastoral': '<p>Paz!</p>',
            'versiculo': 'Filipenses 4:13',
            'reference_start': '2026-10-05',
            'reference_end': '2026-10-11',
            'programacao': [{'time': '19:00', 'label': 'Louvor'}],
            'anuncios': [{'title': 'Culto de Ação', 'text': 'Dia 18/10.', 'highlight': False}],
            'aniversariantes': [{'name': 'Ana', 'day': 8, 'month': 10, 'age': 30}],
            'qr': {},
        }
        payload.update(overrides)
        return payload


class FolderCrudTests(BaseFolderTestCase):
    def test_create_folder_as_secretary(self):
        secretary = self._user('sec@teste.com', ChurchMembership.Role.SECRETARIA)
        client = self._client(secretary)
        resp = client.post(
            reverse('folder-list'), self._folder_payload(), format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        folder = FolderCulto.objects.get(pk=resp.data['id'])
        self.assertEqual(folder.church, self.church)
        self.assertTrue(folder.public_hash)
        self.assertTrue(resp.data['public_url'].endswith(folder.public_hash))

    def test_default_qr_populated(self):
        pastor = self._user('pastor@teste.com', ChurchMembership.Role.PASTOR)
        resp = self._client(pastor).post(
            reverse('folder-list'), self._folder_payload(), format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn('cadastro', resp.data['default_qr'])
        self.assertEqual(
            resp.data['default_qr']['cadastro']['url'],
            f'{settings.FRONTEND_URL}/formulario/{self.church.member_form_hash}',
        )

    def test_duplicate_period_rejected(self):
        pastor = self._user('pastor@teste.com', ChurchMembership.Role.PASTOR)
        client = self._client(pastor)
        first = client.post(reverse('folder-list'), self._folder_payload(), format='json')
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        second = client.post(reverse('folder-list'), self._folder_payload(), format='json')
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('reference_start', second.data)

    def test_end_before_start_rejected(self):
        pastor = self._user('pastor@teste.com', ChurchMembership.Role.PASTOR)
        resp = self._client(pastor).post(
            reverse('folder-list'),
            self._folder_payload(reference_start='2026-10-11', reference_end='2026-10-05'),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rows_nested_create_and_update(self):
        pastor = self._user('pastor@teste.com', ChurchMembership.Role.PASTOR)
        client = self._client(pastor)
        payload = self._folder_payload(rows=[
            {
                'service_type': 'CELEBRACAO', 'date': '2026-10-11',
                'start_time': '19:00', 'title': 'Culto de Celebração',
                'preacher': 'Pr. João', 'sort_order': 0,
            },
        ])
        resp = client.post(reverse('folder-list'), payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        folder_id = resp.data['id']
        self.assertEqual(FolderCultoRow.objects.filter(folder_id=folder_id).count(), 1)
        upd = client.patch(
            reverse('folder-detail', args=[folder_id]),
            {'rows': [{
                'service_type': 'ORACAO', 'date': '2026-10-11',
                'title': 'Oração', 'preacher': '', 'sort_order': 0,
            }]},
            format='json',
        )
        self.assertEqual(upd.status_code, status.HTTP_200_OK)
        rows = list(FolderCultoRow.objects.filter(folder_id=folder_id))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].service_type, 'ORACAO')

    def test_versiculo_create_and_update(self):
        pastor = self._user('pastor@teste.com', ChurchMembership.Role.PASTOR)
        client = self._client(pastor)
        resp = client.post(
            reverse('folder-list'),
            self._folder_payload(versiculo='João 3:16'),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['versiculo'], 'João 3:16')
        upd = client.patch(
            reverse('folder-detail', args=[resp.data['id']]),
            {'versiculo': 'Salmos 23:1'},
            format='json',
        )
        self.assertEqual(upd.status_code, status.HTTP_200_OK)
        self.assertEqual(upd.data['versiculo'], 'Salmos 23:1')

    def test_versiculo_blank_allowed(self):
        pastor = self._user('pastor@teste.com', ChurchMembership.Role.PASTOR)
        resp = self._client(pastor).post(
            reverse('folder-list'), self._folder_payload(versiculo=''), format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['versiculo'], '')

    def test_custom_theme_template_and_colors(self):
        pastor = self._user('pastor@teste.com', ChurchMembership.Role.PASTOR)
        resp = self._client(pastor).post(
            reverse('folder-list'),
            self._folder_payload(
                cover_template=5,
                theme_color='#112233',
                background_color='#f0f0f0',
                cover_font='serif',
            ),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['cover_template'], 5)
        self.assertEqual(resp.data['background_color'], '#f0f0f0')
        self.assertEqual(resp.data['cover_font'], 'serif')

    def test_minimal_template_accepted(self):
        pastor = self._user('pastor@teste.com', ChurchMembership.Role.PASTOR)
        resp = self._client(pastor).post(
            reverse('folder-list'),
            self._folder_payload(cover_template=4),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['cover_template'], 4)

    def test_blocks_roundtrip(self):
        pastor = self._user('pastor@teste.com', ChurchMembership.Role.PASTOR)
        client = self._client(pastor)
        blocks = {
            'donations': True, 'social': False, 'address': True, 'links': False,
        }
        resp = client.post(
            reverse('folder-list'),
            self._folder_payload(blocks=blocks),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['blocks'], blocks)
        upd = client.patch(
            reverse('folder-detail', args=[resp.data['id']]),
            {'blocks': {**blocks, 'social': True}},
            format='json',
        )
        self.assertEqual(upd.status_code, status.HTTP_200_OK)
        self.assertTrue(upd.data['blocks']['social'])

    def test_service_type_cover_roundtrip(self):
        pastor = self._user('pastor@teste.com', ChurchMembership.Role.PASTOR)
        client = self._client(pastor)
        resp = client.post(
            reverse('folder-list'),
            self._folder_payload(service_type='VIGILIA'),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['service_type'], 'VIGILIA')
        self.assertEqual(resp.data['service_type_display'], 'Vigília')
        upd = client.patch(
            reverse('folder-detail', args=[resp.data['id']]),
            {'service_type': 'CEIA'},
            format='json',
        )
        self.assertEqual(upd.status_code, status.HTTP_200_OK)
        self.assertEqual(upd.data['service_type'], 'CEIA')

    def test_social_toggles_roundtrip(self):
        pastor = self._user('pastor@teste.com', ChurchMembership.Role.PASTOR)
        client = self._client(pastor)
        social = {'INSTAGRAM': False, 'YOUTUBE': True}
        resp = client.post(
            reverse('folder-list'),
            self._folder_payload(social=social),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['social'], social)
        upd = client.patch(
            reverse('folder-detail', args=[resp.data['id']]),
            {'social': {**social, 'WHATSAPP': False}},
            format='json',
        )
        self.assertEqual(upd.status_code, status.HTTP_200_OK)
        self.assertFalse(upd.data['social']['WHATSAPP'])
        self.assertFalse(upd.data['social']['INSTAGRAM'])

    def test_layout_block_order_and_article_title_roundtrip(self):
        pastor = self._user('pastor@teste.com', ChurchMembership.Role.PASTOR)
        client = self._client(pastor)
        order = [
            'programacao', 'agenda', 'avisos', 'aniversariantes',
            'contatos', 'dizimos', 'links', 'social',
            'oracao', 'ficha_visitante', 'cadastro', 'anotacoes',
        ]
        resp = client.post(
            reverse('folder-list'),
            self._folder_payload(
                article_title='A Paz que Excede',
                block_order=order,
            ),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['article_title'], 'A Paz que Excede')
        self.assertEqual(resp.data['block_order'], order)

        upd = client.patch(
            reverse('folder-detail', args=[resp.data['id']]),
            {'block_order': order[::-1]},
            format='json',
        )
        self.assertEqual(upd.status_code, status.HTTP_200_OK)
        self.assertEqual(upd.data['block_order'], order[::-1])

    def test_list_filters_ordering_and_pagination(self):
        pastor = self._user('pastor@teste.com', ChurchMembership.Role.PASTOR)
        client = self._client(pastor)
        client.post(reverse('folder-list'), self._folder_payload(), format='json')
        client.post(
            reverse('folder-list'),
            self._folder_payload(
                tipo='MENSAL', title='Boletim de Outubro',
                reference_start='2026-10-01', reference_end='2026-10-31',
            ),
            format='json',
        )
        # Filtro por tipo.
        resp = client.get(reverse('folder-list'), {'tipo': 'MENSAL'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)
        # Filtro por mês/ano.
        resp = client.get(reverse('folder-list'), {'year': '2026', 'month': '10'})
        self.assertEqual(len(resp.data), 2)
        # Ordenação.
        resp = client.get(reverse('folder-list'), {'ordering': 'title'})
        titles = [f['title'] for f in resp.data]
        self.assertEqual(titles, sorted(titles))
        # Paginação.
        resp = client.get(
            reverse('folder-list'), {'paginate': '1', 'page_size': 1},
        )
        self.assertEqual(resp.data['count'], 2)
        self.assertEqual(len(resp.data['results']), 1)

    def test_delete_folder(self):
        pastor = self._user('pastor@teste.com', ChurchMembership.Role.PASTOR)
        client = self._client(pastor)
        created = client.post(
            reverse('folder-list'), self._folder_payload(), format='json',
        )
        resp = client.delete(reverse('folder-detail', args=[created.data['id']]))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(FolderCulto.objects.exists())

    def test_membro_denied(self):
        membro = self._user('membro@teste.com', ChurchMembership.Role.MEMBRO)
        client = self._client(membro)
        resp = client.get(reverse('folder-list'))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_isolated_by_church(self):
        pastor = self._user('pastor@teste.com', ChurchMembership.Role.PASTOR, church=self.church)
        client = self._client(pastor)
        client.post(reverse('folder-list'), self._folder_payload(), format='json')
        other_pastor = self._user('other@teste.com', ChurchMembership.Role.PASTOR, church=self.other)
        resp = self._client(other_pastor).get(reverse('folder-list'))
        self.assertEqual(resp.data, [])


class FolderSyncTests(BaseFolderTestCase):
    def setUp(self):
        super().setUp()
        self.pastor = self._user('pastor@teste.com', ChurchMembership.Role.PASTOR)
        self.client = self._client(self.pastor)
        self.folder = self.client.post(
            reverse('folder-list'), self._folder_payload(), format='json',
        ).data

    def _event(self, **kwargs):
        defaults = {
            'church': self.church,
            'title': 'Culto de Celebração',
            'category': CalendarEvent.Category.CULTO,
            'audience': CalendarEvent.Audience.GENERAL,
            'date': date(2026, 10, 7),
            'start_time': None,
            'end_time': None,
            'repeat_monthly': False,
            'repeat_weekly': False,
            'weekdays': [],
            'repeat_interval': 1,
        }
        defaults.update(kwargs)
        return CalendarEvent.objects.create(**defaults)

    def test_sync_single_event(self):
        self._event(date=date(2026, 10, 7), title='Culto de Quarta')
        resp = self.client.post(
            reverse('folder-sync-events', args=[self.folder['id']]),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['created'], 1)
        row = FolderCultoRow.objects.get()
        self.assertEqual(row.date, date(2026, 10, 7))
        self.assertEqual(row.service_type, 'CELEBRACAO')

    def test_sync_weekly_recurrence(self):
        self._event(
            date=date(2026, 10, 4), repeat_weekly=True, weekdays=[6],
        )
        resp = self.client.post(
            reverse('folder-sync-events', args=[self.folder['id']]),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        dates = sorted(FolderCultoRow.objects.values_list('date', flat=True))
        self.assertEqual(dates, [date(2026, 10, 11)])

    def test_sync_skips_financial_categories(self):
        self._event(
            title='Conta de luz',
            category=CalendarEvent.Category.BILL,
            date=date(2026, 10, 8),
        )
        resp = self.client.post(
            reverse('folder-sync-events', args=[self.folder['id']]),
        )
        self.assertEqual(resp.data['created'], 0)
        self.assertEqual(FolderCultoRow.objects.count(), 0)

    def test_sync_does_not_duplicate(self):
        self._event(date=date(2026, 10, 7), title='Reunião')
        url = reverse('folder-sync-events', args=[self.folder['id']])
        self.client.post(url)
        self.client.post(url)
        self.assertEqual(FolderCultoRow.objects.count(), 1)

    def test_sync_monthly_fixed_day(self):
        self._event(
            title='Culto do Dia 15',
            repeat_monthly=True,
            month=10,
            day=15,
        )
        folder = self.client.post(
            reverse('folder-list'),
            self._folder_payload(
                tipo='MENSAL',
                reference_start='2026-10-01',
                reference_end='2026-10-31',
            ),
            format='json',
        ).data
        resp = self.client.post(
            reverse('folder-sync-events', args=[folder['id']]),
        )
        self.assertEqual(resp.data['created'], 1)
        rows = list(FolderCultoRow.objects.filter(folder_id=folder['id']))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].date, date(2026, 10, 15))


class FolderBirthdaysTests(BaseFolderTestCase):
    def setUp(self):
        super().setUp()
        self.pastor = self._user('pastor@teste.com', ChurchMembership.Role.PASTOR)
        self.client = self._client(self.pastor)
        self.folder = self.client.post(
            reverse('folder-list'), self._folder_payload(), format='json',
        ).data

    def _member(self, name, birth_date, status_=Member.Status.ACTIVE):
        return Member.objects.create(
            church=self.church, name=name, birth_date=birth_date, status=status_,
        )

    def test_birthdays_in_period(self):
        self._member('Ana Dia 9', date(1990, 10, 9))
        self._member('Bia Fora', date(1990, 10, 20))
        self._member('Carla Fora Mês', date(1990, 11, 9))
        self._member('Duda Inativa', date(1992, 10, 9), status_=Member.Status.INACTIVE)
        resp = self.client.get(reverse('folder-birthdays', args=[self.folder['id']]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = [b['name'] for b in resp.data]
        self.assertEqual(names, ['Ana Dia 9'])
        self.assertEqual(resp.data[0]['day'], 9)


class FolderPreviewTests(BaseFolderTestCase):
    """Pré-visualização para o modal de criação (sem folder salvo)."""

    def setUp(self):
        super().setUp()
        self.pastor = self._user('pastor@teste.com', ChurchMembership.Role.PASTOR)
        self.client = self._client(self.pastor)

    def _event(self, **kwargs):
        defaults = {
            'church': self.church,
            'title': 'Culto de Quarta',
            'category': CalendarEvent.Category.CULTO,
            'audience': CalendarEvent.Audience.GENERAL,
            'date': date(2026, 10, 7),
            'start_time': None,
            'end_time': None,
            'repeat_monthly': False,
            'repeat_weekly': False,
            'weekdays': [],
            'repeat_interval': 1,
        }
        defaults.update(kwargs)
        return CalendarEvent.objects.create(**defaults)

    def test_preview_sync_events(self):
        self._event(date=date(2026, 10, 7), title='Culto de Quarta')
        self._event(
            title='Conta de luz',
            category=CalendarEvent.Category.BILL,
            date=date(2026, 10, 8),
        )
        resp = self.client.post(
            reverse('folder-preview-sync-events'),
            {'reference_start': '2026-10-05', 'reference_end': '2026-10-11'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['created'], 1)
        self.assertEqual(len(resp.data['rows']), 1)
        self.assertEqual(resp.data['rows'][0]['date'], '2026-10-07')
        self.assertEqual(resp.data['rows'][0]['service_type'], 'CELEBRACAO')
        self.assertFalse(FolderCultoRow.objects.exists())

    def test_preview_sync_events_requires_period(self):
        resp = self.client.post(
            reverse('folder-preview-sync-events'), {}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_preview_birthdays(self):
        Member.objects.create(
            church=self.church, name='Ana Dia 9',
            birth_date=date(1990, 10, 9), status=Member.Status.ACTIVE,
        )
        Member.objects.create(
            church=self.church, name='Bia Fora',
            birth_date=date(1990, 10, 20), status=Member.Status.ACTIVE,
        )
        resp = self.client.post(
            reverse('folder-preview-birthdays'),
            {'reference_start': '2026-10-05', 'reference_end': '2026-10-11'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = [b['name'] for b in resp.data]
        self.assertEqual(names, ['Ana Dia 9'])

    def test_preview_requires_folder_role(self):
        membro = self._user('membro@teste.com', ChurchMembership.Role.MEMBRO)
        resp = self._client(membro).post(
            reverse('folder-preview-sync-events'),
            {'reference_start': '2026-10-05', 'reference_end': '2026-10-11'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class PublicFolderTests(BaseFolderTestCase):
    def test_public_folder_unauthenticated(self):
        pastor = self._user('pastor@teste.com', ChurchMembership.Role.PASTOR)
        created = self._client(pastor).post(
            reverse('folder-list'),
            self._folder_payload(rows=[
                {
                    'service_type': 'CELEBRACAO', 'date': '2026-10-11',
                    'title': 'Culto', 'preacher': '', 'sort_order': 0,
                },
            ]),
            format='json',
        ).data
        resp = self._client().get(
            reverse('public-folder', args=[created['public_hash']]),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['id'], created['id'])
        self.assertEqual(resp.data['church']['name'], 'Igreja Teste')
        self.assertEqual(resp.data['church']['slug'], self.church.slug)
        self.assertEqual(resp.data['versiculo'], 'Filipenses 4:13')
        self.assertEqual(len(resp.data['rows']), 1)
        self.assertTrue(resp.data['default_qr']['cadastro']['url'])

    def test_public_folder_not_found(self):
        resp = self._client().get(
            reverse('public-folder', args=['hash-invalido']),
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_regenerate_hash(self):
        pastor = self._user('pastor@teste.com', ChurchMembership.Role.PASTOR)
        client = self._client(pastor)
        created = client.post(
            reverse('folder-list'), self._folder_payload(), format='json',
        ).data
        resp = client.post(
            reverse('folder-regenerate-hash', args=[created['id']]),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertNotEqual(resp.data['public_hash'], created['public_hash'])