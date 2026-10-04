import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from src.review_db import ReviewDB
from src.review_server import ReviewApp, make_handler

RAW = {'title': '연습', 'duration': 10, 'engine': 'test',
       'segments': [{'start': 1, 'end': 4, 'text': '발표를 시작하겠습니다'},
                    {'start': 5, 'end': 9, 'text': '감사합니다'}]}


class ReviewHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = ReviewApp(self.temp.name)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def make_review(self, job_id, options=None):
        folder = self.app.folder(job_id)
        folder.mkdir()
        self.app.write_json(folder / 'raw.json', RAW)
        (folder / 'audio.wav').write_bytes(b'RIFF')
        return self.app.save_report(job_id, RAW, options or {'script': '', 'target_seconds': None, 'edits': {}})

    def request(self, method, route, body=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=5)
        conn.request(method, route, body)
        response = conn.getresponse()
        result = response.status, json.loads(response.read() or b'null')
        conn.close()
        return result

    def test_saved_reviews_are_listed_newest_first(self):
        self.make_review('a' * 32)
        self.make_review('b' * 32)
        with self.app.db.transaction() as conn:
            conn.execute("UPDATE review SET created_at = '2026-01-01T00:00:00' WHERE id = ?", ('a' * 32,))
        status, data = self.request('GET', '/api/reviews')
        self.assertEqual(status, 200)
        self.assertEqual([r['id'] for r in data['reviews']], ['b' * 32, 'a' * 32])
        self.assertEqual(data['reviews'][0]['segment_count'], 2)

    def test_segments_keep_original_asr_text_after_edit(self):
        job_id = 'c' * 32
        self.make_review(job_id)
        status, _ = self.request('POST', f'/api/reviews/{job_id}', json.dumps({'edits': {'2': '감사드립니다'}}))
        self.assertEqual(status, 200)
        segments = self.app.db.segments(job_id)
        self.assertEqual(segments[1]['asr_text'], '감사합니다')
        self.assertEqual(segments[1]['text'], '감사드립니다')
        self.assertEqual(segments[1]['edited'], 1)
        self.assertEqual(len(segments), 2)

    def test_rename_updates_report_and_list_but_keeps_created_at(self):
        job_id = 'd' * 32
        self.make_review(job_id)
        created = self.app.db.list_reviews()[0]['created_at']
        status, report = self.request('POST', f'/api/reviews/{job_id}', json.dumps({'title': '  최종 리허설  '}))
        self.assertEqual(status, 200)
        self.assertEqual(report['title'], '최종 리허설')
        listed = self.app.db.list_reviews()[0]
        self.assertEqual((listed['title'], listed['created_at']), ('최종 리허설', created))
        for bad in ('', '   ', 'x' * 81, 3):
            status, _ = self.request('POST', f'/api/reviews/{job_id}', json.dumps({'title': bad}))
            self.assertEqual(status, 400)

    def test_delete_removes_files_and_row(self):
        job_id = 'e' * 32
        self.make_review(job_id)
        status, _ = self.request('DELETE', f'/api/reviews/{job_id}')
        self.assertEqual(status, 200)
        self.assertFalse(self.app.folder(job_id).exists())
        self.assertEqual(self.app.db.list_reviews(), [])
        status, _ = self.request('DELETE', f'/api/reviews/{job_id}')
        self.assertEqual(status, 404)

    def test_running_review_cannot_be_deleted(self):
        job_id = 'f' * 32
        self.make_review(job_id)
        self.app.jobs[job_id] = {'status': 'running'}
        status, _ = self.request('DELETE', f'/api/reviews/{job_id}')
        self.assertEqual(status, 400)
        self.assertTrue(self.app.folder(job_id).exists())

    def test_existing_reports_are_indexed_on_startup(self):
        job_id = '1' * 32
        self.make_review(job_id)
        db_path = Path(self.temp.name) / 'recordings' / 'reviews.db'
        db_path.unlink()  # Simulate records saved before the DB existed.
        restarted = ReviewApp(self.temp.name)
        self.assertEqual([r['id'] for r in restarted.db.list_reviews()], [job_id])

    def test_rows_for_missing_folders_are_dropped_on_sync(self):
        db = ReviewDB(Path(self.temp.name) / 'other.db')
        db.upsert({'id': '2' * 32, 'duration': 1, 'segments': []})
        db.sync(Path(self.temp.name) / 'recordings' / 'reviews')
        self.assertEqual(db.list_reviews(), [])


if __name__ == '__main__':
    unittest.main()
