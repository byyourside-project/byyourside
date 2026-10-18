import json
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scripts.prepare_photo_training_data import PROMPT, completion
from scripts.run_photo_model import create_server
from src.photo_commands import PROMPT as RUNTIME_PROMPT, parse_command


class ContractTests(unittest.TestCase):
    def test_all_dataset_labels_round_trip_and_prompt_matches(self):
        self.assertEqual(PROMPT, RUNTIME_PROMPT)
        source = Path(__file__).resolve().parents[1] / 'docs/photo_booth/dataset_v09_draft.json'
        for row in json.loads(source.read_text(encoding='utf-8')):
            with self.subTest(row=row['id']):
                actual = parse_command(completion(row))
                self.assertEqual(actual['action'], row['action'])
                self.assertEqual(actual['args'], row['args'])

    def test_rejects_unknown_values_extra_calls_and_expressions(self):
        invalid = ['<maum_7>()<maum_end>', '<maum_2>(seconds=5)<maum_end>',
                   '<maum_0>(frame=1)<maum_end>', '<maum_3>(seconds=3)<maum_end>',
                   '<maum_3>()<maum_end><maum_3>()<maum_end>',
                   '<maum_2>(seconds=3+7)<maum_end>', '<maum_3>()',
                   '다음 명령: <maum_3>()<maum_end>']
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_command(raw)

    def test_capture_requests_confirmation(self):
        self.assertTrue(parse_command('<maum_3>()<maum_end>')['confirmation_required'])
        self.assertFalse(parse_command('<maum_2>(seconds=10)<maum_end>')['confirmation_required'])


class FakeModel:
    device = 'test'

    def __init__(self):
        self.inputs = []
        self.fail = False

    def interpret(self, text):
        self.inputs.append(text)
        if self.fail:
            raise ValueError('bad model output')
        return parse_command('<maum_4>(reason=1)<maum_end>')


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.model = FakeModel()
        self.server = create_server(self.model, port=0, allowed_origins=['http://127.0.0.1:8770'])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def call(self, payload, origin='http://127.0.0.1:8770'):
        request = Request(self.base + '/api/photo/interpret', json.dumps(payload).encode(),
                          {'Content-Type': 'application/json', 'Origin': origin})
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read()), response.headers

    def test_model_is_used_and_no_action_preserved(self):
        code, result, headers = self.call({'text': '기본 사진틀을 선택해 줘'})
        self.assertEqual(code, 200)
        self.assertEqual(self.model.inputs, ['기본 사진틀을 선택해 줘'])
        self.assertEqual(result['action'], 'no_action')
        self.assertEqual(headers['Access-Control-Allow-Origin'], 'http://127.0.0.1:8770')

    def test_foreign_origin_rejected_before_inference(self):
        self.assertEqual(self.call({'text': '사진 찍어줘'}, 'https://example.com')[0], 403)
        self.assertEqual(self.model.inputs, [])

    def test_invalid_inputs_do_not_reach_model(self):
        for payload in ({'text': ''}, {'text': 123}, {'text': 'a' * 513}, []):
            with self.subTest(payload=str(payload)[:30]):
                self.assertEqual(self.call(payload)[0], 400)
        self.assertEqual(self.model.inputs, [])

    def test_bad_model_output_is_not_replaced_by_keywords(self):
        self.model.fail = True
        self.assertEqual(self.call({'text': '사진 찍어줘'})[0], 422)


if __name__ == '__main__':
    unittest.main()
