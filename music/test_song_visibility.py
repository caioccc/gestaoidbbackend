"""Testes de visibilidade (público/privado) de músicas e setlists de banda.

Critérios de aceite:
- `is_private` nasce False em-song e em setlist; `created_by` é preservado.
- Música/setlist privado aparece para o DONO e some dos demais (list + 404).
- Sempre invisível entre igrejas, mesmo sendo privado "para si".
- `?visibility=public|private` recorta a fatia sem vazar item de terceiro.
- Bloco 3D: setlist pública não aceita música privada (400).
- Caminho inverso: música pública não vira privada se estiver em setlist público.
- `band_stats` agrupa pela banda do setlist e zera quando a música é privada.
- `history` só conta setlist de banda e zera quando a música é privada.
- `manage_setlist` (setlist do culto) recusa música privada com 400, não 500.
- `check-youtube` continua com a busca global (sem filtro de visibilidade).
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import Church, ChurchMembership
from music.models import (
    Band,
    BandSetlist,
    BandSetlistItem,
    Song,
    VolunteerRoster,
)

User = get_user_model()


class VisibilityBase(TestCase):
    def setUp(self):
        self.church = Church.objects.create(
            name='Igreja Visibilidade Teste',
            church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE',
            is_approved=True,
            city='Campina Grande',
            state='PB',
        )
        self.other_church = Church.objects.create(
            name='Outra Igreja',
            church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE',
            is_approved=True,
            city='João Pessoa',
            state='PB',
        )
        self.author = self._member('autor@v.com', 'Autor')
        self.other = self._member('outro@v.com', 'Outro')
        self.pastor = self._member('pastor@v.com', 'Pastor', ChurchMembership.Role.PASTOR)
        self.outsider = User.objects.create(
            email='fora@v.com', name='Fora', church=self.other_church, is_active=True,
        )
        ChurchMembership.objects.create(
            user=self.outsider, church=self.other_church,
            role=ChurchMembership.Role.MUSICO,
        )
        self.rf = APIRequestFactory()

    def _member(self, email, name, role=ChurchMembership.Role.MUSICO):
        user = User.objects.create(
            email=email, name=name, church=self.church, is_active=True,
        )
        ChurchMembership.objects.create(user=user, church=self.church, role=role)
        return user

    def _song(self, created_by=None, is_private=False, title='Canção', church=None, **kw):
        defaults = {'youtube_id': 'aaaaaaaaaaa'}
        defaults.update(kw)
        return Song.objects.create(
            church=church or self.church,
            title=title,
            created_by=created_by if created_by is not None else self.author,
            is_private=is_private,
            **defaults,
        )

    def _band(self, name='Banda Alpha'):
        return Band.objects.create(church=self.church, name=name, color='#123456')

    def _setlist(self, created_by=None, is_private=False, date_=None, band=None,
                 description='Setlist Teste'):
        return BandSetlist.objects.create(
            church=self.church,
            date=date_ or (timezone.localdate() - timedelta(days=10)),
            description=description,
            created_by=created_by if created_by is not None else self.author,
            is_private=is_private,
            band=band,
        )

    def _add(self, setlist, song, order=1):
        return BandSetlistItem.objects.create(setlist=setlist, song=song, order=order)


class SongVisibilityTestCase(VisibilityBase):
    def _get(self, user, path='/api/music/songs/'):
        request = self.rf.get(path)
        force_authenticate(request, user=user)
        from music.views import SongViewSet
        return SongViewSet.as_view({'get': 'list'})(request)

    def _get_ids(self, user, path='/api/music/songs/'):
        resp = self._get(user, path)
        self.assertEqual(resp.status_code, 200)
        return [row['id'] for row in resp.data]

    def test_is_private_defaults_to_false(self):
        self.assertFalse(self._song().is_private)
        self.assertFalse(self._setlist().is_private)

    def test_public_song_visible_to_everyone_in_church(self):
        song = self._song(title='Pública')
        self.assertIn(song.id, self._get_ids(self.author))
        self.assertIn(song.id, self._get_ids(self.other))
        self.assertIn(song.id, self._get_ids(self.pastor))

    def test_private_song_only_visible_to_creator(self):
        song = self._song(title='Segredo', is_private=True)
        self.assertIn(song.id, self._get_ids(self.author))
        self.assertNotIn(song.id, self._get_ids(self.other))
        self.assertNotIn(song.id, self._get_ids(self.pastor))

    def test_private_song_never_visible_cross_church(self):
        song = self._song(title='Alheia', is_private=True, created_by=self.outsider)
        self.assertNotIn(song.id, self._get_ids(self.author))
        self.assertNotIn(song.id, self._get_ids(self.outsider))

    def test_private_song_retrieve_is_404_for_others(self):
        song = self._song(title='Segredo', is_private=True)
        from music.views import SongViewSet
        request = self.rf.get(f'/api/music/songs/{song.id}/')
        force_authenticate(request, user=self.other)
        resp = SongViewSet.as_view({'get': 'retrieve'})(request, pk=song.id)
        self.assertEqual(resp.status_code, 404)

        request = self.rf.get(f'/api/music/songs/{song.id}/')
        force_authenticate(request, user=self.author)
        resp = SongViewSet.as_view({'get': 'retrieve'})(request, pk=song.id)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['is_private'])

    def test_visibility_query_param(self):
        publica = self._song(title='Pública')
        privada = self._song(title='Privada', is_private=True)
        alheia = self._song(title='Alheia', is_private=True, created_by=self.other)

        public_ids = self._get_ids(self.author, '/api/music/songs/?visibility=public')
        self.assertIn(publica.id, public_ids)
        self.assertNotIn(privada.id, public_ids)
        self.assertNotIn(alheia.id, public_ids)

        private_ids = self._get_ids(self.author, '/api/music/songs/?visibility=private')
        self.assertIn(privada.id, private_ids)
        self.assertNotIn(publica.id, private_ids)
        self.assertNotIn(alheia.id, private_ids)

        all_ids = self._get_ids(self.author, '/api/music/songs/?visibility=all')
        self.assertIn(publica.id, all_ids)
        self.assertIn(privada.id, all_ids)
        self.assertNotIn(alheia.id, all_ids)

    def test_create_accepts_is_private_and_keeps_created_by(self):
        from music.views import SongViewSet
        request = self.rf.post(
            '/api/music/songs/',
            data={'title': 'Nova Privada', 'youtube_id': 'bbbbbbbbbbb', 'is_private': True},
            format='json',
        )
        force_authenticate(request, user=self.author)
        resp = SongViewSet.as_view({'post': 'create'})(request)
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.data['is_private'])
        song = Song.objects.get(pk=resp.data['id'])
        self.assertTrue(song.is_private)
        self.assertEqual(song.created_by_id, self.author.id)

    def test_create_defaults_to_public(self):
        from music.views import SongViewSet
        request = self.rf.post(
            '/api/music/songs/',
            data={'title': 'Nova Publica', 'youtube_id': 'ccccccccccc'},
            format='json',
        )
        force_authenticate(request, user=self.author)
        resp = SongViewSet.as_view({'post': 'create'})(request)
        self.assertEqual(resp.status_code, 201)
        self.assertFalse(resp.data['is_private'])

    def test_song_cannot_be_made_private_while_in_public_setlist(self):
        song = self._song(title='Em uso')
        self._add(self._setlist(description='Show deUME'), song)

        request = self.rf.patch(
            f'/api/music/songs/{song.id}/',
            data={'title': song.title, 'is_private': True},
            format='json',
        )
        force_authenticate(request, user=self.author)
        from music.views import SongViewSet
        resp = SongViewSet.as_view({'patch': 'partial_update'})(request, pk=song.id)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('is_private', resp.data)

    def test_song_can_be_made_private_after_leaving_public_setlists(self):
        song = self._song(title='Livre')
        request = self.rf.patch(
            f'/api/music/songs/{song.id}/',
            data={'title': song.title, 'is_private': True},
            format='json',
        )
        force_authenticate(request, user=self.author)
        from music.views import SongViewSet
        resp = SongViewSet.as_view({'patch': 'partial_update'})(request, pk=song.id)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['is_private'])


class BandSetlistVisibilityTestCase(VisibilityBase):
    def _get(self, user, path='/api/music/setlists/'):
        request = self.rf.get(path)
        force_authenticate(request, user=user)
        from music.views import BandSetlistViewSet
        return BandSetlistViewSet.as_view({'get': 'list'})(request)

    def _get_ids(self, user, path='/api/music/setlists/'):
        resp = self._get(user, path)
        self.assertEqual(resp.status_code, 200)
        return [row['id'] for row in resp.data]

    def test_private_setlist_only_visible_to_creator(self):
        public = self._setlist(description='Público')
        private = self._setlist(description='Privado', is_private=True)
        ids = self._get_ids(self.author)
        self.assertIn(public.id, ids)
        self.assertIn(private.id, ids)

        ids = self._get_ids(self.other)
        self.assertIn(public.id, ids)
        self.assertNotIn(private.id, ids)
        self.assertNotIn(private.id, self._get_ids(self.pastor))

    def test_visibility_query_param(self):
        public = self._setlist(description='Público')
        private = self._setlist(description='Privado', is_private=True)
        alheio = self._setlist(description='Alheio', is_private=True, created_by=self.other)

        public_ids = self._get_ids(self.author, '/api/music/setlists/?visibility=public')
        self.assertIn(public.id, public_ids)
        self.assertNotIn(private.id, public_ids)
        self.assertNotIn(alheio.id, public_ids)

        private_ids = self._get_ids(self.author, '/api/music/setlists/?visibility=private')
        self.assertIn(private.id, private_ids)
        self.assertNotIn(public.id, private_ids)
        self.assertNotIn(alheio.id, private_ids)

    def test_private_setlist_retrieve_is_404_for_others(self):
        private = self._setlist(is_private=True)
        from music.views import BandSetlistViewSet
        request = self.rf.get(f'/api/music/setlists/{private.id}/')
        force_authenticate(request, user=self.other)
        resp = BandSetlistViewSet.as_view({'get': 'retrieve'})(request, pk=private.id)
        self.assertEqual(resp.status_code, 404)

        request = self.rf.get(f'/api/music/setlists/{private.id}/')
        force_authenticate(request, user=self.author)
        resp = BandSetlistViewSet.as_view({'get': 'retrieve'})(request, pk=private.id)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['is_private'])

    def _post(self, user, data):
        from music.views import BandSetlistViewSet
        request = self.rf.post('/api/music/setlists/', data=data, format='json')
        force_authenticate(request, user=user)
        return BandSetlistViewSet.as_view({'post': 'create'})(request)

    def test_public_setlist_rejects_private_song(self):
        private_song = self._song(title='Segredo', is_private=True)
        resp = self._post(self.author, {
            'date': '2026-10-01', 'description': 'Show',
            'is_private': False, 'items': [{'song': private_song.id}],
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn('items', resp.data)

    def test_private_setlist_accepts_private_song(self):
        private_song = self._song(title='Segredo', is_private=True)
        resp = self._post(self.author, {
            'date': '2026-10-01', 'description': 'Show',
            'is_private': True, 'items': [{'song': private_song.id}],
        })
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.data['is_private'])
        self.assertEqual(len(resp.data['items']), 1)

    def test_setlist_rejects_private_song_of_another_user(self):
        foreign = self._song(title='Alheia', is_private=True, created_by=self.other)
        resp = self._post(self.author, {
            'date': '2026-10-01', 'description': 'Show',
            'is_private': True, 'items': [{'song': foreign.id}],
        })
        self.assertEqual(resp.status_code, 400)

    def test_setlist_rejects_song_from_another_church(self):
        foreign = self._song(title='Outra', church=self.other_church, created_by=self.outsider)
        resp = self._post(self.author, {
            'date': '2026-10-01', 'description': 'Show',
            'is_private': True, 'items': [{'song': foreign.id}],
        })
        self.assertEqual(resp.status_code, 400)

    def test_private_setlist_cannot_be_made_public_while_holding_private_song(self):
        private_song = self._song(title='Segredo', is_private=True)
        setlist = self._setlist(is_private=True)
        self._add(setlist, private_song)

        from music.views import BandSetlistViewSet
        request = self.rf.put(
            f'/api/music/setlists/{setlist.id}/',
            data={
                'date': '2026-10-01', 'description': 'Show', 'is_private': False,
                'items': [{'song': private_song.id}],
            },
            format='json',
        )
        force_authenticate(request, user=self.author)
        resp = BandSetlistViewSet.as_view({'put': 'update'})(request, pk=setlist.id)
        self.assertEqual(resp.status_code, 400)

    def test_partial_update_cannot_publish_setlist_with_stored_private_song(self):
        """PATCH sem `items` mantém os itens gravados, então virar público
        continua proibido — o corpo não pode servir de atalho."""
        private_song = self._song(title='Segredo', is_private=True)
        setlist = self._setlist(is_private=True)
        self._add(setlist, private_song)

        from music.views import BandSetlistViewSet
        request = self.rf.patch(
            f'/api/music/setlists/{setlist.id}/',
            data={
                'date': '2026-10-01', 'description': 'Show', 'is_private': False,
            },
            format='json',
        )
        force_authenticate(request, user=self.author)
        resp = BandSetlistViewSet.as_view({'patch': 'partial_update'})(
            request, pk=setlist.id
        )
        self.assertEqual(resp.status_code, 400)
        setlist.refresh_from_db()
        self.assertTrue(setlist.is_private)

    def test_partial_update_keeps_stored_items_of_public_setlist(self):
        """Contrapartida: setlist pública sem música privada pode ser editada
        (ex.: trocar a data) sem reenviar `items`."""
        public_song = self._song(title='Pública')
        setlist = self._setlist(is_private=False)
        self._add(setlist, public_song)

        from music.views import BandSetlistViewSet
        request = self.rf.patch(
            f'/api/music/setlists/{setlist.id}/',
            data={'date': '2026-10-02', 'description': 'Show Atualizado'},
            format='json',
        )
        force_authenticate(request, user=self.author)
        resp = BandSetlistViewSet.as_view({'patch': 'partial_update'})(
            request, pk=setlist.id
        )
        self.assertEqual(resp.status_code, 200)
        setlist.refresh_from_db()
        self.assertEqual(setlist.description, 'Show Atualizado')
        self.assertEqual(setlist.items.count(), 1)


class BandStatsAndHistoryTestCase(VisibilityBase):
    def test_band_stats_grouped_by_setlist_band(self):
        song = self._song(title='Hit')
        alpha = self._band('Alpha')
        beta = self._band('Beta')
        self._add(self._setlist(band=alpha), song)
        self._add(self._setlist(band=alpha, date_=timezone.localdate() - timedelta(days=20)), song)
        self._add(self._setlist(band=beta), song)

        from music.serializers import SongSerializer
        song.refresh_from_db()
        stats = SongSerializer(song).data['band_stats']
        by_band = {s['band_name']: s['times_played'] for s in stats}
        self.assertEqual(by_band, {'Alpha': 2, 'Beta': 1})

    def test_band_stats_ignores_future_setlists(self):
        song = self._song(title='Futura')
        self._add(
            self._setlist(band=self._band('Alpha'), date_=timezone.localdate() + timedelta(days=30)),
            song,
        )
        from music.serializers import SongSerializer
        song.refresh_from_db()
        self.assertEqual(SongSerializer(song).data['band_stats'], [])

    def test_band_stats_empty_for_private_song(self):
        song = self._song(title='Segredo', is_private=True)
        self._add(self._setlist(band=self._band('Alpha')), song)
        from music.serializers import SongSerializer
        song.refresh_from_db()
        self.assertEqual(SongSerializer(song).data['band_stats'], [])

    def test_band_stats_ignores_other_users_private_setlists(self):
        """A contagem não pode revelar a existência de setlist privado alheio
        (nem indiretamente, via `ordering=times_played`)."""
        from rest_framework.request import Request
        from rest_framework.test import force_authenticate as fa

        song = self._song(title='Hit')
        alpha = self._band('Alpha')
        self._add(self._setlist(band=alpha), song)
        self._add(
            self._setlist(band=alpha, created_by=self.other, is_private=True),
            song,
        )

        def api_request_for(user):
            """`force_authenticate` grava `_force_auth_user` no request cru e o
            `Request` do DRF só o lê na construção. Forçar na ordem errada deixa
            `request.user` como None e o recorte de visibilidade não seria
            exercitado de verdade."""
            raw = self.rf.get('/api/music/songs/')
            fa(raw, user=user)
            return Request(raw)

        from music.serializers import SongSerializer
        song.refresh_from_db()
        stats = SongSerializer(
            song, context={'request': api_request_for(self.author)}
        ).data['band_stats']
        self.assertEqual([s['times_played'] for s in stats], [1])

        listing = self.rf.get('/api/music/songs/?ordering=times_played&page=1')
        force_authenticate(listing, user=self.author)
        from music.views import SongViewSet
        response = SongViewSet.as_view({'get': 'list'})(listing)
        self.assertEqual(response.status_code, 200)
        row = next(
            item for item in response.data['results']
            if item['id'] == song.pk
        )
        self.assertEqual(
            [s['times_played'] for s in row['band_stats']], [1]
        )

        # O dono do setlist privado continua enxergando a própria execução.
        own_stats = SongSerializer(
            song, context={'request': api_request_for(self.other)}
        ).data['band_stats']
        self.assertEqual([s['times_played'] for s in own_stats], [2])

    def test_band_stats_uses_setlist_band_not_song_band(self):
        alpha = self._band('Alpha')
        beta = self._band('Beta')
        song = self._song(title='Hit', band=alpha)
        self._add(self._setlist(band=beta), song)
        from music.serializers import SongSerializer
        song.refresh_from_db()
        stats = SongSerializer(song).data['band_stats']
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]['band_name'], 'Beta')

    def test_history_only_counts_band_setlists(self):
        song = self._song(title='Hit')
        setlist = self._setlist(band=self._band('Alpha'), description='Ensaio Alpha')
        self._add(setlist, song)

        roster = VolunteerRoster.objects.create(
            church=self.church, date=timezone.localdate() - timedelta(days=5), theme='Culto',
        )
        from music.models import SetlistItem, WorshipSetlist
        worship = WorshipSetlist.objects.create(roster=roster, created_by=self.author)
        SetlistItem.objects.create(setlist=worship, song=song, order=1)

        from music.views import SongViewSet
        request = self.rf.get(f'/api/music/songs/{song.id}/history/')
        force_authenticate(request, user=self.author)
        resp = SongViewSet.as_view({'get': 'history'})(request, pk=song.id)
        self.assertEqual(resp.status_code, 200)
        results = resp.data['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'], 'Ensaio Alpha')
        self.assertEqual(results[0]['kind'], 'band')

    def test_history_empty_for_private_song(self):
        song = self._song(title='Segredo', is_private=True)
        self._add(self._setlist(band=self._band('Alpha')), song)
        from music.views import SongViewSet
        request = self.rf.get(f'/api/music/songs/{song.id}/history/')
        force_authenticate(request, user=self.author)
        resp = SongViewSet.as_view({'get': 'history'})(request, pk=song.id)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['results'], [])

    def test_history_hides_other_users_private_setlists(self):
        song = self._song(title='Hit')
        self._add(self._setlist(description='Escondido', is_private=True, created_by=self.other), song)
        self._add(self._setlist(description='Aberto', created_by=self.author), song, order=2)

        from music.views import SongViewSet
        request = self.rf.get(f'/api/music/songs/{song.id}/history/')
        force_authenticate(request, user=self.author)
        resp = SongViewSet.as_view({'get': 'history'})(request, pk=song.id)
        names = [row['name'] for row in resp.data['results']]
        self.assertIn('Aberto', names)
        self.assertNotIn('Escondido', names)

    def test_list_never_omits_band_stats(self):
        song = self._song(title='Hit')
        self._add(self._setlist(band=self._band('Alpha')), song)
        from music.views import SongViewSet
        request = self.rf.get('/api/music/songs/')
        force_authenticate(request, user=self.author)
        resp = SongViewSet.as_view({'get': 'list'})(request)
        row = next(r for r in resp.data if r['id'] == song.id)
        self.assertEqual(len(row['band_stats']), 1)
        self.assertEqual(row['band_stats'][0]['times_played'], 1)
        self.assertNotIn('times_played', row)
        self.assertNotIn('last_played', row)


class WorshipSetlistAndCheckYoutubeTestCase(VisibilityBase):
    def _roster(self):
        return VolunteerRoster.objects.create(
            church=self.church, date=date(2026, 10, 10), theme='Culto',
        )

    def test_manage_setlist_rejects_private_song_with_400(self):
        from music.views import VolunteerRosterViewSet
        private_song = self._song(title='Segredo', is_private=True)
        roster = self._roster()
        request = self.rf.put(
            f'/api/music/rosters/{roster.id}/setlist/',
            data={'items': [{'song': private_song.id, 'order': 1,
                             'custom_key': '', 'notes': ''}]},
            format='json',
        )
        force_authenticate(request, user=self.pastor)
        resp = VolunteerRosterViewSet.as_view(
            {'put': 'manage_setlist'}
        )(request, pk=roster.id)
        self.assertEqual(resp.status_code, 400)

    def test_manage_setlist_rejects_unknown_song_with_400(self):
        """Regressão: antes disso aqui estourava NameError e devolvia 500."""
        from music.views import VolunteerRosterViewSet
        roster = self._roster()
        request = self.rf.put(
            f'/api/music/rosters/{roster.id}/setlist/',
            data={'items': [{'song': 999999, 'order': 1,
                             'custom_key': '', 'notes': ''}]},
            format='json',
        )
        force_authenticate(request, user=self.pastor)
        resp = VolunteerRosterViewSet.as_view(
            {'put': 'manage_setlist'}
        )(request, pk=roster.id)
        self.assertEqual(resp.status_code, 400)

    def test_manage_setlist_accepts_public_song(self):
        from music.views import VolunteerRosterViewSet
        song = self._song(title='Pública')
        roster = self._roster()
        request = self.rf.put(
            f'/api/music/rosters/{roster.id}/setlist/',
            data={'items': [{'song': song.id, 'order': 1,
                             'custom_key': '', 'notes': ''}]},
            format='json',
        )
        force_authenticate(request, user=self.pastor)
        resp = VolunteerRosterViewSet.as_view(
            {'put': 'manage_setlist'}
        )(request, pk=roster.id)
        self.assertEqual(resp.status_code, 200)

    def test_check_youtube_keeps_global_search(self):
        """A busca por youtube_id é intencionalmente global (reaproveita o
        scraping). Este teste trava o comportamento acordado."""
        from music.views import SongViewSet
        self._song(title='Alheia', youtube_id='zzzzzzzzzzz', is_private=True,
                   created_by=self.outsider, church=self.other_church)
        request = self.rf.get('/api/music/songs/check-youtube/?video_id=zzzzzzzzzzz')
        force_authenticate(request, user=self.author)
        resp = SongViewSet.as_view({'get': 'check_youtube'})(request)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['found'])
        self.assertEqual(resp.data['song']['title'], 'Alheia')
        self.assertEqual(resp.data['song']['band_stats'], [])
