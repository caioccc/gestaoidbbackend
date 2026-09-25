"""Testes de Ownership + Governança do repertório e de setlists.

Critérios de aceite:
- Usuário comum (MÚSICO, sem ser o autor) → 403 em PATCH/DELETE de música.
- Autor → 200/204 na própria música.
- PASTOR e superuser → 200 em músicas alheias (e nas sem autor).
- Cadastro de músicas aberto a qualquer membro (POST → 201, created_by correto).
- LOUVOR perde edição/exclusão de músicas alheias e de setlists.
- Setlists de banda: criação continua com gestores; editar/excluir só PASTOR/ADMIN.
- Setlist do culto (manage_setlist): só PASTOR/ADMIN.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import Church, ChurchMembership
from music.models import BandSetlist, Song, VolunteerRoster, WorshipSetlist
from music.serializers import SongSerializer
from music.views import (
    BandSetlistViewSet,
    SongViewSet,
    VolunteerRosterViewSet,
)

User = get_user_model()


class SongOwnershipTestCase(TestCase):
    def setUp(self):
        self.church = Church.objects.create(
            name='Igreja Ownership Teste',
            church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE',
            is_approved=True,
            city='Campina Grande',
            state='PB',
        )
        self.author = self._member('autor@teste.com', 'Autor', ChurchMembership.Role.MUSICO)
        self.other = self._member('outro@teste.com', 'Outro', ChurchMembership.Role.MUSICO)
        self.louvore = self._member('louvore@teste.com', 'Louvore', ChurchMembership.Role.LOUVOR)
        self.pastor = self._member('pastor@teste.com', 'Pastor', ChurchMembership.Role.PASTOR)
        self.staff = User.objects.create_superuser(
            email='admin@teste.com', password='admin', church=self.church,
        )
        self.rf = APIRequestFactory()

    def _member(self, email, name, role):
        user = User.objects.create(
            email=email, name=name, church=self.church, is_active=True,
        )
        ChurchMembership.objects.create(
            user=user, church=self.church, role=role,
        )
        return user

    def _song(self, **kwargs):
        defaults = {
            'church': self.church, 'title': 'Canção A',
            'youtube_id': 'aaaaaaaaaaa', 'created_by': self.author,
        }
        defaults.update(kwargs)
        return Song.objects.create(**defaults)

    def _call(self, mapping, user, path, data=None):
        request = getattr(self.rf, mapping['method'])(path, data=data or {}, format='json')
        force_authenticate(request, user=user)
        view = SongViewSet.as_view({mapping['verb']: mapping['action']})
        return view(request, pk=path.rsplit('/', 2)[-2] if '/songs/' in path else None) if mapping.get('with_pk') else view(request)

    def test_other_musico_patch_forbidden(self):
        song = self._song()
        request = self.rf.patch(
            f'/api/music/songs/{song.id}/',
            data={'title': 'X', 'artist': 'Y'}, format='json',
        )
        force_authenticate(request, user=self.other)
        resp = SongViewSet.as_view({'patch': 'partial_update'})(request, pk=song.id)
        self.assertEqual(resp.status_code, 403)
        self.assertIn('criador', resp.data['detail'])

    def test_other_musico_delete_forbidden(self):
        song = self._song()
        request = self.rf.delete(f'/api/music/songs/{song.id}/')
        force_authenticate(request, user=self.other)
        resp = SongViewSet.as_view({'delete': 'destroy'})(request, pk=song.id)
        self.assertEqual(resp.status_code, 403)

    def test_author_can_patch(self):
        song = self._song()
        request = self.rf.patch(
            f'/api/music/songs/{song.id}/',
            data={'title': song.title, 'artist': 'Yes'}, format='json',
        )
        force_authenticate(request, user=self.author)
        resp = SongViewSet.as_view({'patch': 'partial_update'})(request, pk=song.id)
        self.assertEqual(resp.status_code, 200)
        song.refresh_from_db()
        self.assertEqual(song.artist, 'Yes')

    def test_author_can_delete(self):
        song = self._song()
        request = self.rf.delete(f'/api/music/songs/{song.id}/')
        force_authenticate(request, user=self.author)
        resp = SongViewSet.as_view({'delete': 'destroy'})(request, pk=song.id)
        self.assertEqual(resp.status_code, 204)

    def test_pastor_can_patch_others(self):
        song = self._song()
        request = self.rf.patch(
            f'/api/music/songs/{song.id}/',
            data={'title': song.title, 'bpm': 120}, format='json',
        )
        force_authenticate(request, user=self.pastor)
        resp = SongViewSet.as_view({'patch': 'partial_update'})(request, pk=song.id)
        self.assertEqual(resp.status_code, 200)

    def test_superuser_can_patch_others(self):
        song = self._song()
        request = self.rf.patch(
            f'/api/music/songs/{song.id}/',
            data={'title': song.title, 'bpm': 100}, format='json',
        )
        force_authenticate(request, user=self.staff)
        resp = SongViewSet.as_view({'patch': 'partial_update'})(request, pk=song.id)
        self.assertEqual(resp.status_code, 200)

    def test_louvore_cannot_patch_others(self):
        song = self._song()
        request = self.rf.patch(
            f'/api/music/songs/{song.id}/',
            data={'title': song.title, 'artist': 'X'}, format='json',
        )
        force_authenticate(request, user=self.louvore)
        resp = SongViewSet.as_view({'patch': 'partial_update'})(request, pk=song.id)
        self.assertEqual(resp.status_code, 403)

    def test_louvore_cannot_delete_others(self):
        song = self._song()
        request = self.rf.delete(f'/api/music/songs/{song.id}/')
        force_authenticate(request, user=self.louvore)
        resp = SongViewSet.as_view({'delete': 'destroy'})(request, pk=song.id)
        self.assertEqual(resp.status_code, 403)

    def test_musico_can_create_and_is_recorded_as_author(self):
        request = self.rf.post(
            '/api/music/songs/',
            data={'title': 'Nova do Músico', 'youtube_id': 'bbbbbbbbbbb'},
            format='json',
        )
        force_authenticate(request, user=self.other)
        resp = SongViewSet.as_view({'post': 'create'})(request)
        self.assertEqual(resp.status_code, 201)
        song = Song.objects.get(pk=resp.data['id'])
        self.assertEqual(song.created_by_id, self.other.id)
        self.assertEqual(song.church_id, self.church.id)

    def test_created_by_is_read_only(self):
        song = self._song()
        request = self.rf.patch(
            f'/api/music/songs/{song.id}/',
            data={'title': song.title, 'created_by': 1}, format='json',
        )
        force_authenticate(request, user=self.author)
        resp = SongViewSet.as_view({'patch': 'partial_update'})(request, pk=song.id)
        self.assertEqual(resp.status_code, 200)

    def test_musico_can_list_and_retrieve(self):
        self._song(title='Listável')
        for user in (self.other, self.louvore):
            list_req = self.rf.get('/api/music/songs/')
            force_authenticate(list_req, user=user)
            resp = SongViewSet.as_view({'get': 'list'})(list_req)
            self.assertEqual(resp.status_code, 200)
            self.assertGreaterEqual(len(resp.data), 1)
            retr_req = self.rf.get(f'/api/music/songs/{resp.data[0]["id"]}/')
            force_authenticate(retr_req, user=user)
            resp = SongViewSet.as_view({'get': 'retrieve'})(retr_req, pk=resp.data[0]['id'])
            self.assertEqual(resp.status_code, 200)

    def test_musico_can_reprocess(self):
        song = self._song(chord_status=Song.ChordStatus.FAILED)
        request = self.rf.post(f'/api/music/songs/{song.id}/reprocess/')
        force_authenticate(request, user=self.other)
        resp = SongViewSet.as_view({'post': 'reprocess'})(request, pk=song.id)
        self.assertEqual(resp.status_code, 200)

    def test_serializer_exposes_created_by_and_can_edit(self):
        song = self._song(created_by=self.author)

        raw = self.rf.get(f'/api/music/songs/{song.id}/')
        force_authenticate(raw, user=self.author)
        data = SongSerializer(song, context={'request': Request(raw)}).data
        self.assertEqual(data['created_by'], self.author.id)
        self.assertEqual(data['created_by_name'], 'Autor')
        self.assertIs(data['can_edit'], True)

        raw2 = self.rf.get(f'/api/music/songs/{song.id}/')
        force_authenticate(raw2, user=self.other)
        data2 = SongSerializer(song, context={'request': Request(raw2)}).data
        self.assertIs(data2['can_edit'], False)

    def test_legacy_song_without_author_musico_forbidden(self):
        song = self._song(created_by=None)
        request = self.rf.patch(
            f'/api/music/songs/{song.id}/',
            data={'title': song.title, 'artist': 'X'}, format='json',
        )
        force_authenticate(request, user=self.other)
        resp = SongViewSet.as_view({'patch': 'partial_update'})(request, pk=song.id)
        self.assertEqual(resp.status_code, 403)

    def test_legacy_song_without_author_pastor_allowed(self):
        song = self._song(created_by=None)
        request = self.rf.patch(
            f'/api/music/songs/{song.id}/',
            data={'title': song.title, 'bpm': 90}, format='json',
        )
        force_authenticate(request, user=self.pastor)
        resp = SongViewSet.as_view({'patch': 'partial_update'})(request, pk=song.id)
        self.assertEqual(resp.status_code, 200)


class SetlistGovernanceTestCase(TestCase):
    def setUp(self):
        self.church = Church.objects.create(
            name='Igreja Setlist Teste',
            church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE',
            is_approved=True,
            city='Campina Grande',
            state='PB',
        )
        self.louvore = self._member('louvore@teste.com', 'Louvore', ChurchMembership.Role.LOUVOR)
        self.pastor = self._member('pastor@teste.com', 'Pastor', ChurchMembership.Role.PASTOR)
        self.musico = self._member('musico@teste.com', 'Músico', ChurchMembership.Role.MUSICO)
        self.rf = APIRequestFactory()
        self.song = Song.objects.create(
            church=self.church, title='Cantor da Fé', youtube_id='ccccccccccc',
        )

    def _member(self, email, name, role):
        user = User.objects.create(
            email=email, name=name, church=self.church, is_active=True,
        )
        ChurchMembership.objects.create(user=user, church=self.church, role=role)
        return user

    def _band_setlist(self, created_by=None):
        return BandSetlist.objects.create(
            church=self.church, date='2026-10-01',
            description='Culto', theme='Gratidão', created_by=created_by,
        )

    def _roster(self):
        return VolunteerRoster.objects.create(
            church=self.church, date='2026-10-10', theme='Culto',
        )

    def test_band_setlist_louvore_create_allowed(self):
        request = self.rf.post(
            '/api/music/setlists/',
            data={'date': '2026-11-01', 'description': 'Novo', 'items': []},
            format='json',
        )
        force_authenticate(request, user=self.louvore)
        resp = BandSetlistViewSet.as_view({'post': 'create'})(request)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['created_by'], self.louvore.id)

    def test_band_setlist_louvore_update_allowed(self):
        # Ownership: LOUVORE criou a própria setlist → pode editar (200).
        setlist = self._band_setlist(created_by=self.louvore)
        request = self.rf.put(
            f'/api/music/setlists/{setlist.id}/',
            data={
                'date': '2026-10-02', 'description': 'Culto 2',
                'theme': 'Xi', 'items': [],
            },
            format='json',
        )
        force_authenticate(request, user=self.louvore)
        resp = BandSetlistViewSet.as_view({'put': 'update'})(request, pk=setlist.id)
        self.assertEqual(resp.status_code, 200)
        setlist.refresh_from_db()
        self.assertEqual(setlist.theme, 'Xi')

    def test_band_setlist_louvore_delete_allowed(self):
        setlist = self._band_setlist(created_by=self.louvore)
        request = self.rf.delete(f'/api/music/setlists/{setlist.id}/')
        force_authenticate(request, user=self.louvore)
        resp = BandSetlistViewSet.as_view({'delete': 'destroy'})(request, pk=setlist.id)
        self.assertEqual(resp.status_code, 204)

    def test_band_setlist_pastor_update_allowed(self):
        setlist = self._band_setlist(created_by=self.louvore)
        request = self.rf.put(
            f'/api/music/setlists/{setlist.id}/',
            data={
                'date': '2026-10-02', 'description': 'Culto 2',
                'theme': 'Xi', 'items': [],
            },
            format='json',
        )
        force_authenticate(request, user=self.pastor)
        resp = BandSetlistViewSet.as_view({'put': 'update'})(request, pk=setlist.id)
        self.assertEqual(resp.status_code, 200)
        setlist.refresh_from_db()
        self.assertEqual(setlist.theme, 'Xi')

    def test_band_setlist_musico_list_allowed(self):
        self._band_setlist(created_by=self.louvore)
        request = self.rf.get('/api/music/setlists/')
        force_authenticate(request, user=self.musico)
        resp = BandSetlistViewSet.as_view({'get': 'list'})(request)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)

    def test_band_setlist_musico_update_denied_non_owner(self):
        # Ownership: a setlist de banda criada pelo LOUVORE pertence a ELE;
        # MUSICO (não-dono, não-PASTOR/ADMIN) recebe HTTP 403.
        setlist = self._band_setlist(created_by=self.louvore)
        request = self.rf.patch(
            f'/api/music/setlists/{setlist.id}/',
            data={'theme': 'Outrologia'}, format='json',
        )
        force_authenticate(request, user=self.musico)
        resp = BandSetlistViewSet.as_view({'patch': 'partial_update'})(request, pk=setlist.id)
        self.assertEqual(resp.status_code, 403)

    def test_band_setlist_musico_delete_denied_non_owner(self):
        setlist = self._band_setlist(created_by=self.louvore)
        request = self.rf.delete(f'/api/music/setlists/{setlist.id}/')
        force_authenticate(request, user=self.musico)
        resp = BandSetlistViewSet.as_view({'delete': 'destroy'})(request, pk=setlist.id)
        self.assertEqual(resp.status_code, 403)

    def test_worship_setlist_louvore_denied(self):
        roster = self._roster()
        request = self.rf.post(
            f'/api/music/rosters/{roster.id}/setlist/',
            data={'items': [{'song': self.song.id, 'order': 1}]}, format='json',
        )
        force_authenticate(request, user=self.louvore)
        resp = VolunteerRosterViewSet.as_view({'post': 'manage_setlist'})(request, pk=roster.id)
        self.assertEqual(resp.status_code, 403)

    def test_worship_setlist_louvore_delete_denied(self):
        roster = self._roster()
        WorshipSetlist.objects.create(roster=roster, created_by=self.pastor)
        request = self.rf.delete(f'/api/music/rosters/{roster.id}/setlist/')
        force_authenticate(request, user=self.louvore)
        resp = VolunteerRosterViewSet.as_view({'delete': 'manage_setlist'})(request, pk=roster.id)
        self.assertEqual(resp.status_code, 403)

    def test_worship_setlist_pastor_allowed(self):
        roster = self._roster()
        request = self.rf.post(
            f'/api/music/rosters/{roster.id}/setlist/',
            data={'items': [{'song': self.song.id, 'order': 1}]}, format='json',
        )
        force_authenticate(request, user=self.pastor)
        resp = VolunteerRosterViewSet.as_view({'post': 'manage_setlist'})(request, pk=roster.id)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['items']), 1)