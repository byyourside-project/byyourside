import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

from src.review_server import ReviewApp, make_handler


class ReviewServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = ReviewApp(self.temp.name)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.job_id = 'a' * 32
        folder = self.app.folder(self.job_id)
        folder.mkdir()
        self.raw = {'duration': 8, 'segments': [{'start': 1, 'end': 6, 'text': '발표 시작'}]}
        self.app.write_json(folder / 'raw.json', self.raw)
        (folder / 'audio.wav').write_bytes(b'RIFF' + bytes(range(100)))
        self.app.save_report(self.job_id, self.raw, {'script': '', 'target_seconds': None})

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, route, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=5)
        conn.request(method, route, body, headers or {})
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def test_export_matches_report_and_is_downloadable(self):
        status, headers, data = self.request('GET', '/api/export/' + self.job_id)
        self.assertEqual(status, 200)
        self.assertIn('attachment', headers['Content-Disposition'])
        self.assertEqual(json.loads(data)['segments'][0]['text'], '발표 시작')

    def test_audio_seeking_returns_requested_bytes(self):
        status, headers, data = self.request('GET', '/api/media/' + self.job_id,
                                             headers={'Range': 'bytes=4-13'})
        self.assertEqual(status, 206)
        self.assertEqual(headers['Content-Range'], 'bytes 4-13/104')
        self.assertEqual(data, bytes(range(10)))
        status, _, _ = self.request('GET', '/api/media/' + self.job_id, headers={'Range': 'bytes=999-'})
        self.assertEqual(status, 416)

    def test_client_disconnect_during_audio_is_not_an_error(self):
        folder = self.app.folder(self.job_id)
        (folder / 'audio.wav').write_bytes(b'RIFF' + bytes(4 * 1024 * 1024))
        errors = []
        self.server.handle_error = lambda request, address: errors.append(address)
        sock = socket.create_connection(('127.0.0.1', self.server.server_address[1]), timeout=5)
        host = f'127.0.0.1:{self.server.server_address[1]}'
        sock.sendall(f'GET /api/media/{self.job_id} HTTP/1.1\r\nHost: {host}\r\n\r\n'.encode())
        sock.recv(1024)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b'\x01\x00\x00\x00\x00\x00\x00\x00')
        sock.close()  # Abort mid-stream, like a browser seeking to another position.
        status, _, _ = self.request('GET', '/api/status')
        self.assertEqual(status, 200)
        time.sleep(0.5)
        self.assertEqual(errors, [])

    def test_external_origin_and_host_rejected(self):
        for headers in ({'Origin': 'https://example.com'}, {'Host': 'example.com'}):
            status, _, _ = self.request('POST', '/api/demo', '{}', headers)
            self.assertEqual(status, 403)

    def test_edit_recomputes_report_without_rewriting_raw(self):
        body = json.dumps({'edits': {'1': '발표를 시작합니다'}, 'target_seconds': 4})
        status, _, data = self.request('POST', '/api/reviews/' + self.job_id, body)
        self.assertEqual(status, 200)
        report = json.loads(data)
        self.assertEqual(report['segments'][0]['asr_text'], '발표 시작')
        self.assertEqual(report['schedule']['difference_seconds'], 1)
        stored = json.loads((self.app.folder(self.job_id) / 'raw.json').read_text(encoding='utf-8'))
        self.assertEqual(stored, self.raw)

    def test_invalid_edit_does_not_destroy_existing_result(self):
        path = self.app.folder(self.job_id) / 'report.json'
        original = path.read_bytes()
        status, _, _ = self.request('POST', '/api/reviews/' + self.job_id, '{"edits":{"99":"x"}}')
        self.assertEqual(status, 400)
        self.assertEqual(path.read_bytes(), original)

    def test_upload_is_stored_inside_new_job(self):
        status, _, data = self.request('POST', '/api/upload', b'RIFF-data', {'X-Audio-Extension': '.wav'})
        self.assertEqual(status, 201)
        job_id = json.loads(data)['id']
        self.assertEqual((self.app.folder(job_id) / 'source.wav').read_bytes(), b'RIFF-data')

    def test_invalid_upload_and_path_rejected(self):
        for ext, body in (('.exe', b'x'), ('.wav', b''), ('../../outside.wav', b'x')):
            status, _, _ = self.request('POST', '/api/upload', body, {'X-Audio-Extension': ext})
            self.assertEqual(status, 400)
        status, _, _ = self.request('GET', '/api/media/../../outside.wav')
        self.assertEqual(status, 404)

    def test_missing_models_blocks_inference(self):
        folder = self.app.folder(self.job_id)
        self.app.write_json(folder / 'upload.json', {'source': 'source.wav', 'title': '연습'})
        status, _, data = self.request('POST', '/api/analyze', json.dumps({'id': self.job_id}))
        self.assertEqual(status, 400)
        self.assertIn('모델', json.loads(data)['error'])
        self.assertFalse(self.app.analysis_lock.locked())


if __name__ == '__main__':
    unittest.main()
