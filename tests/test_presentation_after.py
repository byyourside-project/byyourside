import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from src.presentation_after import AfterStore, build_report, markdown_report, retrieve
from src.presentation_server import PresentationApp, make_server
from src.question_audio import QuestionAudio


def saved_record():
    return {'session_id': 'example', 'status': 'ended', 'elapsed_sec': 50,
            'deck': {'deck_id': 'd', 'title': '테스트 발표', 'total_duration_sec': 60, 'slides': [
                {'slide_id': 'a', 'title': '첫 부분', 'target_duration_sec': 30, 'keypoints': [
                    {'keypoint_id': 'p1', 'text': '음성 인식은 로컬에서 실행합니다.', 'required': True}]},
                {'slide_id': 'b', 'title': '마지막 부분', 'target_duration_sec': 30, 'keypoints': [
                    {'keypoint_id': 'p2', 'text': '보드 시험 결과는 아직 없습니다.', 'required': True}]}]},
            'visits': [{'slide_id': 'a', 'start_sec': 0, 'end_sec': 20},
                       {'slide_id': 'b', 'start_sec': 20, 'end_sec': 40},
                       {'slide_id': 'a', 'start_sec': 40, 'end_sec': 50}],
            'segments': [{'segment_id': 's1', 'start_sec': 2, 'end_sec': 6, 'text': '음성 인식은 로컬에서 실행합니다.', 'status': 'OK'}],
            'states': {'p1': {'status': 'explained', 'evidence_segment_ids': ['s1']},
                       'p2': {'status': 'unconfirmed', 'evidence_segment_ids': []}}, 'issues': []}


