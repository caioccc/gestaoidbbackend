"""Testes da importação em lote do repertório ("Adicionar Múltiplos").

Critérios de aceite:
- Só o ADMIN (staff/superuser) com igreja ativa importa; LOUVOR/PASTOR/
  MÚSICO recebem 403 e staff sem igreja ativa também.
- O envio cria uma música por linha, forçando `church`/`created_by` do
  usuário (o payload não consegue forjar esses campos).
- Sem `chords`/`chords_json` no payload a música entra em PENDING — quem
  processa é o worker local, como no cadastro unitário.
- `youtube_id` já cadastrado NA MESMA igreja → `skipped` (sem criar linha).
- Linha inválida vira `failed` com erro por campo sem derrubar as demais
  (validação independente por índice).
- `check-youtube-bulk` responde em uma consulta, agrupa por youtube_id
  (vence o mais recente) e, como o `check-youtube`, ignora a visibilidade.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import Church, ChurchMembership
from music.models import Song
from music.views import SongViewSet

User = get_user_model()

BULK_URL = '/api/music/songs/bulk-create/'
PREFILL_URL = '/api/music/songs/check-youtube-bulk/'


class SongBulkImportTestCase(TestCase):
    def setUp(self):
        self.church = Church.objects.create(
            name='Igreja Bulk Teste',
            church_type=Church.ChurchType.INDEPENDENT,
            status='ACTIVE',
            is_approved=True,
            city='Campina Grande',
            state='PB',
        )
        self.other_church = Church.objects.create(
            name='Outra Igreja',
            church_type=Church.ChurchType.CONGREGATION,
            status='ACTIVE',
            is_approved=True,
            city='João Pessoa',
            state='PB',
        )
        self.louvore = self._member('louvore@teste.com', 'Louvore', ChurchMembership.Role.LOUVOR)
        self.pastor = self._member('pastor@teste.com', 'Pastor', ChurchMembership.Role.PASTOR)
        self.musico = self._member('musico@teste.com', 'Musico', ChurchMembership.Role.MUSICO)
        self.admin = User.objects.create_superuser(
            email='admin@teste.com', password='admin', church=self.church,
        )
        self.rf = APIRequestFactory()

    def _member(self, email, name, role):
        user = User.objects.create(
            email=email, name=name, church=self.church, is_active=True,
        )
        ChurchMembership.objects.create(user=user, church=self.church, role=role)
        return user

    def _row(self, **kwargs):
        defaults = {
            'title': 'Senhor te Adoramos',
            'youtube_id': 'aaaaaaaaaaa',
            'artist': 'Fernandinho',
            'thumbnail_url': 'https://i.ytimg.com/vi/aaaaaaaaaaa/hqdefault.jpg',
            'church_key': 'C',
        }
        defaults.update(kwargs)
        return defaults

    def _bulk(self, rows, user=None):
        request = self.rf.post(
            BULK_URL, data={'songs': rows}, format='json',
        )
        force_authenticate(request, user=user or self.admin)
        return SongViewSet.as_view({'post': 'bulk_create'})(request)

    def _prefill(self, video_ids, user=None):
        request = self.rf.post(
            PREFILL_URL, data={'video_ids': video_ids}, format='json',
        )
        force_authenticate(request, user=user or self.admin)
        return SongViewSet.as_view({'post': 'check_youtube_bulk'})(request)

    # ------------------------------------------------------------------
    # Permissão
    # ------------------------------------------------------------------

    def test_admin_bulk_creates_all_rows(self):
        rows = [
            self._row(youtube_id='aaaaaaaaaaa'),
            self._row(youtube_id='bbbbbbbbbbb', title='Valadão 1'),
            self._row(youtube_id='ccccccccccc', title='Valadão 2', church_key='G'),
        ]
        resp = self._bulk(rows)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['created'], 3)
        self.assertEqual(resp.data['skipped'], 0)
        self.assertEqual(resp.data['failed'], 0)
        self.assertEqual(Song.objects.count(), 3)
        self.assertEqual(
            [r['status'] for r in resp.data['results']], ['created'] * 3,
        )
        song = Song.objects.get(youtube_id='ccccccccccc')
        self.assertEqual(song.title, 'Valadão 2')
        self.assertEqual(song.church_key, 'G')
        self.assertEqual(song.band_id, None)

    def test_church_and_created_by_come_from_user(self):
        resp = self._bulk([
            self._row(church=999, created_by=999, church_key='D'),
        ])

        self.assertEqual(resp.status_code, 200)
        song = Song.objects.get(youtube_id='aaaaaaaaaaa')
        self.assertEqual(song.church_id, self.church.id)
        self.assertEqual(song.created_by_id, self.admin.id)

    def test_rows_land_in_pending_for_the_local_worker(self):
        resp = self._bulk([self._row()])

        self.assertEqual(resp.status_code, 200)
        song = Song.objects.get(youtube_id='aaaaaaaaaaa')
        self.assertEqual(song.chord_status, Song.ChordStatus.PENDING)

    def test_non_admin_roles_are_forbidden(self):
        for user in (self.louvore, self.pastor, self.musico):
            with self.subTest(email=user.email):
                resp = self._bulk([self._row()], user=user)
                self.assertEqual(resp.status_code, 403)
        self.assertEqual(Song.objects.count(), 0)

    def test_admin_without_active_church_is_forbidden(self):
        orphan = User.objects.create_superuser(
            email='orfao@teste.com', password='admin',
        )
        resp = self._bulk([self._row()], user=orphan)

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Song.objects.count(), 0)

    def test_prefill_requires_admin_too(self):
        resp = self._prefill(['aaaaaaaaaaa'], user=self.louvore)
        self.assertEqual(resp.status_code, 403)

    # ------------------------------------------------------------------
    # Duplicados
    # ------------------------------------------------------------------

    def test_youtube_id_already_in_church_is_skipped(self):
        Song.objects.create(
            church=self.church, title='Já cadastrada', youtube_id='aaaaaaaaaaa',
        )
        resp = self._bulk([
            self._row(youtube_id='aaaaaaaaaaa', title='Repetida'),
            self._row(youtube_id='bbbbbbbbbbb', title='Nova'),
        ])

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['created'], 1)
        self.assertEqual(resp.data['skipped'], 1)
        skipped = [r for r in resp.data['results'] if r['status'] == 'skipped']
        self.assertEqual(skipped[0]['reason'], 'already_registered')
        self.assertEqual(skipped[0]['index'], 0)
        self.assertEqual(Song.objects.count(), 2)
        self.assertTrue(
            Song.objects.filter(youtube_id='aaaaaaaaaaa', title='Já cadastrada').exists()
        )

    def test_same_youtube_id_from_another_church_is_not_skipped(self):
        Song.objects.create(
            church=self.other_church, title='De outra igreja',
            youtube_id='aaaaaaaaaaa',
        )
        resp = self._bulk([self._row(youtube_id='aaaaaaaaaaa')])

        self.assertEqual(resp.data['skipped'], 0)
        self.assertEqual(resp.data['created'], 1)

    def test_repeated_youtube_id_inside_one_payload_is_skipped(self):
        resp = self._bulk([
            self._row(youtube_id='aaaaaaaaaaa', title='Primeira'),
            self._row(youtube_id='aaaaaaaaaaa', title='Segunda'),
        ])

        self.assertEqual(resp.data['created'], 1)
        self.assertEqual(resp.data['skipped'], 1)
        self.assertEqual(Song.objects.count(), 1)

    # ------------------------------------------------------------------
    # Validação por linha
    # ------------------------------------------------------------------

    def test_invalid_row_fails_without_blocking_the_others(self):
        resp = self._bulk([
            self._row(youtube_id='aaaaaaaaaaa', title='Válida'),
            self._row(youtube_id='bbbbbbbbbbb', title='   '),
            self._row(youtube_id='ccccccccccc', title='Também válida'),
        ])

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['created'], 2)
        self.assertEqual(resp.data['failed'], 1)
        failed = [r for r in resp.data['results'] if r['status'] == 'failed']
        self.assertEqual(failed[0]['index'], 1)
        self.assertIn('title', failed[0]['errors'])
        self.assertEqual(
            sorted(Song.objects.values_list('youtube_id', flat=True)),
            ['aaaaaaaaaaa', 'ccccccccccc'],
        )

    def test_thumbnail_url_invalid_is_reported_per_field(self):
        resp = self._bulk([self._row(thumbnail_url='nao-e-uma-url')])

        self.assertEqual(resp.data['failed'], 1)
        failed = [r for r in resp.data['results'] if r['status'] == 'failed'][0]
        self.assertIn('thumbnail_url', failed['errors'])

    def test_empty_or_invalid_songs_payload_is_rejected(self):
        self.assertEqual(self._bulk([]).status_code, 400)

        request = self.rf.post(BULK_URL, data={}, format='json')
        force_authenticate(request, user=self.admin)
        resp = SongViewSet.as_view({'post': 'bulk_create'})(request)
        self.assertEqual(resp.status_code, 400)

    def test_batch_size_limit(self):
        rows = [self._row(youtube_id=f'{index:011d}') for index in range(101)]
        resp = self._bulk(rows)

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Song.objects.count(), 0)

    # ------------------------------------------------------------------
    # check-youtube-bulk
    # ------------------------------------------------------------------

    def test_prefill_returns_only_existing_videos(self):
        Song.objects.create(
            church=self.other_church,
            title='Já no catálogo',
            artist='Alto Riso',
            youtube_id='aaaaaaaaaaa',
            church_key='E',
            bpm=72,
            tags='louvor',
        )
        resp = self._prefill(['aaaaaaaaaaa', 'bbbbbbbbbbb'])

        self.assertEqual(resp.status_code, 200)
        found = resp.data['found']
        self.assertEqual(list(found), ['aaaaaaaaaaa'])
        self.assertEqual(found['aaaaaaaaaaa']['title'], 'Já no catálogo')
        self.assertEqual(found['aaaaaaaaaaa']['church_key'], 'E')
        self.assertEqual(found['aaaaaaaaaaa']['bpm'], 72)
        self.assertEqual(found['aaaaaaaaaaa']['tags'], 'louvor')

    def test_prefill_keeps_the_most_recent_for_duplicated_ids(self):
        old = Song.objects.create(
            church=self.other_church, title='Antiga',
            youtube_id='aaaaaaaaaaa', church_key='C',
        )
        new = Song.objects.create(
            church=self.other_church, title='Recente',
            youtube_id='aaaaaaaaaaa', church_key='D',
        )
        Song.objects.filter(pk=old.pk).update(updated_at='2020-01-01T00:00:00Z')

        resp = self._prefill(['aaaaaaaaaaa'])

        self.assertEqual(resp.data['found']['aaaaaaaaaaa']['title'], 'Recente')
        self.assertEqual(resp.data['found']['aaaaaaaaaaa']['church_key'], 'D')
        self.assertNotEqual(new.pk, old.pk)

    def test_prefill_ignores_visibility_like_the_single_check(self):
        Song.objects.create(
            church=self.other_church, title='Privada alheia',
            youtube_id='aaaaaaaaaaa', is_private=True,
        )
        resp = self._prefill(['aaaaaaaaaaa'])

        self.assertIn('aaaaaaaaaaa', resp.data['found'])

    def test_prefill_dedupes_and_ignores_blank_ids(self):
        Song.objects.create(
            church=self.other_church, title='Catálogo',
            youtube_id='aaaaaaaaaaa',
        )
        resp = self._prefill(['aaaaaaaaaaa', ' aaaaaaaaaaa ', '', '   '])

        self.assertEqual(list(resp.data['found']), ['aaaaaaaaaaa'])

    def test_prefill_flags_only_the_own_church_as_duplicate(self):
        Song.objects.create(
            church=self.other_church, title='De outra igreja',
            youtube_id='aaaaaaaaaaa',
        )
        Song.objects.create(
            church=self.church, title='Já aqui',
            youtube_id='bbbbbbbbbbb',
        )
        found = self._prefill(['aaaaaaaaaaa', 'bbbbbbbbbbb']).data['found']

        self.assertFalse(found['aaaaaaaaaaa']['same_church'])
        self.assertTrue(found['bbbbbbbbbbb']['same_church'])

    def test_prefill_with_no_ids_returns_empty(self):
        self.assertEqual(self._prefill([]).data['found'], {})

    def test_prefill_batch_size_limit(self):
        resp = self._prefill([f'{index:011d}' for index in range(51)])

        self.assertEqual(resp.status_code, 400)