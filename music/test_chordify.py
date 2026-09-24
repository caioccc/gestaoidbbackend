"""Testes do scraper do Chordify (sem rede: todas as estratégias são mockadas)."""
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from music.services import chordify as chordify_service
from music.views import ChordifyView

User = get_user_model()

_PUBLIC_PAGE = 'https://chordify.net/chords/youtube:0ZF5em0MTwY'
_API_URL = (
    'https://chordify.net/api/v2/songs/youtube:0ZF5em0MTwY/chords'
    '?vocabulary=extended_inversions'
)


class _FakeElement:
    def __init__(self, text='', data_chord=None):
        self.text = text
        self._data_chord = data_chord

    def get_attribute(self, name):
        return self._data_chord if name == 'data-chord' else None


class _FakeDriver:
    def __init__(self, next_data='', api_body='', page_source='', dom_chords=(),
                 next_data_sequence=None, api_body_sequence=None):
        self._next_data = next_data
        self._next_data_sequence = list(next_data_sequence or [])
        self._api_body = api_body
        self._api_body_sequence = list(api_body_sequence or [])
        self.page_source = page_source
        self._dom_chords = list(dom_chords)
        self.visited = []
        self.quit_called = False

    def get(self, url):
        self.visited.append(url)

    def find_element(self, by, value):
        return _FakeElement()

    def find_elements(self, by, value):
        return self._dom_chords

    def execute_script(self, script):
        if '__NEXT_DATA__' in script:
            if self._next_data_sequence:
                return self._next_data_sequence.pop(0)
            return self._next_data
        if self._api_body_sequence:
            return self._api_body_sequence.pop(0)
        return self._api_body

    def quit(self):
        self.quit_called = True


class SafeJsonLoadsTest(TestCase):
    def test_valid_json_object(self):
        payload = chordify_service._safe_json_loads('{"chords": "x", "ok": true}')
        self.assertEqual(payload['chords'], 'x')
        self.assertTrue(payload['ok'])

    def test_valid_json_array(self):
        payload = chordify_service._safe_json_loads('[1, 2, 3]')
        self.assertEqual(payload, [1, 2, 3])

    def test_empty_string_returns_none(self):
        self.assertIsNone(chordify_service._safe_json_loads(''))
        self.assertIsNone(chordify_service._safe_json_loads(None))

    def test_html_not_starting_with_brace_returns_none(self):
        self.assertIsNone(chordify_service._safe_json_loads('<html>oi</html>'))
        self.assertIsNone(chordify_service._safe_json_loads('just a moment...'))

    def test_cloudflare_html_returns_none(self):
        html = '<html>Just a moment... checking your browser</html>'
        self.assertIsNone(chordify_service._safe_json_loads(html))

    def test_invalid_json_returns_none(self):
        self.assertIsNone(chordify_service._safe_json_loads('{"chords": oops'))


