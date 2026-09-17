"""Testes de regressão: permission corrigida, relacionamento church_memberships, my_rosters e bandas."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.db.models import Count
from django.db.utils import IntegrityError
from django.utils import timezone
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import Church, ChurchMembership
from music.models import (
    Band,
    BandSetlist,
    BandSetlistItem,
    Ministry,
    MinistryRole,
    RosterAssignment,
    SetlistItem,
    Song,
    VolunteerRoster,
    WorshipSetlist,
)
from music.serializers import SongSerializer

User = get_user_model()


class MusicFixturesMixin:
    def setUp(self):
        super().setUp()
        self.church = Church.objects.create(
            name='Igreja de Música Teste',
            church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE',
            is_approved=True,
            city='Campina Grande',
            state='PB',
        )
        self.user = self._user('music1@fix.com', 'Músico Um')
        self.user2 = self._user('noisem@fix.com', 'Voluntário Dois')
        self.rf = APIRequestFactory()

    def _user(self, email, name):
        user = User.objects.create(
            email=email, name=name, church=self.church, is_active=True,
        )
        user.set_password('S3nh@segura')
        user.save(update_fields=['password', 'is_active'])
        ChurchMembership.objects.create(
            user=user, church=self.church, role=ChurchMembership.Role.MUSICO,
        )
        return user

    def _rf(self, method, path, user=None):
        http = getattr(self.rf, method)(path)
        req = Request(http)
        req.user = user or self.user
        return req


class MusicManagerFixturesMixin(MusicFixturesMixin):
    def _user(self, email, name):
        user = User.objects.create(
            email=email, name=name, church=self.church, is_active=True,
        )
        user.set_password('S3nh@segura')
        user.save(update_fields=['password', 'is_active'])
        ChurchMembership.objects.create(
            user=user, church=self.church, role=ChurchMembership.Role.LOUVOR,
        )
        return user


class VolunteersEndpointTest(MusicFixturesMixin, TestCase):
    def test_volunteers_returns_users(self):
        from music.views import VolunteerRosterViewSet
        v = VolunteerRosterViewSet()
        v.request = self._rf('get', '/api/music/rosters/volunteers/')
        v.action = 'volunteers'
        v.format_kwarg = None
        resp = v.volunteers(v.request)
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.data, list)
        self.assertGreaterEqual(len(resp.data), 2)
        emails = [u['email'] for u in resp.data]
        self.assertIn('music1@fix.com', emails)
        self.assertIn('noisem@fix.com', emails)


class MyRostersEndpointTest(MusicFixturesMixin, TestCase):
    def test_my_rosters_empty(self):
        from music.views import VolunteerRosterViewSet
        v = VolunteerRosterViewSet()
        req = self._rf('get', '/api/music/rosters/my-rosters/?month=9999-01')
        v.request = req
        v.action = 'my_rosters'
        v.format_kwarg = None
        v.kwargs = {}
        resp = v.my_rosters(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, [])

    def test_my_rosters_returns_user_rosters(self):
        ministry = Ministry.objects.create(church=self.church, name='Louvor', color='#000000')
        role = MinistryRole.objects.create(ministry=ministry, name='Vocal')
        roster = VolunteerRoster.objects.create(
            church=self.church, date='2099-05-10', theme='Culto de Ações de Graças',
        )
        RosterAssignment.objects.create(
            roster=roster, ministry=ministry, role=role, user=self.user,
        )
        from music.views import VolunteerRosterViewSet
        v = VolunteerRosterViewSet()
        req = self._rf('get', '/api/music/rosters/my-rosters/?month=2099-05')
        v.request = req
        v.action = 'my_rosters'
        v.format_kwarg = None
        v.kwargs = {}
        resp = v.my_rosters(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['assignments'][0]['role_name'], 'Vocal')


class GetPermissionsTest(MusicFixturesMixin, TestCase):
    def test_permissions_returns_list_of_instances(self):
        from music.views import VolunteerRosterViewSet
        v = VolunteerRosterViewSet()
        v.request = self._rf('get', '/api/music/rosters/')
        v.action = 'volunteers'
        perms = v.get_permissions()
        self.assertIsInstance(perms, list)
        self.assertEqual(len(perms), 1)
        self.assertTrue(hasattr(perms[0], 'has_permission'))

    def test_my_rosters_action_uses_view_roles(self):
        from music.views import VolunteerRosterViewSet
        v = VolunteerRosterViewSet()
        v.request = self._rf('get', '/api/music/rosters/my-rosters/')
        v.action = 'my_rosters'
        perms = v.get_permissions()
        self.assertIsInstance(perms, list)
        self.assertEqual(len(perms), 1)


class BandModelTest(MusicFixturesMixin, TestCase):
    def test_band_creation_and_str(self):
        band = Band.objects.create(church=self.church, name='Águia da Paz', color='#0000ff')
        self.assertEqual(str(band), 'Águia da Paz')
        self.assertEqual(band.color, '#0000ff')

    def test_band_unique_constraint(self):
        Band.objects.create(church=self.church, name='Siloé')
        with self.assertRaises(IntegrityError):
            Band.objects.create(church=self.church, name='Siloé')

    def test_band_song_count_annotation(self):
        band = Band.objects.create(church=self.church, name='Siloé')
        Song.objects.create(church=self.church, band=band, title='M1', youtube_id='aaaaaaaaaaa')
        Song.objects.create(church=self.church, band=band, title='M2', youtube_id='bbbbbbbbbbb')
        Song.objects.create(church=self.church, title='M3', youtube_id='ccccccccccc')
        qs = Band.objects.filter(church=self.church).annotate(song_count=Count('songs'))
        counts = {b.name: b.song_count for b in qs}
        self.assertEqual(counts['Siloé'], 2)


class SongBandFilterTest(MusicFixturesMixin, TestCase):
    def test_songs_band_filter_returns_only_matching(self):
        band = Band.objects.create(church=self.church, name='Frutos')
        s1 = Song.objects.create(church=self.church, band=band, title='A', youtube_id='aaaaaaaaaaa')
        s2 = Song.objects.create(church=self.church, title='B', youtube_id='bbbbbbbbbbb')
        from music.views import SongViewSet
        v = SongViewSet()
        v.request = self._rf('get', f'/api/music/songs/?band={band.id}')
        v.action = 'list'
        ids = list(v.get_queryset().values_list('id', flat=True))
        self.assertIn(s1.id, ids)
        self.assertNotIn(s2.id, ids)


class BandSetlistTest(MusicManagerFixturesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.band = Band.objects.create(church=self.church, name='Siloé')
        self.song = Song.objects.create(church=self.church, title='M1', youtube_id='aaaaaaaaaaa')

    def test_model_creation_and_str(self):
        setlist = BandSetlist.objects.create(
            church=self.church, band=self.band, date='2099-06-01',
            description='Culto de domingo', theme='Páscoa',
        )
        BandSetlistItem.objects.create(
            setlist=setlist, song=self.song, order=1, custom_key='G',
        )
        self.assertIn('2099-06-01', str(setlist))
        self.assertEqual(setlist.items.count(), 1)
        self.assertEqual(str(setlist.items.first()), '1. M1')

    def test_viewset_create_with_items(self):
        from rest_framework.test import force_authenticate
        from music.views import BandSetlistViewSet
        data = {
            'band': self.band.id,
            'date': '2099-06-10',
            'description': 'Culto de Páscoa',
            'theme': 'Páscoa',
            'items': [{'song': self.song.id, 'order': 1, 'custom_key': 'G'}],
        }
        factory = APIRequestFactory()
        request = factory.post('/api/music/setlists/', data=data, format='json')
        force_authenticate(request, user=self.user)
        view = BandSetlistViewSet.as_view({'post': 'create'})
        resp = view(request)
        self.assertEqual(resp.status_code, 201)
        body = resp.data
        self.assertEqual(body['description'], 'Culto de Páscoa')
        self.assertEqual(len(body['items']), 1)
        self.assertEqual(body['items'][0]['song_title'], 'M1')
        self.assertEqual(body['items'][0]['custom_key'], 'G')

    def test_viewset_update_replaces_items(self):
        from rest_framework.test import force_authenticate
        from music.views import BandSetlistViewSet
        setlist = BandSetlist.objects.create(
            church=self.church, date='2099-06-02', description='Antiga',
        )
        BandSetlistItem.objects.create(setlist=setlist, song=self.song, order=1)
        second = Song.objects.create(church=self.church, title='M2', youtube_id='bbbbbbbbbbb')
        data = {
            'band': None,
            'date': '2099-06-15',
            'description': 'Nova',
            'theme': 'Ceia',
            'items': [{'song': second.id, 'order': 1}],
        }
        factory = APIRequestFactory()
        request = factory.put(f'/api/music/setlists/{setlist.id}/', data=data, format='json')
        force_authenticate(request, user=self.user)
        view = BandSetlistViewSet.as_view({'put': 'update'})
        resp = view(request, pk=setlist.id)
        self.assertEqual(resp.status_code, 200)
        setlist.refresh_from_db()
        self.assertEqual(setlist.description, 'Nova')
        self.assertEqual(setlist.items.count(), 1)
        self.assertEqual(setlist.items.first().song_id, second.id)

    def test_month_filter(self):
        from music.views import BandSetlistViewSet
        BandSetlist.objects.create(church=self.church, date='2099-06-01', description='A')
        BandSetlist.objects.create(church=self.church, date='2099-07-01', description='B')
        v = BandSetlistViewSet()
        req = self._rf('get', '/api/music/setlists/?month=2099-06')
        v.request = req
        v.action = 'list'
        qs = v.get_queryset()
        self.assertEqual(list(qs.values_list('description', flat=True)), ['A'])


class SongTimesPlayedTest(MusicManagerFixturesMixin, TestCase):
    """`times_played` e `last_played` devem ser calculados a partir dos setlists já realizados."""

    def setUp(self):
        super().setUp()
        self.song = Song.objects.create(
            church=self.church, title='M1', youtube_id='aaaaaaaaaaa',
        )
        self.today = timezone.localdate()

    def _song_serialized(self):
        from music.views import SongViewSet
        v = SongViewSet()
        v.request = self._rf('get', '/api/music/songs/')
        v.action = 'list'
        obj = v.get_queryset().get(pk=self.song.pk)
        return SongSerializer(obj).data

    def test_counts_only_past_band_setlists(self):
        past = BandSetlist.objects.create(
            church=self.church, date=self.today - timedelta(days=3),
            description='Passado',
        )
        BandSetlistItem.objects.create(setlist=past, song=self.song, order=1)
        future = BandSetlist.objects.create(
            church=self.church, date=self.today + timedelta(days=5),
            description='Futuro',
        )
        BandSetlistItem.objects.create(setlist=future, song=self.song, order=1)

        data = self._song_serialized()
        self.assertEqual(data['times_played'], 1)
        self.assertEqual(data['last_played'], self.today - timedelta(days=3))

    def test_counts_past_worship_setlist(self):
        roster = VolunteerRoster.objects.create(
            church=self.church, date=self.today - timedelta(days=1), theme='Culto',
        )
        setlist = WorshipSetlist.objects.create(roster=roster)
        SetlistItem.objects.create(setlist=setlist, song=self.song, order=1)

        data = self._song_serialized()
        self.assertEqual(data['times_played'], 1)
        self.assertEqual(data['last_played'], self.today - timedelta(days=1))

    def test_zero_when_no_past_setlists(self):
        data = self._song_serialized()
        self.assertEqual(data['times_played'], 0)
        self.assertIsNone(data['last_played'])


class SongHistoryTest(MusicManagerFixturesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.song = Song.objects.create(
            church=self.church, title='M1', youtube_id='aaaaaaaaaaa', church_key='Em',
        )

    def _history(self):
        from music.views import SongViewSet
        v = SongViewSet()
        req = self._rf('get', f'/api/music/songs/{self.song.id}/history/')
        v.request = req
        v.action = 'history'
        v.kwargs = {'pk': self.song.id}
        v.format_kwarg = None
        return v.history(req, pk=self.song.id)

    def test_history_includes_band_setlist(self):
        setlist = BandSetlist.objects.create(
            church=self.church, date='2026-09-06', description='culto teste',
        )
        BandSetlistItem.objects.create(setlist=setlist, song=self.song, order=1)

        resp = self._history()
        self.assertEqual(resp.status_code, 200)
        results = resp.data['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['date'], '2026-09-06')
        self.assertEqual(results[0]['name'], 'culto teste')
        self.assertEqual(results[0]['key'], 'Em')
        self.assertEqual(results[0]['kind'], 'band')

    def test_history_uses_custom_key_when_set(self):
        setlist = BandSetlist.objects.create(
            church=self.church, date='2026-09-06', description='culto teste',
        )
        BandSetlistItem.objects.create(
            setlist=setlist, song=self.song, order=1, custom_key='G#m',
        )

        results = self._history().data['results']
        self.assertEqual(results[0]['key'], 'G#m')