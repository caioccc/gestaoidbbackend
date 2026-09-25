"""Testes da listagem paginada e ordenável do repertório (SongViewSet.list)."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from music.models import Song

from .test_fixes import MusicFixturesMixin

User = get_user_model()


class SongListPaginationTest(MusicFixturesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.songs = []
        for i, (title, artist) in enumerate([
            ('Oceanos', 'Hillsong United'),
            ('Rasga o Céu', 'Nívea Soares'),
            ('A Ele a Glória', 'Diante do Trono'),
            ('Casa do Pai', 'Anderson Freire'),
            ('Águas Purificadoras', 'Hillsong United'),
        ]):
            self.songs.append(
                Song.objects.create(
                    church=self.church,
                    title=title,
                    artist=artist,
                    youtube_id=f'{i + 1:011d}',
                )
            )

    def _list_request(self, path):
        from music.views import SongViewSet
        v = SongViewSet()
        req = self._rf('get', path)
        v.request = req
        v.action = 'list'
        v.format_kwarg = None
        v.kwargs = {}
        return v, v.list(req)

    def test_list_with_page_returns_paginated_dict(self):
        v, resp = self._list_request('/api/music/songs/?page=1&page_size=2')
        self.assertEqual(resp.status_code, 200)
        data = resp.data
        self.assertIn('count', data)
        self.assertIn('results', data)
        self.assertEqual(data['count'], 5)
        self.assertEqual(len(data['results']), 2)

    def test_list_without_pagination_params_returns_plain_array(self):
        _, resp = self._list_request('/api/music/songs/')
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.data, list)
        self.assertEqual(len(resp.data), 5)

    def test_ordering_by_title(self):
        _, resp = self._list_request('/api/music/songs/?page=1&page_size=10&ordering=title')
        titles = [s['title'] for s in resp.data['results']]
        expected = sorted(t for t in [
            'Oceanos', 'Rasga o Céu', 'A Ele a Glória',
            'Casa do Pai', 'Águas Purificadoras',
        ])
        self.assertEqual(titles, expected)

    def test_ordering_by_title_desc(self):
        _, resp = self._list_request('/api/music/songs/?page=1&page_size=10&ordering=-title')
        titles = [s['title'] for s in resp.data['results']]
        self.assertEqual(titles, sorted(titles, reverse=True))

    def test_ordering_by_artist(self):
        _, resp = self._list_request('/api/music/songs/?page=1&page_size=10&ordering=artist')
        artists = [s['artist'] for s in resp.data['results']]
        self.assertEqual(artists, sorted(artists))

    def test_ordering_random_returns_all_songs(self):
        _, resp = self._list_request('/api/music/songs/?page=1&page_size=10&ordering=random')
        self.assertEqual(len(resp.data['results']), 5)

    def test_ordering_times_played_ok(self):
        for ordering in ('times_played', '-times_played', 'band', '-band'):
            _, resp = self._list_request(
                f'/api/music/songs/?page=1&page_size=10&ordering={ordering}'
            )
            self.assertEqual(resp.status_code, 200, msg=ordering)
            self.assertEqual(resp.data['count'], 5)