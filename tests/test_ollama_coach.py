import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError

from src.ollama_coach import OllamaCoach


class OllamaTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.models = [{"name": "test:1b", "size": 100, "digest": "fixture"}]
        self.response = {"done": True, "done_reason": "stop", "load_duration": 10,
                         "message": {"content": json.dumps({"1": {"s": 1, "e": [1]}})}}
        self.redirect = False
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"models": owner.models}).encode())
            def do_POST(self):
                owner.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                if owner.redirect:
                    self.send_response(302)
                    self.send_header("Location", "https://example.com/model")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(json.dumps(owner.response).encode())
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.coach = OllamaCoach("test:1b", f"http://127.0.0.1:{self.server.server_port}")
        self.job = {"slide": {"title": "내용", "keypoints": [{"keypoint_id": "k1", "text": "로컬 실행", "aliases": []}]},
                    "segments": [{"segment_id": "s1", "text": "기기 안에서 처리합니다"}]}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_installed_model_and_schema_request_preserve_input_ids(self):
        response = self.coach.evaluate(self.job)
        request = self.requests[0]
        self.assertEqual(request["model"], "test:1b")
        self.assertFalse(request["stream"])
        self.assertFalse(request["think"])
        self.assertEqual(request["format"]["required"], ["1"])
        self.assertEqual(request["format"]["properties"]["1"]["properties"]["s"]["enum"], [-1, 0, 1])
        self.assertEqual(response["judgments"][0]["keypoint_id"], "k1")
        self.assertEqual(response["judgments"][0]["evidence_segment_ids"], ["s1"])
        self.assertEqual(self.coach.model_info["digest"], "fixture")
        self.assertGreater(self.coach.last_metrics["wall_ms"], 0)

    def test_multiple_points_and_split_evidence_restore_original_ids(self):
        self.job["slide"]["keypoints"].append({"keypoint_id": "long-original-id", "text": "시간 안내", "aliases": []})
        self.job["segments"].append({"segment_id": "s2", "text": "로컬에서 실행합니다."})
        self.response["message"]["content"] = json.dumps({"1": {"s": 1, "e": [1, 2]}, "2": {"s": -1, "e": []}})
        result = self.coach.evaluate(self.job)["judgments"]
        self.assertEqual([r["keypoint_id"] for r in result], ["k1", "long-original-id"])
        self.assertEqual(result[0]["evidence_segment_ids"], ["s1", "s2"])
        self.assertEqual(result[1]["status"], "unconfirmed")
        shape = self.requests[0]["format"]["properties"]["1"]
        self.assertEqual(shape["properties"]["s"]["enum"], [-1, 0, 1])
        self.assertEqual(shape["properties"]["e"]["items"]["enum"], [1, 2])

    def test_missing_or_extra_points_and_invalid_status_evidence_are_rejected(self):
        for content in ({"1": {"s": 2, "e": [1]}}, {"1": {"s": True, "e": [1]}}, {"1": {"s": 1, "e": []}}, {"1": {"s": -1, "e": [1]}}, {"1": {"s": 0, "e": []}}, {}, {"1": [1, 1], "2": [-1]}, [[1, 1]], {"1": [True, 1]}, {"1": [2, 1]}, {"1": [1]}, {"1": [0]}, {"1": [-1, 1]}):
            self.response["message"]["content"] = json.dumps(content)
            with self.assertRaises(ValueError):
                self.coach.evaluate(self.job)

    def test_missing_or_remote_model_never_triggers_inference_or_download(self):
        for models in ([], [{"name": "test:1b", "size": 100, "remote_host": "remote"}]):
            self.models = models
            with self.assertRaises(ValueError):
                self.coach.evaluate(self.job)
        self.assertFalse(self.requests)

    def test_remote_url_cloud_name_and_invalid_timeout_rejected(self):
        for kwargs in ({"base_url": "https://example.com"}, {"model": "test:cloud"}, {"timeout": 0}):
            with self.assertRaises(ValueError):
                OllamaCoach(**{"model": "test:1b", **kwargs})

    def test_redirects_are_not_followed(self):
        self.redirect = True
        with self.assertRaises(HTTPError):
            self.coach.evaluate(self.job)

    def test_incomplete_or_truncated_responses_are_not_accepted(self):
        self.response["done"] = False
        with self.assertRaises(ValueError):
            self.coach.evaluate(self.job)
        self.response.update(done=True, done_reason="length")
        with self.assertRaises(ValueError):
            self.coach.evaluate(self.job)

    def test_invalid_json_and_missing_content_are_errors(self):
        self.response["message"]["content"] = "not json"
        with self.assertRaises(ValueError):
            self.coach.evaluate(self.job)

    def test_evidence_indices_cannot_reference_missing_segments(self):
        for indices in ([True], [0], [2], ["1"]):
            self.response["message"]["content"] = json.dumps({"1": {"s": 1, "e": indices}})
            with self.assertRaises(ValueError):
                self.coach.evaluate(self.job)
        self.response["message"] = {}
        with self.assertRaises(ValueError):
            self.coach.evaluate(self.job)

    def test_context_limit_is_not_treated_as_complete_context(self):
        self.response["prompt_eval_count"] = 4096
        with self.assertRaises(ValueError):
            self.coach.evaluate(self.job)


if __name__ == "__main__":
    unittest.main()
