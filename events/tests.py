from datetime import date, timedelta

from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import ChurchMembership, Member
from accounts.tests import BaseChurchTestCase
from finance.models import CalendarEvent

from .models import (
    Event,
    EventCheckin,
    EventCheckinConfig,
    EventCheckinTeamMember,
    EventRegistration,
)
from . import services


class CheckinTestCase(BaseChurchTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.pastor = self._user(
            'checkin-pastor@teste.com',
            name='Pastor Check-in',
            church=self.sede,
            role=ChurchMembership.Role.PASTOR,
        )
        self.sec = self._user(
            'checkin-sec@teste.com',
            name='Secretaria Check-in',
            church=self.sede,
            role=ChurchMembership.Role.SECRETARIA,
        )
        self.member = Member.objects.create(
            church=self.sede,
            name='Ana Participante',
            birth_date='1990-01-01',
            status=Member.Status.ACTIVE,
            cpf='12345678901',
        )
        self.outsider = Member.objects.create(
            church=self.sede,
            name='Bruno Fora da Lista',
            birth_date='1991-01-01',
            status=Member.Status.ACTIVE,
        )
        self.other_church_member = Member.objects.create(
            church=self.congregation,
            name='Carla Outra Igreja',
            birth_date='1992-01-01',
            status=Member.Status.ACTIVE,
        )
        self.event = CalendarEvent.objects.create(
            church=self.sede,
            title='Conferência IDB',
            category=CalendarEvent.Category.EVENT,
            audience=CalendarEvent.Audience.GENERAL,
            date=date(2026, 11, 26),
            start_time='09:00:00',
        )
        self.event.members.add(self.member)
        self.config = EventCheckinConfig.objects.create(
            event=self.event,
            church=self.sede,
            mode=EventCheckinConfig.Mode.ONCE,
        )
        services.generate_sessions(self.config)
        self.session = self.config.sessions.first()

    def public_client(self):
        return APIClient()

    def auth_client(self, user=None):
        return self._client(user or self.pastor)

    def public_url(self, token=None):
        return reverse(
            'public-event-checkin',
            args=[token or self.session.token],
        )

    def scan(self, code, token=None, client=None, **extra):
        payload = {'code': code, **extra}
        return (client or self.public_client()).post(
            self.public_url(token), payload, format='json'
        )

    def test_create_config_generates_once_session(self):
        event = CalendarEvent.objects.create(
            church=self.sede,
            title='Retiro',
            audience=CalendarEvent.Audience.GENERAL,
            date=date(2026, 12, 20),
        )
        resp = self.auth_client().post(
            reverse('event-checkin-config-list'),
            {'event': event.pk, 'mode': EventCheckinConfig.Mode.ONCE},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        config = EventCheckinConfig.objects.get(event=event)
        self.assertEqual(config.sessions.count(), 1)

    def test_generate_per_day_sessions_from_recurrence(self):
        event = CalendarEvent.objects.create(
            church=self.sede,
            title='Culto Semanal',
            audience=CalendarEvent.Audience.GENERAL,
            date=date(2026, 11, 2),
            repeat_weekly=True,
            weekdays=[0],
            repeat_end_date=date(2026, 11, 16),
        )
        config = EventCheckinConfig.objects.create(
            event=event,
            church=self.sede,
            mode=EventCheckinConfig.Mode.PER_DAY,
        )
        created, total = services.generate_sessions(config)
        self.assertEqual(created, 3)
        self.assertEqual(total, 3)

    def test_public_get_returns_minimal_session_metadata(self):
        self.config.team_members.create(
            church=self.sede,
            member=self.member,
            role=EventCheckinTeamMember.Role.LEAD,
        )
        resp = self.public_client().get(self.public_url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['event_title'], 'Conferência IDB')
        self.assertEqual(resp.data['team'][0]['name'], 'Ana Participante')
        self.assertNotIn('cpf', str(resp.data))

    def test_public_scan_registers_once_and_reports_duplicate(self):
        first = self.scan(self.member.qr_hash)
        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        self.assertTrue(first.data['valid'])
        self.assertEqual(first.data['result'], 'REGISTERED')
        self.assertEqual(first.data['person']['name'], 'Ana Participante')
        self.assertNotIn('cpf', str(first.data))

        second = self.scan(self.member.qr_hash.lower())
        self.assertEqual(second.data['result'], 'ALREADY')
        self.assertTrue(second.data['already'])
        self.assertEqual(EventCheckin.objects.count(), 1)

    def test_public_scan_marks_outside_roster(self):
        resp = self.scan(self.outsider.qr_hash)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['in_roster'] is False)
        checkin = EventCheckin.objects.get(member=self.outsider)
        self.assertFalse(checkin.in_roster)

    def test_public_scan_rejects_member_from_other_church(self):
        resp = self.scan(self.other_church_member.qr_hash)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data['code'], 'wrong_church')
        self.assertEqual(EventCheckin.objects.count(), 0)

    def test_public_scan_rejects_invalid_code(self):
        resp = self.scan('INVALID1')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data['code'], 'invalid_code')

    def test_closed_session_rejects_scan(self):
        self.config.checkin_closes_at = self.config.created_at + timedelta(days=-2)
        self.config.save(update_fields=['checkin_closes_at'])
        resp = self.scan(self.member.qr_hash)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data['code'], 'session_closed')

    def test_regenerate_token_invalidates_old_link(self):
        old_token = self.session.token
        resp = self.auth_client().post(
            reverse(
                'event-checkin-session-regenerate-token',
                args=[self.session.pk],
            )
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertNotEqual(resp.data['token'], old_token)
        self.assertEqual(
            self.public_client().get(self.public_url(old_token)).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.public_client().get(self.public_url(resp.data['token'])).status_code,
            status.HTTP_200_OK,
        )

    def test_team_manual_checkin_summary_and_export(self):
        team = self.config.team_members.create(
            church=self.sede,
            member=self.outsider,
            role=EventCheckinTeamMember.Role.OPERATOR,
        )
        resp = self.auth_client().post(
            reverse('event-checkin-manual'),
            {
                'session': self.session.pk,
                'member': self.outsider.pk,
                'operator': team.pk,
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data['checkin']['source'], 'MANUAL')

        summary = self.auth_client().get(
            reverse('event-checkin-summary'),
            {'session': self.session.pk},
        )
        self.assertEqual(summary.status_code, status.HTTP_200_OK)
        self.assertEqual(summary.data['checked_count'], 1)
        self.assertEqual(summary.data['outside_roster_count'], 1)
        self.assertEqual(summary.data['expected_count'], 1)

        exported = self.auth_client().get(
            reverse('event-checkin-export'),
            {'session': self.session.pk},
        )
        self.assertEqual(exported.status_code, status.HTTP_200_OK)
        self.assertNotIn('12345678901', exported.content.decode('utf-8-sig'))

    def test_checkin_list_filters_search_ordering_and_pagination(self):
        for name in ('Ana', 'Bruno', 'Carla'):
            member = Member.objects.create(
                church=self.sede,
                name=name,
                birth_date='1990-01-01',
            )
            services.register_checkin(self.session, member, source='MANUAL')
        resp = self.auth_client().get(
            reverse('event-checkin-list'),
            {'session': self.session.pk, 'ordering': 'member_name', 'page_size': 2},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 3)
        self.assertEqual(len(resp.data['results']), 2)
        self.assertEqual(resp.data['results'][0]['member']['name'], 'Ana')

        searched = self.auth_client().get(
            reverse('event-checkin-list'),
            {'session': self.session.pk, 'search': 'Bru'},
        )
        self.assertEqual(searched.data['count'], 1)

    def test_cancel_keeps_audit_and_summary_excludes_cancelled(self):
        result = services.register_checkin(self.session, self.member)
        checkin = result['checkin']
        resp = self.auth_client().post(
            reverse('event-checkin-cancel', args=[checkin.pk])
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        checkin.refresh_from_db()
        self.assertEqual(checkin.status, EventCheckin.Status.CANCELLED)
        self.assertTrue(
            checkin.session.attempts.filter(
                member=self.member, result='CANCELLED'
            ).exists()
        )
        summary = self.auth_client().get(reverse('event-checkin-summary'))
        self.assertEqual(summary.data['checked_count'], 0)

    def test_member_role_cannot_manage_checkin(self):
        plain = self._user(
            'checkin-member@teste.com',
            name='Membro Comum',
            church=self.sede,
            role=ChurchMembership.Role.MEMBRO,
        )
        resp = self.auth_client(plain).get(reverse('event-checkin-config-list'))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        resp = self.auth_client(plain).get(
            reverse('admin-church-event-checkin-config-list', args=[self.sede.pk])
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_public_scan_throttled(self):
        cache.clear()
        self.public_client()
        client = self.public_client()
        last = None
        for _ in range(31):
            last = client.post(
                self.public_url(),
                {'code': 'INVALID1'},
                format='json',
            )
        self.assertEqual(last.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class EventRegistrationTestCase(BaseChurchTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.sec = self._user(
            'checkin-reg-sec@teste.com',
            name='Secretaria Inscrições',
            church=self.sede,
            role=ChurchMembership.Role.SECRETARIA,
        )
        self.member = Member.objects.create(
            church=self.sede,
            name='Ana Inscrita',
            birth_date='1990-01-01',
            status=Member.Status.ACTIVE,
        )
        self.outsider = Member.objects.create(
            church=self.sede,
            name='Bruno Inscrito',
            birth_date='1991-01-01',
            status=Member.Status.ACTIVE,
        )
        self.calendar_event = CalendarEvent.objects.create(
            church=self.sede,
            title='Conferência com inscrições',
            category=CalendarEvent.Category.EVENT,
            audience=CalendarEvent.Audience.GENERAL,
            date=date(2026, 11, 26),
            start_time='09:00:00',
        )
        self.calendar_event.members.add(self.member)
        self.config = EventCheckinConfig.objects.create(
            event=self.calendar_event,
            church=self.sede,
            mode=EventCheckinConfig.Mode.ONCE,
        )
        services.generate_sessions(self.config)
        self.session = self.config.sessions.first()
        self.event = services.ensure_event(self.calendar_event, self.sede)

    def auth_client(self, user=None):
        return self._client(user or self.sec)

    def registration_url(self, token=None):
        return reverse(
            'public-event-registration',
            args=[token or self.event.registration_token],
        )

    def test_standalone_event_creates_registration_link_and_sessions(self):
        resp = self.auth_client().post(
            reverse('event-list'),
            {
                'source': Event.Source.STANDALONE,
                'title': 'Evento Avulso',
                'date': '2026-12-20',
                'start_time': '10:00:00',
                'location': 'Auditório',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        event = Event.objects.get(pk=resp.data['id'])
        self.assertTrue(event.registration_token)
        self.assertTrue(resp.data['registration_url'].endswith(event.registration_token))

        enabled = self.auth_client().post(
            reverse('event-enable-checkin', args=[event.pk]),
            {'mode': EventCheckinConfig.Mode.ONCE},
            format='json',
        )
        self.assertEqual(enabled.status_code, status.HTTP_200_OK, enabled.data)
        self.assertEqual(event.checkin_sessions.count(), 1)

        shared = self.auth_client().get(
            reverse('event-share-registration', args=[event.pk])
        )
        self.assertEqual(shared.status_code, status.HTTP_200_OK)
        self.assertEqual(shared.data['token'], event.registration_token)

        old_token = event.registration_token
        regenerated = self.auth_client().post(
            reverse('event-regenerate-registration-token', args=[event.pk])
        )
        self.assertEqual(regenerated.status_code, status.HTTP_200_OK)
        self.assertNotEqual(regenerated.data['token'], old_token)
        self.assertEqual(APIClient().get(self.registration_url(old_token)).status_code,
                         status.HTTP_404_NOT_FOUND)
        self.assertEqual(APIClient().get(
            self.registration_url(regenerated.data['token'])
        ).status_code, status.HTTP_200_OK)

    def test_event_list_filters_by_month_and_year_with_pagination(self):
        for title, event_date in (
            ('Evento dezembro', '2026-12-10'),
            ('Evento ano anterior', '2025-11-10'),
        ):
            created = self.auth_client().post(
                reverse('event-list'),
                {
                    'source': Event.Source.STANDALONE,
                    'title': title,
                    'date': event_date,
                },
                format='json',
            )
            self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)

        response = self.auth_client().get(
            reverse('event-list'), {'month': 11, 'year': 2026, 'page_size': 1}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(len(response.data['results']), 1)

        response = self.auth_client().get(
            reverse('event-list'), {'month': 12, 'year': 2026}
        )
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['title'], 'Evento dezembro')

        response = self.auth_client().get(reverse('event-list'), {'year': 2025})
        self.assertEqual(response.data['count'], 1)

    def test_public_registration_creates_external_registration_and_reuses_contact(self):
        meta = APIClient().get(self.registration_url())
        self.assertEqual(meta.status_code, status.HTTP_200_OK)
        self.assertEqual(meta.data['event_id'], self.event.pk)
        self.assertEqual(meta.data['church_name'], self.sede.name)
        self.assertTrue(meta.data['is_open'])

        payload = {
            'name': 'Visitante Silva',
            'phone': '(61) 99999-0001',
            'email': 'visitante@example.com',
        }
        created = APIClient().post(self.registration_url(), payload, format='json')
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        self.assertTrue(created.data['created'])
        self.assertTrue(created.data['code'].startswith('EVT'))
        self.assertTrue(created.data['qr_url'])
        self.assertEqual(
            EventRegistration.objects.filter(event=self.event).count(), 1
        )

        repeated = APIClient().post(
            self.registration_url(),
            {**payload, 'name': 'Visitante Outro Nome'},
            format='json',
        )
        self.assertEqual(repeated.status_code, status.HTTP_200_OK)
        self.assertFalse(repeated.data['created'])
        self.assertTrue(repeated.data['already'])
        self.assertEqual(repeated.data['code'], created.data['code'])
        self.assertEqual(
            EventRegistration.objects.filter(event=self.event).count(), 1
        )

        self.event.registration_token_is_active = False
        self.event.save(update_fields=['registration_token_is_active'])
        self.assertEqual(
            APIClient().get(self.registration_url()).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_manager_can_add_and_manage_member_registrations(self):
        created = self.auth_client().post(
            reverse('event-registration-list'),
            {
                'event': self.event.pk,
                'kind': EventRegistration.Kind.MEMBER,
                'member': self.outsider.pk,
            },
            format='json',
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        self.assertEqual(created.data['source'], 'MANUAL')
        self.assertTrue(created.data['code'].startswith('EVT'))

        bulk = self.auth_client().post(
            reverse('event-registration-bulk-members'),
            {'event': self.event.pk, 'members': [self.member.pk]},
            format='json',
        )
        self.assertEqual(bulk.status_code, status.HTTP_201_CREATED, bulk.data)
        self.assertEqual(bulk.data['created'], 1)
        self.assertEqual(EventRegistration.objects.filter(event=self.event).count(), 2)

        cancelled = self.auth_client().post(
            reverse('event-registration-cancel', args=[created.data['id']])
        )
        self.assertEqual(cancelled.status_code, status.HTTP_200_OK)
        self.assertEqual(cancelled.data['status'], EventRegistration.Status.CANCELLED)

        old_code = created.data['code']
        regenerated = self.auth_client().post(
            reverse('event-registration-regenerate-code', args=[created.data['id']])
        )
        self.assertEqual(regenerated.status_code, status.HTTP_200_OK)
        self.assertNotEqual(regenerated.data['code'], old_code)

    def test_manager_can_bulk_add_all_active_church_members(self):
        bulk = self.auth_client().post(
            reverse('event-registration-bulk-members'),
            {'event': self.event.pk, 'all_members': True},
            format='json',
        )
        self.assertEqual(bulk.status_code, status.HTTP_201_CREATED, bulk.data)
        expected = Member.objects.filter(
            church=self.sede, status=Member.Status.ACTIVE
        ).count()
        self.assertEqual(bulk.data['created'], expected)
        self.assertEqual(
            EventRegistration.objects.filter(event=self.event).count(), expected
        )

    def test_external_registration_qr_can_check_in(self):
        registration = APIClient().post(
            self.registration_url(),
            {'name': 'Visitante Check-in', 'phone': '61988887777'},
            format='json',
        )
        self.assertEqual(registration.status_code, status.HTTP_201_CREATED)

        scanned = APIClient().post(
            reverse('public-event-checkin', args=[self.session.token]),
            {'code': registration.data['code']},
            format='json',
        )
        self.assertEqual(scanned.status_code, status.HTTP_200_OK, scanned.data)
        self.assertEqual(scanned.data['result'], 'REGISTERED')
        self.assertTrue(scanned.data['in_roster'])
        self.assertEqual(scanned.data['person']['kind'], 'EXTERNAL')

        rows = self.auth_client().get(
            reverse('event-checkin-list'),
            {'session': self.session.pk},
        )
        self.assertEqual(rows.status_code, status.HTTP_200_OK)
        self.assertEqual(rows.data['count'], 1)
        self.assertEqual(rows.data['results'][0]['registration_kind'], 'Externo')
        self.assertEqual(rows.data['results'][0]['code'], registration.data['code'])

        summary = self.auth_client().get(
            reverse('event-checkin-summary'),
            {'session': self.session.pk},
        )
        self.assertEqual(summary.data['checked_count'], 1)
        self.assertEqual(summary.data['expected_count'], 2)

    def test_member_role_cannot_manage_events_or_registrations(self):
        plain = self._user(
            'checkin-reg-member@teste.com',
            name='Membro Comum',
            church=self.sede,
            role=ChurchMembership.Role.MEMBRO,
        )
        for route in ('event-list', 'event-registration-list'):
            resp = self.auth_client(plain).get(reverse(route))
            self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