class ReportTests(unittest.TestCase):
    def test_revisited_slide_uses_sum_and_preserves_evidence(self):
        report = build_report(saved_record())
        self.assertEqual([s['actual_sec'] for s in report['slides']], [30, 20])
        self.assertEqual(report['difference_sec'], -10)
        self.assertEqual(report['counts'], {'explained': 1, 'uncertain': 0, 'unconfirmed': 1})
        self.assertEqual(report['slides'][0]['points'][0]['evidence'][0]['segment_id'], 's1')

    def test_missing_evidence_cannot_remain_explained(self):
        record = saved_record(); record['segments'] = []
        report = build_report(record)
        self.assertEqual(report['counts']['explained'], 0)
        self.assertEqual(report['counts']['uncertain'], 1)
        self.assertTrue(report['issues'])

    def test_uncertain_audio_cannot_remain_explained(self):
        record = saved_record(); record['segments'][0]['status'] = 'UNCERTAIN'
        self.assertEqual(build_report(record)['counts']['explained'], 0)

    def test_interrupted_visit_and_unvisited_slide(self):
        record = saved_record(); record['status'] = 'running'
        record['visits'] = [{'slide_id': 'a', 'start_sec': 0, 'end_sec': None}]
        report = build_report(record)
        self.assertEqual(report['slides'][0]['actual_sec'], 50)
        self.assertFalse(report['slides'][1]['visited'])
        self.assertTrue(report['issues'])

    def test_invalid_duration_rejected(self):
        for value in [-1, float('nan'), float('inf'), True]:
            record = saved_record(); record['elapsed_sec'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_report(record)

    def test_markdown_has_evidence_and_escapes_user_html(self):
        record = saved_record(); record['deck']['title'] = '<script>alert(1)</script>'
        output = markdown_report(build_report(record))
        self.assertNotIn('<script>', output)
        self.assertIn('2–6초', output)

    def test_retrieval_cites_exact_original_and_does_not_claim_an_answer(self):
        sources = build_report(saved_record())['sources']
        result = retrieve('음성 인식은 어디서 실행하나요?', sources)
        self.assertEqual(result['status'], 'sources_found')
        self.assertEqual(result['sources'][0]['text'], sources[0]['text'])
        self.assertEqual(result['evidence_ids'], ['S1'])
        self.assertEqual(retrieve('판매 가격이 얼마인가요?', sources)['status'], 'insufficient_evidence')


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'example.json'
        self.original = json.dumps(saved_record(), ensure_ascii=False)
        self.path.write_text(self.original, encoding='utf-8')
        self.store = AfterStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_question_and_evidence_survive_reload_and_supplement_edits(self):
        self.store.supplement('example', '검증용 센서는 세 개입니다.')
        self.store.ask('example', '센서는 몇 개입니까?')
        self.store.supplement('example', '수정된 자료')
        report = AfterStore(self.root).detail('example')
        self.assertEqual(report['qa']['questions'][0]['sources'][0]['text'], '검증용 센서는 세 개입니다.')
        self.assertEqual(self.path.read_text(encoding='utf-8'), self.original)

    def test_bad_file_does_not_hide_valid_history(self):
        (self.root / 'broken.json').write_text('not json', encoding='utf-8')
        listing = self.store.listing()
        self.assertEqual(len(listing['sessions']), 1)
        self.assertEqual(listing['unreadable_count'], 1)

    def test_path_traversal_invalid_ids_and_unknown_record_rejected(self):
        for value in ['../secret', '..', 'x/y', 'C:\\Windows', None, '', 'a' * 81]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.detail(value)
        with self.assertRaises(FileNotFoundError):
            self.store.ask('missing', '질문')

    def test_question_limit_and_empty_input(self):
        for question in ['', '   ', None, 'a' * 1001]:
            with self.subTest(question=question), self.assertRaises(ValueError):
                self.store.ask('example', question)
        self.store._save('example', {'questions': [{}] * 200, 'supplement': ''})
        with self.assertRaises(ValueError):
            self.store.ask('example', '질문')

    def test_concurrent_questions_not_lost(self):
        threads = [threading.Thread(target=self.store.ask, args=('example', f'질문 {i}')) for i in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(self.store.detail('example')['qa']['questions']), 8)

    def test_overfull_notes_do_not_replace_previous_questions(self):
        self.store.ask('example', '음성 인식?')
        original = (self.root / 'qa/example.json').read_bytes()
        with patch('src.presentation_after.MAX_RECORD_BYTES', 10):
            with self.assertRaises(ValueError):
                self.store._save('example', {'supplement': 'new data', 'questions': []})
        self.assertEqual((self.root / 'qa/example.json').read_bytes(), original)


class AfterHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'example.json').write_text(json.dumps(saved_record()), encoding='utf-8')
        self.app = PresentationApp(saved_record()['deck'], output_dir=self.root)
        self.server = make_server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.app.close(); self.thread.join(2)
        self.temp.cleanup()

    def get(self, path, data=None, headers=None):
        request = Request(self.url + path, data=json.dumps(data).encode() if data is not None else None,
                          headers={'Content-Type': 'application/json', **(headers or {})})
        return urlopen(request, timeout=5)

    def test_navigation_report_and_export(self):
        with self.get('/after') as response:
            self.assertIn('default-src', response.headers['Content-Security-Policy'])
            self.assertIn('발표 후', response.read().decode())
        with self.get('/api/after/sessions') as response:
            self.assertEqual(json.load(response)['sessions'][0]['session_id'], 'example')
        with self.get('/api/after/export?session_id=example') as response:
            self.assertIn('목표 60초', json.load(response)['markdown'])

    def test_post_roundtrip_and_cross_origin_rejected(self):
        with self.get('/api/after/ask', {'session_id': 'example', 'question': '음성 인식?'}) as response:
            self.assertEqual(json.load(response)['status'], 'sources_found')
        with self.assertRaises(HTTPError) as exc:
            self.get('/api/after/ask', {'session_id': 'example', 'question': '질문'}, {'Origin': 'https://example.com'})
        self.assertEqual(exc.exception.code, 403)

    def test_question_audio_route_and_no_recording_during_live(self):
        with patch.object(self.app.question_audio, 'transcribe', return_value={'question': '테스트 질문'}) as transcribe:
            with self.get('/api/after/transcribe', {'fake': True}) as response:
                self.assertEqual(json.load(response)['question'], '테스트 질문')
            transcribe.assert_called_once()
            self.app.start({'microphone': False})
            with self.assertRaises(HTTPError) as exc:
                self.get('/api/after/transcribe', {'fake': True})
            self.assertEqual(exc.exception.code, 400)
            self.assertEqual(transcribe.call_count, 1)

    def test_missing_and_traversal(self):
        for suffix, expected in [('missing', 404), ('../example', 400)]:
            with self.subTest(suffix=suffix), self.assertRaises(HTTPError) as exc:
                self.get('/api/after/report?session_id=' + suffix)
            self.assertEqual(exc.exception.code, expected)


class QuestionAudioTests(unittest.TestCase):
    def test_temporary_recording_removed_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = QuestionAudio(tmp)
            with patch('src.review_audio.analyze_file', side_effect=ValueError('bad input')) as analyzer:
                with self.assertRaises(ValueError): audio.transcribe(b'invalid')
                self.assertEqual(analyzer.call_args.kwargs['max_duration'], 60)
            self.assertEqual(list((Path(tmp) / 'logs/question_audio').iterdir()), [])
            self.assertTrue(audio.lock.acquire(blocking=False)); audio.lock.release()

    def test_busy_and_empty_audio_rejected(self):
        audio = QuestionAudio('.')
        with self.assertRaises(ValueError): audio.transcribe(b'')
        audio.lock.acquire()
        try:
            with self.assertRaises(ValueError): audio.transcribe(b'audio')
        finally: audio.lock.release()


if __name__ == '__main__':
    unittest.main()
