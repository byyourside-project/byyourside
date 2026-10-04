import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from src.presentation_server import PresentationApp, make_server
from src.logger import StructuredLogger
from tests.test_presentation import DECK


def wait_for(predicate, timeout=3):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError("condition timed out")


class PresentationServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = PresentationApp(DECK, output_dir=self.temp.name)
        self.server = make_server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, route, body=None, headers=None):
        payload = None if body is None else json.dumps(body).encode()
        return urlopen(Request(self.url + route, data=payload, headers={"Content-Type": "application/json", **(headers or {})}), timeout=3)

    def test_http_end_to_end_manual_flow_and_saved_event_contract(self):
        with self.request("/") as response:
            self.assertIn("발표 시작", response.read().decode())
        self.request("/api/start", {"microphone": False}).close()
        time.sleep(.03)
        self.request("/api/utterance", {"text": "기기에서 음성을 인식합니다"}).close()
        wait_for(lambda: self.app.state()["session"]["states"]["p1"]["status"] == "explained")
        self.request("/api/navigate", {"index": 1}).close()
        self.request("/api/stop", {}).close()
        wait_for(lambda: self.app.state()["session"]["status"] == "ended")
        saved = json.loads(Path(self.app.output_path).read_text())
        self.assertEqual(saved["events"][-1]["type"], "session_ended")
        self.assertEqual(saved["deck"], self.app.deck)
        with self.request("/api/export") as response:
            exported = json.load(response)
        self.assertEqual(exported["segments"][0]["text"], "기기에서 음성을 인식합니다")

    def test_cross_origin_mutation_and_invalid_deck_rejected(self):
        with self.assertRaises(HTTPError) as context:
            self.request("/api/state", headers={"Host": "attacker.example"})
        self.assertEqual(context.exception.code, 403)
        with self.assertRaises(HTTPError) as context:
            self.request("/api/start", {}, {"Origin": "https://example.com"})
        self.assertEqual(context.exception.code, 403)
        with self.assertRaises(HTTPError) as context:
            self.request("/api/deck", {"slides": []})
        self.assertEqual(context.exception.code, 400)
        self.assertIsNone(self.app.session)

    def test_logger_event_sink_and_consumer_failure_preserve_raw_log(self):
        events = []
        with StructuredLogger("consumer", self.temp.name, terminal_output=False, event_sink=events.append) as logger:
            logger.log_event("run_start", {"mode": "mic"})
        self.assertEqual(events[0]["event_type"], "run_start")
        def broken(event):
            raise ValueError("sink failed")
        with StructuredLogger("broken", self.temp.name, terminal_output=False, event_sink=broken) as logger:
            logger.log_event("run_start", {})
        content = (Path(self.temp.name) / "stt_run_broken.jsonl").read_text()
        self.assertIn("event_sink_error", content)
        self.assertIn("run_start", content)

    def test_fake_mic_stream_emits_during_run_and_stop_drains(self):
        class STT:
            def warm_up(self, seconds):
                pass
        class Pipeline:
            stt = STT()
            def __init__(self, sink):
                self.sink = sink
                self.closed = False
            def run_mic(self, **kwargs):
                origin = time.perf_counter()
                self.sink({"event_type": "run_start", "clock_origin_perf_counter": origin, "run_id": "fake"})
                time.sleep(.04)
                self.sink({"event_type": "segment_result", "run_id": "fake", "segment_id": 1,
                           "text": "기기에서 음성을 인식합니다", "audio_start_ms": 0, "audio_end_ms": 30,
                           "endpoint_reason": "silence", "stt_inference_ms": 1,
                           "delay_after_speech_ms": 10, "status": "OK"})
                kwargs["stop_event"].wait(2)
                self.sink({"event_type": "run_summary", "is_lossless": True})
            def close(self):
                self.closed = True
        pipelines = []
        def factory(sink):
            pipelines.append(Pipeline(sink))
            return pipelines[-1]
        self.app.pipeline_factory = factory
        self.app.start(microphone=True)
        wait_for(lambda: self.app.state()["session"]["states"]["p1"]["status"] == "explained")
        self.assertEqual(self.app.state()["audio_status"], "recording")
        self.app.command("stop", {})
        wait_for(lambda: self.app.state()["session"]["status"] == "ended")
        self.assertTrue(pipelines[0].closed)
        self.assertTrue(any(e["type"] == "audio_summary" for e in self.app.session.events))

    def test_model_failure_is_uncertain_and_does_not_stop_timer(self):
        class BrokenCoach:
            name = "test"
            def evaluate(self, job):
                raise TimeoutError("model timeout")
        self.app.coach = BrokenCoach()
        self.app.start()
        time.sleep(.02)
        self.app.command("utterance", {"text": "음성 인식"})
        wait_for(lambda: self.app.state()["session"]["states"]["p1"]["status"] == "uncertain")
        self.assertEqual(self.app.state()["session"]["status"], "running")
        self.assertIn("model timeout", self.app.state()["session"]["issues"][0])

    def test_mic_initialization_failure_remains_visible_and_saved(self):
        def broken(sink):
            raise RuntimeError("no microphone")
        self.app.pipeline_factory = broken
        self.app.start(True)
        wait_for(lambda: self.app.state()["audio_status"] == "error")
        self.app.command("stop", {})
        wait_for(lambda: self.app.state()["session"]["status"] == "ended")
        saved = json.loads(Path(self.app.output_path).read_text())
        self.assertIn("no microphone", saved["issues"][0])


if __name__ == "__main__":
    unittest.main()
