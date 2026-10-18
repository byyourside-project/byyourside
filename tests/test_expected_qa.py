import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from src import expected_qa as qa
from src.app_suite import create_servers
from src.review_server import ReviewApp

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ('안녕하세요. 오늘은 온디바이스 발표 도우미 곁을 소개하겠습니다.\n'
          '기존 서비스는 클라우드에 녹음을 보내지만, 곁은 기기 안에서 음성을 처리합니다.\n'
          'QCS6490 보드의 NPU를 활용해 실시간으로 피드백합니다.\n'
          '연습 영상 분석에서 시선은 93% 구간에서 판단이 가능했습니다.\n'
          '실제 마이크 시험은 아직 진행 중입니다.')


class FakeCoach:
    model, base_url, timeout = 'fake:1b', 'http://127.0.0.1:11434', 6.0

    def __init__(self, content=None, error=None):
        self.content, self.error = content, error

    def _request(self, url, body):
        if self.error:
            raise self.error
        assert json.loads(body)['format'] == 'json'
        return {'message': {'content': json.dumps(self.content, ensure_ascii=False)}}


class GeneratorTests(unittest.TestCase):
    def test_rule_based_covers_typical_question_sources(self):
        doc = qa.generate(SCRIPT)
        cats = [i['category'] for i in doc['items']]
        self.assertEqual(doc['generator'], 'rule_based')
        for cat in ('수치·근거', '용어·선택 이유', '비교·차별점', '한계·계획', '데이터·보안', '핵심 요약'):
            self.assertIn(cat, cats)
        number = next(i for i in doc['items'] if i['category'] == '수치·근거')
        self.assertIn('93%', number['question'])          # not the 6490 inside QCS6490
        self.assertEqual([i['id'] for i in doc['items']], [f'Q{n}' for n in range(1, len(doc['items']) + 1)])
        self.assertTrue(all(1 <= e['line'] <= 5 for i in doc['items'] for e in i['evidence']))

    def test_rehearsal_gap_becomes_first_question(self):
        report = {'comparison': [{'text': 'QCS6490 보드의 NPU를 활용해 실시간으로 피드백합니다.', 'status': 'unconfirmed'}]}
        first = qa.generate(SCRIPT, report)['items'][0]
        self.assertEqual((first['category'], first['evidence'][0]['line']), ('연습에서 빠진 부분', 3))

    def test_model_items_are_grounded_or_dropped(self):
        content = {'items': [
            {'category': '수치·근거', 'question': '93%는 어떻게 측정했나요?', 'answer_points': ['1분 영상 기준'], 'lines': [4]},
            {'category': '성과', 'question': '매출은 얼마인가요?', 'answer_points': ['10억'], 'lines': [99]},   # invented line
            {'question': '근거 없는 질문', 'answer_points': [], 'lines': [1]},
        ]}
        doc = qa.generate(SCRIPT, None, FakeCoach(content))
        self.assertEqual(doc['generator'], 'ollama:fake:1b')
        self.assertEqual([i['question'] for i in doc['items']], ['93%는 어떻게 측정했나요?'])

    def test_model_failure_falls_back_with_note(self):
        doc = qa.generate(SCRIPT, None, FakeCoach(error=TimeoutError()))
        self.assertEqual(doc['generator'], 'rule_based')
        self.assertIn('TimeoutError', doc['note'])
        self.assertTrue(doc['items'])

    def test_empty_script_rejected_and_live_copy_validated(self):
        with self.assertRaises(ValueError):
            qa.generate('   ')
        with self.assertRaises(ValueError):
            qa.sanitize_for_live({'items': [{'question': 'x', 'answer_points': [], 'my_answer': 'a' * 2001}]})


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'examples', self.root / 'examples')
        shutil.copytree(ROOT / 'web', self.root / 'web')
        self.live, self.review_server, self.live_server = create_servers(self.root, 0, 0)
        self.servers = [self.review_server, self.live_server]
        self.threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in self.servers]
        for t in self.threads:
            t.start()
        self.review_url = f'http://127.0.0.1:{self.review_server.server_port}'
        self.live_url = f'http://127.0.0.1:{self.live_server.server_port}'
        app = ReviewApp(self.root)
        self.job = 'c' * 32
        folder = app.folder(self.job)
        folder.mkdir()
        raw = {'duration': 30, 'segments': [{'start': 1, 'end': 5, 'text': '안녕하세요 오늘은 온디바이스 발표 도우미 곁을 소개하겠습니다'}]}
        app.write_json(folder / 'raw.json', raw)
        app.save_report(self.job, raw, {'script': SCRIPT, 'target_seconds': 60, 'edits': {}})

    def tearDown(self):
        for s in self.servers:
            s.shutdown()
            s.server_close()
        self.live.close()
        for t in self.threads:
            t.join(timeout=2)
        self.temp.cleanup()

    def post(self, base, path, data):
        req = Request(base + path, data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
        with urlopen(req, timeout=10) as r:
            return json.load(r)

    def get(self, base, path):
        with urlopen(base + path, timeout=10) as r:
            return json.load(r)

    def test_generate_edit_and_hand_off_to_live(self):
        doc = self.post(self.review_url, f'/api/reviews/{self.job}/expected-qa', {'action': 'generate'})
        self.assertTrue(doc['items'])
        # Rehearsal speech only covered line 1, so missing script lines come first.
        self.assertEqual(doc['items'][0]['category'], '연습에서 빠진 부분')
        report = self.get(self.review_url, f'/api/jobs/{self.job}')['report']
        self.assertEqual(report['expected_qa']['items'][0]['id'], 'Q1')

        keep = doc['items'][:2]
        edited = [{**keep[0], 'my_answer': '측정 방법부터 설명한다.'}, keep[1]]
        saved = self.post(self.review_url, f'/api/reviews/{self.job}/expected-qa', {'action': 'save', 'items': edited})
        self.assertEqual(len(saved['items']), 2)
        self.assertEqual(saved['items'][0]['my_answer'], '측정 방법부터 설명한다.')
        self.assertEqual(saved['items'][0]['evidence'], keep[0]['evidence'])   # server keeps its own evidence

        # Re-saving the transcript keeps the questions in report.json.
        again = self.post(self.review_url, f'/api/reviews/{self.job}', {'title': '새 제목'})
        self.assertEqual(len(again['expected_qa']['items']), 2)

        self.post(self.review_url, '/api/presentation/prepare',
                  {'script': SCRIPT, 'target_seconds': 60, 'title': '연습', 'review_id': self.job})
        live = self.get(self.live_url, '/api/state')['expected_qa']
        self.assertEqual([i['question'] for i in live['items']], [i['question'] for i in saved['items']])
        self.assertEqual(live['items'][0]['my_answer'], '측정 방법부터 설명한다.')
        self.assertNotIn('evidence', live['items'][0])

        # Editing the script in the live screen keeps the prepared questions.
        self.post(self.live_url, '/api/script', {'text': SCRIPT + '\n감사합니다.', 'duration_sec': 70})
        self.assertEqual(len(self.get(self.live_url, '/api/state')['expected_qa']['items']), 2)

    def test_unknown_question_id_rejected(self):
        self.post(self.review_url, f'/api/reviews/{self.job}/expected-qa', {'action': 'generate'})
        with self.assertRaises(HTTPError) as ctx:
            self.post(self.review_url, f'/api/reviews/{self.job}/expected-qa',
                      {'action': 'save', 'items': [{'id': 'Q99', 'question': 'x', 'answer_points': [], 'my_answer': ''}]})
        self.assertEqual(ctx.exception.code, 400)


if __name__ == '__main__':
    unittest.main()
