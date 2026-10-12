import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from src.app_suite import create_servers
from src.metrics import get_peak_rss_mb

ROOT = Path(__file__).resolve().parents[1]


class AppSuiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'examples', self.root / 'examples')
        shutil.copytree(ROOT / 'web', self.root / 'web')
        self.live, self.review_server, self.live_server = create_servers(self.root, 0, 0)
        self.servers = [self.review_server, self.live_server]
        self.threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in self.servers]
        for thread in self.threads:
            thread.start()
        self.review_url = f'http://127.0.0.1:{self.review_server.server_port}'
        self.live_url = f'http://127.0.0.1:{self.live_server.server_port}'

    def tearDown(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        self.live.close()
        for thread in self.threads:
            thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, base, path, data=None, headers=None):
        req = Request(base + path, data=None if data is None else json.dumps(data).encode(),
                      headers={'Content-Type': 'application/json', **(headers or {})})
        with urlopen(req, timeout=5) as response:
            return json.load(response)

    def test_distinct_servers_expose_reciprocal_navigation(self):
        review = self.request(self.review_url, '/api/status')
        live = self.request(self.live_url, '/api/state')
        self.assertEqual(review['presentation_url'], self.live_url)
        self.assertEqual(live['review_url'], self.review_url)
        self.assertIsNone(live['session'])
        self.assertFalse(live['voice_enabled'])
        self.assertEqual(self.request(self.review_url, '/api/reviews')['reviews'], [])

    def test_handoff_preserves_script_and_time_without_starting(self):
        script = '안녕하세요.\n발표 전과 발표 중을 함께 준비합니다.'
        result = self.request(self.review_url, '/api/presentation/prepare',
                              {'script': script, 'target_seconds': 90, 'title': '통합 연습'})
        state = self.request(self.live_url, '/api/state')
        self.assertEqual(result['url'], self.live_url)
        self.assertEqual(state['deck']['script_text'], script)
        self.assertEqual(state['deck']['total_duration_sec'], 90)
        self.assertIsNone(state['session'])
        self.assertEqual(state['audio_status'], 'idle')
        self.assertFalse(state['voice_enabled'])

    def test_invalid_handoff_does_not_replace_existing_plan(self):
        old = self.request(self.live_url, '/api/state')['deck']
        with self.assertRaises(HTTPError) as error:
            self.request(self.review_url, '/api/presentation/prepare',
                         {'script': '새 대본', 'target_seconds': None})
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.request(self.live_url, '/api/state')['deck'], old)

    def test_handoff_cannot_replace_running_session(self):
        self.request(self.live_url, '/api/start', {'microphone': False})
        old = self.request(self.live_url, '/api/state')['deck']
        with self.assertRaises(HTTPError) as error:
            self.request(self.review_url, '/api/presentation/prepare',
                         {'script': '새 대본', 'target_seconds': 60})
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.request(self.live_url, '/api/state')['deck'], old)

    def test_external_origin_cannot_replace_script(self):
        with self.assertRaises(HTTPError) as error:
            self.request(self.review_url, '/api/presentation/prepare',
                         {'script': '새 대본', 'target_seconds': 60}, {'Origin': 'https://example.com'})
        self.assertEqual(error.exception.code, 403)


class WindowsMetricsTests(unittest.TestCase):
    def test_peak_working_set_is_used_without_unix_resource(self):
        memory = SimpleNamespace(rss=4 * 1024**2, peak_wset=8 * 1024**2)
        with patch('src.metrics.resource', None), patch('src.metrics.psutil.Process') as process:
            process.return_value.memory_info.return_value = memory
            self.assertEqual(get_peak_rss_mb(), 8.0)


if __name__ == '__main__':
    unittest.main()