class FetchSeleniumTest(TestCase):
    def _run(self, driver):
        with patch('music.services.chromedriver.create_chrome_driver', return_value=driver):
            return chordify_service._fetch_selenium('0ZF5em0MTwY'), driver

    def test_navigates_to_public_page_when_api_unavailable(self):
        driver = _FakeDriver(
            api_body='<html>Just a moment</html>',
            next_data=json.dumps({'props': {'pageProps': {'chords': '1000;C:maj;0;2'}}}),
        )
        data, created = self._run(driver)
        self.assertEqual(created.visited, [_API_URL, _PUBLIC_PAGE])
        self.assertIn('1000;C:maj;0;2', data['chords'])
        self.assertTrue(created.quit_called)

    def test_api_url_with_valid_json_is_used(self):
        api_payload = {
            'chords': '1000;C:maj;0;2\n1000;F:maj;2;4',
            'derivedKey': 'C',
            'derivedBpm': 120,
            'youtube_id': '0ZF5em0MTwY',
        }
        driver = _FakeDriver(api_body=json.dumps(api_payload))
        data, created = self._run(driver)
        self.assertEqual(created.visited, [_API_URL])
        self.assertEqual(data['chords'], api_payload['chords'])
        self.assertEqual(data['derivedKey'], 'C')
        self.assertEqual(data['source'], 'selenium_api')
        self.assertEqual(data['youtube_id'], '0ZF5em0MTwY')

    def test_api_blocked_falls_back_to_public_page(self):
        driver = _FakeDriver(
            api_body='Just a moment... cf-chl-bm',
            next_data=json.dumps({
                'props': {
                    'pageProps': {
                        'song': {
                            'chords': '1000;C:maj;0;2',
                            'derivedKey': 'C',
                            'derivedBpm': 110,
                        }
                    }
                }
            }),
        )
        data, created = self._run(driver)
        self.assertEqual(created.visited, [_API_URL, _PUBLIC_PAGE])
        self.assertIn('1000;C:maj;0;2', data['chords'])

    def test_extracts_next_data_recursively(self):
        payload = {
            'props': {
                'pageProps': {
                    'song': {
                        'youtube_id': '0ZF5em0MTwY',
                        'derivedKey': 'C',
                        'derivedBpm': 120,
                        'chords': '1000;C:maj;0;2\n1000;F:maj;2;4',
                    }
                }
            }
        }
        data, _ = self._run(_FakeDriver(
            api_body='<html>carregando</html>',
            next_data=json.dumps(payload),
        ))
        self.assertEqual(data['chords'], payload['props']['pageProps']['song']['chords'])
        self.assertEqual(data['derivedKey'], 'C')
        self.assertEqual(data['derivedBpm'], 120)
        self.assertEqual(data['youtube_id'], '0ZF5em0MTwY')

    def test_cloudflare_blocked_page_raises(self):
        driver = _FakeDriver(
            api_body='Just a moment... cf-browser-verification',
            next_data='Just a moment... cf-browser-verification',
            page_source='',
        )
        with self.assertRaises(RuntimeError) as ctx:
            self._run(driver)
        self.assertIn('Cloudflare', str(ctx.exception))

    def test_cf_challenge_clears_and_extracts(self):
        payload = json.dumps({
            'props': {
                'pageProps': {
                    'song': {
                        'youtube_id': '0ZF5em0MTwY',
                        'derivedKey': 'G',
                        'derivedBpm': 100,
                        'chords': '0;C:maj;0;2',
                    }
                }
            }
        })
        driver = _FakeDriver(
            api_body='<html>carregando</html>',
            next_data_sequence=['', '', payload],
        )
        with patch('music.services.chordify.CF_CHALLENGE_POLL', 0):
            data, _ = self._run(driver)
        self.assertEqual(data['chords'], '0;C:maj;0;2')
        self.assertEqual(data['derivedKey'], 'G')

    def test_no_chords_raises(self):
        driver = _FakeDriver(
            api_body='<html>nada</html>',
            next_data=json.dumps({'props': {'pageProps': {}}}),
        )
        with self.assertRaises(RuntimeError):
            self._run(driver)

    def test_dom_fallback_collects_chords(self):
        payload = {'props': {'pageProps': {}}}
        driver = _FakeDriver(
            api_body='<html>mix html sem json</html>',
            next_data=json.dumps(payload),
            dom_chords=[_FakeElement(data_chord='C'), _FakeElement(data_chord='G')],
        )
        data, _ = self._run(driver)
        self.assertIn('0;C;0;0', data['chords'])
        self.assertIn('0;G;0;0', data['chords'])

    def test_dom_fallback_normalizes_solfege_and_slash_chords(self):
        payload = {'props': {'pageProps': {}}}
        driver = _FakeDriver(
            api_body='<html>ix</html>',
            next_data=json.dumps(payload),
            dom_chords=[
                _FakeElement(text='Re♭'),
                _FakeElement(text='Doₘ⁷'),
                _FakeElement(text='Mi♭\n/Do♯'),
            ],
        )
        data, _ = self._run(driver)
        self.assertIn('0;Db;0;0', data['chords'])
        self.assertIn('0;Cm7;0;0', data['chords'])
        self.assertIn('0;Eb/C#;0;0', data['chords'])

    def test_selenium_import_error_is_handled(self):
        with patch(
            'builtins.__import__',
            side_effect=ImportError('no selenium'),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                chordify_service._fetch_selenium('0ZF5em0MTwY')
        self.assertIn('Selenium indisponível', str(ctx.exception))


class NormalizeChordSymbolTest(TestCase):
    def test_solfege_naturals(self):
        for source, expected in (
            ('Do', 'C'), ('Re', 'D'), ('Mi', 'E'), ('Fa', 'F'),
            ('Sol', 'G'), ('La', 'A'), ('Si', 'B'),
        ):
            self.assertEqual(chordify_service._normalize_chord_symbol(source), expected)

    def test_solfege_with_accidentals(self):
        self.assertEqual(chordify_service._normalize_chord_symbol('Re♭'), 'Db')
        self.assertEqual(chordify_service._normalize_chord_symbol('Mi♭'), 'Eb')
        self.assertEqual(chordify_service._normalize_chord_symbol('Si♭'), 'Bb')
        self.assertEqual(chordify_service._normalize_chord_symbol('Fa♯'), 'F#')

    def test_solfege_with_unicode_quality(self):
        self.assertEqual(chordify_service._normalize_chord_symbol('Doₘ⁷'), 'Cm7')
        self.assertEqual(chordify_service._normalize_chord_symbol('Si♭ₘ⁷'), 'Bbm7')
        self.assertEqual(chordify_service._normalize_chord_symbol('La♭'), 'Ab')
        self.assertEqual(chordify_service._normalize_chord_symbol('La♭⁵'), 'Ab5')

    def test_slash_chord_with_newline(self):
        self.assertEqual(
            chordify_service._normalize_chord_symbol('Mi♭\n/Do♯'), 'Eb/C#'
        )

    def test_rest_symbol_becomes_n(self):
        self.assertEqual(chordify_service._normalize_chord_symbol('𝄽'), 'N')

    def test_letter_notation_passes_through(self):
        for source in ('C', 'G', 'Am', 'F#m7', 'Bb', 'Eb/G', 'C:maj', 'F:sus4'):
            self.assertEqual(
                chordify_service._normalize_chord_symbol(source), source,
            )


class EnrichAndUnavailableTest(TestCase):
    @patch('music.services.chordify._fetch_selenium', side_effect=RuntimeError('blocked'))
    def test_returns_graceful_unavailable_payload(self, _sel):
        payload = chordify_service.enrich_chordify_data('0ZF5em0MTwY')
        self.assertEqual(payload['status'], 'unavailable')
        self.assertFalse(payload['success'])
        self.assertEqual(payload['error'], 'chordify_blocked_or_unavailable')
        self.assertEqual(payload['chords'], [])
        self.assertEqual(payload['chords_formatada'], [])

    @patch('music.services.chordify._fetch_selenium')
    def test_success_sets_success_and_chords(self, _sel):
        _sel.return_value = {
            'chords': '1000;C:maj;0;2',
            'derivedKey': 'C:maj',
            'derivedBpm': 120,
            'youtube_id': '0ZF5em0MTwY',
        }
        payload = chordify_service.enrich_chordify_data('0ZF5em0MTwY')
        self.assertEqual(payload['status'], 'success')
        self.assertTrue(payload['success'])
        self.assertEqual(payload['format_key'], 'C')
        self.assertEqual(payload['chords_formatada'][0]['note_fmt'], 'C')

    @patch('music.services.chordify._fetch_selenium')
    def test_infers_format_key_from_chords_when_derived_key_missing(self, _sel):
        _sel.return_value = {
            'chords': '0;Re♭;0;0\n0;Mi♭;0;0',
            'youtube_id': '0ZF5em0MTwY',
        }
        payload = chordify_service.enrich_chordify_data('0ZF5em0MTwY')
        self.assertEqual(payload['status'], 'success')
        self.assertEqual(payload['format_key'], 'Db')
        self.assertEqual(payload['chords_formatada'][0]['note_fmt'], 'Db')


class ChordifyViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='chordify@fix.com', is_active=True)
        self.factory = APIRequestFactory()

    def _get(self, url='/api/music/chordify/?youtube_id=0ZF5em0MTwY'):
        request = self.factory.get(url)
        force_authenticate(request, user=self.user)
        return ChordifyView.as_view()(request)

    def test_missing_youtube_id_returns_400(self):
        resp = self._get('/api/music/chordify/')
        self.assertEqual(resp.status_code, 400)

    def test_invalid_youtube_id_returns_400(self):
        resp = self._get('/api/music/chordify/?youtube_id=curta')
        self.assertEqual(resp.status_code, 400)

    @patch('music.services.chordify.enrich_chordify_data')
    def test_unavailable_service_returns_200_json(self, mock_enrich):
        mock_enrich.return_value = chordify_service.chordify_unavailable('403 Forbidden')
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        body = resp.data
        self.assertEqual(body['status'], 'unavailable')
        self.assertEqual(body['error'], 'chordify_blocked_or_unavailable')
        self.assertEqual(body['chords'], [])

    @patch('music.services.chordify.enrich_chordify_data', side_effect=Exception('boom'))
    def test_unexpected_error_returns_200_json(self, _mock):
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], 'unavailable')
        self.assertEqual(resp.data['error'], 'chordify_blocked_or_unavailable')