"""Testes do worker de extração de cifras e do fluxo de status (PENDING/MANUAL/FAILED).

Cobre:
- command process_chordify_queue (serviço mockado, sem Selenium/rede);
- regras de church_key/original_key/bpm do worker;
- ação reprocess (qualquer perfil da igreja);
- derivar chord_status no create/update do SongSerializer via SongViewSet.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from music.management.commands.process_chordify_queue import Command as WorkerCommand
from music.models import Song
from music.views import SongViewSet

User = get_user_model()

_SUCCESS = {
    'status': 'success',
    'success': True,
    'chords': '1000;C:maj;0;2\n1000;G:maj;2;4',
    'chords_formatada': [
        {'start': 0, 'end': 2, 'note': 'C', 'note_fmt': 'C'},
        {'start': 2, 'end': 4, 'note': 'G', 'note_fmt': 'G'},
    ],
    'format_key': 'G',
    'derivedBpm': 120,
    'source': 'next_data',
    'youtube_id': 'aaaaaaaaaaa',
}

_UNAVAILABLE = {
    'status': 'unavailable',
    'success': False,
    'chords': [],
    'chords_formatada': [],
    'reason': 'blocked_by_cloudflare',
    'youtube_id': 'aaaaaaaaaaa',
}


class WorkerCommandMixin:
    def setUp(self):
        from accounts.models import Church
        self.church = Church.objects.create(
            name='Igreja Worker Teste',
            church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE',
            is_approved=True,
            city='Campina Grande',
            state='PB',
        )

    def _worker(self, max_retries=3, limit=3, instrument='guitar'):
        cmd = WorkerCommand()
        cmd.instrument = instrument
        cmd.max_retries = max_retries
        cmd.limit = limit
        cmd.interval = 1.0
        cmd.once = True
        cmd._stop = False
        return cmd

    def _song(self, **kwargs):
        defaults = {
            'church': self.church,
            'title': 'Música Teste',
            'youtube_id': 'aaaaaaaaaaa',
        }
        defaults.update(kwargs)
        return Song.objects.create(**defaults)


class WorkerProcessSongTest(WorkerCommandMixin, TestCase):
    def _process(self, song, max_retries=3, **result_overrides):
        payload = dict(_SUCCESS)
        payload.update(result_overrides)
        cmd = self._worker(max_retries=max_retries)
        with patch(
            'music.management.commands.process_chordify_queue.'
            'chordify_service.enrich_chordify_data',
            return_value=payload,
        ) as mock_enrich:
            cmd._process_song(song)
        return mock_enrich

    def test_success_completes_and_preserves_church_key(self):
        song = self._song(church_key='A')
        self._process(song, format_key='G', derivedBpm=120)
        song.refresh_from_db()
        self.assertEqual(song.chord_status, Song.ChordStatus.COMPLETED)
        self.assertEqual(song.chord_error, '')
        self.assertEqual(song.chord_retries, 0)
        self.assertIsNotNone(song.chord_processed_at)
        self.assertEqual(song.original_key, 'G')
        self.assertEqual(song.bpm, 120)
        self.assertEqual(song.church_key, 'A')
        self.assertEqual(song.chords, _SUCCESS['chords'])
        self.assertEqual(len(song.chords_json), 2)

    def test_success_does_not_overwrite_filled_key_and_bpm(self):
        song = self._song(church_key='A', original_key='Bb', bpm=90)
        self._process(song, format_key='G', derivedBpm=120)
        song.refresh_from_db()
        self.assertEqual(song.original_key, 'Bb')
        self.assertEqual(song.bpm, 90)
        self.assertEqual(song.church_key, 'A')

    def test_tom_definido_never_overwritten(self):
        song = self._song(church_key='Dm')
        self._process(song, format_key='C', derivedBpm=100)
        song.refresh_from_db()
        self.assertEqual(song.church_key, 'Dm')
        self.assertEqual(song.original_key, 'C')

    def test_failure_increments_retries_and_returns_to_pending(self):
        song = self._song()
        self._process(song, **{'status': 'unavailable', 'reason': 'blocked'})
        song.refresh_from_db()
        self.assertEqual(song.chord_status, Song.ChordStatus.PENDING)
        self.assertEqual(song.chord_retries, 1)
        self.assertIn('blocked', song.chord_error)

    def test_failure_at_max_retries_marks_failed(self):
        song = self._song(chord_retries=2)
        self._process(song, max_retries=3, **{'status': 'unavailable', 'reason': 'blocked'})
        song.refresh_from_db()
        self.assertEqual(song.chord_status, Song.ChordStatus.FAILED)
        self.assertEqual(song.chord_retries, 3)
        self.assertIn('blocked', song.chord_error)
        self.assertIsNotNone(song.chord_processed_at)

    def test_success_without_chords_is_a_failure(self):
        song = self._song()
        self._process(song, chords_formatada=[])
        song.refresh_from_db()
        self.assertEqual(song.chord_status, Song.ChordStatus.PENDING)
        self.assertEqual(song.chord_retries, 1)
        self.assertIn('nenhum acorde', song.chord_error)

    def test_unexpected_exception_marks_failure(self):
        song = self._song()
        with patch(
            'music.management.commands.process_chordify_queue.'
            'chordify_service.enrich_chordify_data',
            side_effect=RuntimeError('explodiu'),
        ):
            self._worker()._process_song(song)
        song.refresh_from_db()
        self.assertEqual(song.chord_status, Song.ChordStatus.PENDING)
        self.assertEqual(song.chord_retries, 1)
        self.assertIn('explodiu', song.chord_error)


class WorkerBatchTest(WorkerCommandMixin, TestCase):
    def test_batch_picks_only_pending_and_with_youtube_id(self):
        self._song(chord_status=Song.ChordStatus.COMPLETED)
        self._song(youtube_id='')
        cmd = self._worker()
        with patch(
            'music.management.commands.process_chordify_queue.'
            'chordify_service.enrich_chordify_data',
            return_value=_SUCCESS,
        ) as mock_enrich:
            processed = cmd._process_batch()
        self.assertEqual(processed, 0)
        mock_enrich.assert_not_called()

    def test_batch_respects_limit(self):
        for i in range(5):
            self._song(title=f'Música {i}')
        cmd = self._worker(limit=3)
        with patch(
            'music.management.commands.process_chordify_queue.'
            'chordify_service.enrich_chordify_data',
            return_value=_SUCCESS,
        ) as mock_enrich:
            processed = cmd._process_batch()
        self.assertEqual(processed, 3)
        self.assertEqual(mock_enrich.call_count, 3)
        self.assertEqual(
            Song.objects.filter(chord_status=Song.ChordStatus.COMPLETED).count(), 3,
        )


class ReproduceChordStatusSerializerTest(TestCase):
    def setUp(self):
        self.church = None
        from accounts.models import Church
        from accounts.models import ChurchMembership
        self.church = Church.objects.create(
            name='Igreja Status Teste',
            church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE',
            is_approved=True,
            city='Campina Grande',
            state='PB',
        )
        self.user = User.objects.create(
            email='gestor@status.com', name='Gestor', church=self.church,
            is_active=True,
        )
        self.user.set_password('S3nh@segura')
        self.user.save(update_fields=['password', 'is_active'])
        ChurchMembership.objects.create(
            user=self.user, church=self.church, role=ChurchMembership.Role.LOUVOR,
        )
        self.factory = APIRequestFactory()

    def _create_song(self, **kwargs):
        defaults = {
            'church': self.church, 'title': 'M1', 'youtube_id': 'aaaaaaaaaaa',
        }
        defaults.update(kwargs)
        return Song.objects.create(**defaults)

    def test_create_without_chords_defaults_pending(self):
        request = self.factory.post(
            '/api/music/songs/', data={'title': 'Nova', 'artist': 'Art',
                                       'youtube_id': 'aaaaaaaaaaa'},
            format='json',
        )
        force_authenticate(request, user=self.user)
        resp = SongViewSet.as_view({'post': 'create'})(request)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['chord_status'], Song.ChordStatus.PENDING)

    def test_create_with_chords_json_marks_manual(self):
        request = self.factory.post(
            '/api/music/songs/',
            data={'title': 'Nova', 'youtube_id': 'aaaaaaaaaaa',
                  'chords_json': [{'start': 0, 'end': 2, 'note': 'C', 'note_fmt': 'C'}]},
            format='json',
        )
        force_authenticate(request, user=self.user)
        resp = SongViewSet.as_view({'post': 'create'})(request)
        self.assertEqual(resp.status_code, 201)
        song = Song.objects.get(pk=resp.data['id'])
        self.assertEqual(song.chord_status, Song.ChordStatus.MANUAL)
        self.assertEqual(song.chord_retries, 0)
        self.assertEqual(song.chord_error, '')

    def test_create_with_empty_chords_string_marks_pending(self):
        request = self.factory.post(
            '/api/music/songs/',
            data={'title': 'Nova', 'youtube_id': 'aaaaaaaaaaa', 'chords': ''},
            format='json',
        )
        force_authenticate(request, user=self.user)
        resp = SongViewSet.as_view({'post': 'create'})(request)
        self.assertEqual(resp.data['chord_status'], Song.ChordStatus.PENDING)

    def test_update_without_chords_preserves_completed(self):
        song = self._create_song(
            chord_status=Song.ChordStatus.COMPLETED, chords='1000;C:maj;0;2',
        )
        request = self.factory.patch(
            f'/api/music/songs/{song.id}/',
            data={'title': 'M1', 'artist': 'Novo Artista'},
            format='json',
        )
        force_authenticate(request, user=self.user)
        view = SongViewSet.as_view({'patch': 'partial_update'})
        resp = view(request, pk=song.id)
        self.assertEqual(resp.status_code, 200)
        song.refresh_from_db()
        self.assertEqual(song.chord_status, Song.ChordStatus.COMPLETED)

    def test_update_with_empty_chords_resets_to_pending(self):
        song = self._create_song(chord_status=Song.ChordStatus.COMPLETED)
        request = self.factory.patch(
            f'/api/music/songs/{song.id}/',
            data={'title': 'M1', 'chords': ''}, format='json',
        )
        force_authenticate(request, user=self.user)
        SongViewSet.as_view({'patch': 'partial_update'})(request, pk=song.id)
        song.refresh_from_db()
        self.assertEqual(song.chord_status, Song.ChordStatus.PENDING)

    def test_update_with_chords_marks_manual(self):
        song = self._create_song(chord_status=Song.ChordStatus.COMPLETED)
        request = self.factory.patch(
            f'/api/music/songs/{song.id}/',
            data={'title': 'M1',
                  'chords_json': [{'start': 0, 'end': 2, 'note': 'G', 'note_fmt': 'G'}]},
            format='json',
        )
        force_authenticate(request, user=self.user)
        SongViewSet.as_view({'patch': 'partial_update'})(request, pk=song.id)
        song.refresh_from_db()
        self.assertEqual(song.chord_status, Song.ChordStatus.MANUAL)


class ReprocessActionTest(TestCase):
    def setUp(self):
        self.church = None
        from accounts.models import Church
        from accounts.models import ChurchMembership
        self.church = Church.objects.create(
            name='Igreja Reprocess Teste',
            church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE',
            is_approved=True,
            city='Campina Grande',
            state='PB',
        )
        self.user = User.objects.create(
            email='musico@reprocess.com', name='Músico', church=self.church,
            is_active=True,
        )
        ChurchMembership.objects.create(
            user=self.user, church=self.church, role=ChurchMembership.Role.MUSICO,
        )
        self.factory = APIRequestFactory()

    def test_reprocess_resets_and_is_available_to_musico(self):
        song = Song.objects.create(
            church=self.church, title='M1', youtube_id='aaaaaaaaaaa',
            chord_status=Song.ChordStatus.COMPLETED,
            chord_retries=5, chord_error='erro antigo',
            chord_processed_at=timezone.now(), chords='1000;C:maj;0;2',
        )
        request = self.factory.post(f'/api/music/songs/{song.id}/reprocess/')
        force_authenticate(request, user=self.user)
        resp = SongViewSet.as_view({'post': 'reprocess'})(request, pk=song.id)
        self.assertEqual(resp.status_code, 200)
        body = resp.data
        self.assertTrue(body['success'])
        self.assertEqual(body['song']['chord_status'], Song.ChordStatus.PENDING)
        song.refresh_from_db()
        self.assertEqual(song.chord_status, Song.ChordStatus.PENDING)
        self.assertEqual(song.chord_retries, 0)
        self.assertEqual(song.chord_error, '')
        self.assertIsNone(song.chord_processed_at)
        self.assertEqual(song.chords, '1000;C:maj;0;2')

    def test_reprocess_also_available_to_manager(self):
        from accounts.models import ChurchMembership
        ChurchMembership.objects.filter(user=self.user).delete()
        ChurchMembership.objects.create(
            user=self.user, church=self.church, role=ChurchMembership.Role.LOUVOR,
        )
        song = Song.objects.create(
            church=self.church, title='M2', youtube_id='bbbbbbbbbbb',
            chord_status=Song.ChordStatus.FAILED,
        )
        request = self.factory.post(f'/api/music/songs/{song.id}/reprocess/')
        force_authenticate(request, user=self.user)
        resp = SongViewSet.as_view({'post': 'reprocess'})(request, pk=song.id)
        self.assertEqual(resp.status_code, 200)
