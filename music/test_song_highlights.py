"""Testes dos destaques pessoais de músicas (SongHighlight)."""
from django.db import transaction
from django.db.utils import IntegrityError
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from music.models import Song, SongHighlight
from music.views import SongViewSet

from .test_fixes import MusicFixturesMixin


class SongHighlightTest(MusicFixturesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.rf = APIRequestFactory()
        self.song = Song.objects.create(
            church=self.church,
            title='Oceanos',
            artist='Hillsong United',
            created_by=self.user,
            youtube_id='highlight001',
        )
        self.song2 = Song.objects.create(
            church=self.church,
            title='Rasga o Céu',
            artist='Nívea Soares',
            created_by=self.user,
            youtube_id='highlight002',
        )
        self.song3 = Song.objects.create(
            church=self.church,
            title='Casa do Pai',
            artist='Anderson Freire',
            created_by=self.user,
            youtube_id='highlight003',
        )

    def _call(self, user, action, method='post', pk=None, path='/api/music/songs/'):
        request = getattr(self.rf, method)(
            path, data={}, format='json',
        )
        force_authenticate(request, user=user)
        view = SongViewSet.as_view({method: action})
        if pk is not None:
            return view(request, pk=pk)
        return view(request)

    def test_highlight_toggle_is_idempotent(self):
        resp = self._call(self.user, 'highlight', pk=self.song.id)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['is_highlighted'])
        self.assertEqual(
            SongHighlight.objects.filter(user=self.user, song=self.song).count(), 1
        )

        resp = self._call(self.user, 'highlight', pk=self.song.id)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            SongHighlight.objects.filter(user=self.user, song=self.song).count(), 1
        )

        resp = self._call(self.user, 'highlight', method='delete', pk=self.song.id)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['is_highlighted'])
        self.assertFalse(
            SongHighlight.objects.filter(user=self.user, song=self.song).exists()
        )

    def test_highlight_is_unique_per_user_and_song(self):
        SongHighlight.objects.create(user=self.user, song=self.song)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SongHighlight.objects.create(user=self.user, song=self.song)

    def test_highlights_are_private_per_user(self):
        SongHighlight.objects.create(user=self.user, song=self.song)

        resp = self._call(self.user, 'highlights', method='get')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([row['id'] for row in resp.data], [self.song.id])

        resp = self._call(self.user2, 'highlights', method='get')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, [])

    def test_is_highlighted_reflects_authenticated_user(self):
        SongHighlight.objects.create(user=self.user, song=self.song)

        resp = self._call(
            self.user, 'list', method='get',
            path='/api/music/songs/?page=1&page_size=10',
        )
        rows = {row['id']: row for row in resp.data['results']}
        self.assertTrue(rows[self.song.id]['is_highlighted'])
        self.assertFalse(rows[self.song2.id]['is_highlighted'])

        resp = self._call(
            self.user2, 'list', method='get',
            path='/api/music/songs/?page=1&page_size=10',
        )
        rows = {row['id']: row for row in resp.data['results']}
        self.assertFalse(rows[self.song.id]['is_highlighted'])

    def test_ordering_highlighted_filters_and_keeps_search(self):
        SongHighlight.objects.create(user=self.user, song=self.song)
        SongHighlight.objects.create(user=self.user, song=self.song2)
        SongHighlight.objects.create(user=self.user2, song=self.song3)

        resp = self._call(
            self.user, 'list', method='get',
            path='/api/music/songs/?page=1&page_size=10&ordering=highlighted',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 2)
        self.assertEqual(
            {row['id'] for row in resp.data['results']},
            {self.song.id, self.song2.id},
        )

        resp = self._call(
            self.user, 'list', method='get',
            path='/api/music/songs/?page=1&page_size=10&ordering=highlighted&q=Oceanos',
        )
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['id'], self.song.id)

    def test_cannot_highlight_song_invisible_to_user(self):
        private_song = Song.objects.create(
            church=self.church,
            title='Segredo',
            created_by=self.user2,
            is_private=True,
            youtube_id='highlight004',
        )

        resp = self._call(self.user, 'highlight', pk=private_song.id)
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(
            SongHighlight.objects.filter(user=self.user, song=private_song).exists()
        )

    def test_deleting_song_removes_its_highlights(self):
        SongHighlight.objects.create(user=self.user, song=self.song)
        self.song.delete()
        self.assertFalse(
            SongHighlight.objects.filter(user=self.user).exists()
        )
