import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
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

    def test_silence_endpoint_finishes_cut_even_when_endpoint_precedes_stt_result(self):
        for endpoint_first in (False, True):
            self.app.start()
            with self.app.lock:
                self.app.session.origin -= 5
                endpoint = {"event_type": "speech_endpoint", "audio_end_ms": 4000}
                if endpoint_first:
                    self.app._process_audio(endpoint)
                self.app._process_audio({"event_type": "segment_result", "run_id": "fixture", "segment_id": 1,
                                        "text": "기기에서 음성을 인식합니다", "audio_start_ms": 1000,
                                        "audio_end_ms": 3000, "endpoint_reason": "soft_max_duration",
                                        "stt_inference_ms": 10, "delay_after_speech_ms": 100})
                if not endpoint_first:
                    self.assertTrue(self.app.session.pending)
                    self.app._process_audio(endpoint)
            wait_for(lambda: self.app.state()["session"]["states"]["p1"]["status"] == "explained")
            self.app.command("stop", {})
            wait_for(lambda: self.app.state()["session"]["status"] == "ended")

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

    def test_stop_finalizes_last_cut_after_audio_drain_and_before_saving(self):
        for empty_tail in (False, True):
            self.app.start()
            with self.app.lock:
                self.app.session.origin -= 5
                self.app.audio_done = False
                self.app._process_audio({"event_type": "segment_result", "run_id": "fixture", "segment_id": 1,
                                        "text": "기기에서 음성을 인식합니다", "audio_start_ms": 1000,
                                        "audio_end_ms": 3000, "endpoint_reason": "hard_max_duration",
                                        "stt_inference_ms": 10, "delay_after_speech_ms": 100})
                self.app.command("stop", {})
            # The final detector/STT events must drain before cut fragments are
            # judged as the completed utterance.
            time.sleep(.08)
            self.assertEqual(self.app.session.status, "stopping")
            self.assertTrue(self.app.session.pending)
            self.assertFalse(any(e["type"] == "keypoint_judged" for e in self.app.session.events))
            if empty_tail:
                self.app._enqueue_audio({"event_type": "segment_result", "run_id": "fixture", "segment_id": 2,
                                         "text": "", "audio_start_ms": 3000, "audio_end_ms": 3020,
                                         "endpoint_reason": "flush_continuation", "stt_inference_ms": 1,
                                         "delay_after_speech_ms": 100})
            with self.app.lock:
                self.app.audio_done = True
            wait_for(lambda: self.app.state()["session"]["status"] == "ended")
            self.assertFalse(self.app.session.pending)
            self.assertEqual(self.app.session.states["p1"]["status"], "explained")
            saved = json.loads(Path(self.app.output_path).read_text())
            self.assertEqual(saved["states"]["p1"]["status"], "explained")
            types = [e["type"] for e in saved["events"]]
            self.assertLess(types.index("keypoint_judged"), types.index("session_ended"))

    def test_new_materials_clear_ended_display_and_preserve_saved_result(self):
        for action, body in (("script", {"text": "새로운 대본을 설명합니다.", "duration_sec": 30}),
                             ("deck", {**DECK, "title": "새 발표 자료"})):
            self.app.start()
            self.app.command("stop", {})
            wait_for(lambda: self.app.state()["session"]["status"] == "ended")
            old_path = Path(self.app.output_path)
            old_result = old_path.read_text()
            # Invalid replacement must retain the previous result on screen.
            previous = self.app.session
            with self.assertRaises(ValueError):
                self.app.command("script", {"text": "", "duration_sec": 30})
            self.assertIs(self.app.session, previous)
            state = self.app.command(action, body)
            self.assertIsNone(state["session"])
            self.assertIsNone(state["output_path"])
            self.assertEqual(state["audio_status"], "idle")
            self.assertEqual(old_path.read_text(), old_result)
            if action == "script":
                self.assertEqual(state["deck"]["script_text"], body["text"])
            else:
                self.assertEqual(state["deck"]["title"], body["title"])

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

    def test_queued_job_from_previous_visit_skips_model_inference(self):
        calls = []
        class RecordingCoach:
            name = "recording"
            def evaluate(self, job):
                calls.append(job)
                raise AssertionError("obsolete job should never run")
        self.app.coach = RecordingCoach()
        self.app.start()
        with self.app.lock:
            self.app.command("utterance", {"text": "기기에서 음성을 인식합니다"})
            self.app.command("navigate", {"index": 1})
            self.app.command("navigate", {"index": 0})
        wait_for(lambda: self.app.jobs.unfinished_tasks == 0)
        self.assertFalse(calls)
        self.assertFalse(self.app.session.revisions[1]["pending"])
        self.assertTrue(any(e["type"] == "judgment_discarded" for e in self.app.session.events))

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
        events = [e for e in self.app.session.events if e["type"] == "coaching_inference"]
        self.assertEqual(events[-1]["outcome"], "error")
        self.assertEqual(events[-1]["error_type"], "TimeoutError")
        self.assertGreater(events[-1]["wall_ms"], 0)

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

    def test_mic_error_does_not_enable_manual_utterances(self):
        def broken(sink):
            raise RuntimeError("Internal PortAudio error -9986")
        self.app.pipeline_factory = broken
        self.request("/api/start", {"microphone": True}).close()
        wait_for(lambda: self.app.state()["audio_status"] == "error")
        state = self.app.state()
        self.assertEqual(state["audio_input_mode"], "mic")
        self.assertIn("-9986", state["audio_error"])
        self.assertTrue(state["audio_done"])
        with self.assertRaises(HTTPError) as context:
            self.request("/api/utterance", {"text": "가짜 마이크 입력"})
        self.assertEqual(context.exception.code, 400)
        self.assertFalse(self.app.session.segments)
        self.assertEqual(self.app.session.status, "running")

    def test_microphone_picker_and_probe_http_success_or_error_are_visible_in_state(self):
        device = {"id": 2, "name": "Mock USB mic", "sample_rate": 44100}
        listing = {"devices": [device], "default_device_id": 2}
        with patch("src.microphone.input_devices", return_value=listing) as devices:
            with self.request("/api/microphones") as response:
                self.assertEqual(json.load(response), listing)
            devices.assert_called_once_with()
        result = {"status": "ok", "device": device, "callbacks": 4, "peak_dbfs": -18.5}
        with patch("src.microphone.test_input", return_value=result) as probe:
            with self.request("/api/microphone_test", {"device_id": 2}) as response:
                state = json.load(response)
            probe.assert_called_once_with(2)
            self.assertEqual(state["microphone_test"], result)
            self.assertIsNone(state["session"])
        with patch("src.microphone.test_input", side_effect=RuntimeError("No input frames")):
            with self.request("/api/microphone_test", {"device_id": 2}) as response:
                state = json.load(response)
            self.assertEqual(state["microphone_test"]["status"], "error")
            self.assertIn("No input frames", state["microphone_test"]["error"])
            self.assertIn("hint", state["microphone_test"])
            self.assertIsNone(state["audio_error"])

    def test_running_probe_rejects_duplicate_probe_and_presentation_start(self):
        entered, release = threading.Event(), threading.Event()
        results = []
        def forbidden_pipeline(sink):
            raise AssertionError("Presentation must not open capture while the probe is active")
        self.app.pipeline_factory = forbidden_pipeline
        def probe(device_id):
            entered.set()
            if not release.wait(2):
                raise AssertionError("test probe was not released")
            return {"status": "ok", "callbacks": 1, "peak_dbfs": -20}
        def request_probe():
            try:
                with self.request("/api/microphone_test", {"device_id": 0}) as response:
                    results.append(json.load(response))
            except Exception as exc:
                results.append(exc)
        with patch("src.microphone.test_input", side_effect=probe) as mocked:
            thread = threading.Thread(target=request_probe)
            thread.start()
            try:
                self.assertTrue(entered.wait(1))
                with self.request("/api/state") as response:
                    self.assertEqual(json.load(response)["microphone_test"]["status"], "testing")
                for route, body in (("/api/microphone_test", {"device_id": 0}),
                                    ("/api/start", {"microphone": False}),
                                    ("/api/start", {"microphone": True})):
                    with self.assertRaises(HTTPError) as context:
                        self.request(route, body)
                    self.assertEqual(context.exception.code, 400)
                self.assertIsNone(self.app.session)
            finally:
                release.set()
                thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            mocked.assert_called_once_with(0)
        self.assertEqual(results[0]["microphone_test"]["status"], "ok")

    def test_device_validation_rejects_probe_start_and_retry_before_open(self):
        with patch("src.microphone.test_input") as probe, patch.object(self.app, "_launch_audio") as launch:
            for value in (-1, True, "0", 0.5):
                for route, body in (("/api/microphone_test", {"device_id": value}),
                                    ("/api/start", {"microphone": True, "device_id": value}),
                                    ("/api/microphone_retry", {"device_id": value})):
                    with self.assertRaises(HTTPError) as context:
                        self.request(route, body)
                    self.assertEqual(context.exception.code, 400)
            probe.assert_not_called()
            launch.assert_not_called()
            self.assertIsNone(self.app.session)

    def test_retry_keeps_session_and_ignores_old_generation_events(self):
        calls, pipelines = [], []
        active = threading.Event()
        class STT:
            def warm_up(self, seconds):
                pass
        class Pipeline:
            stt = STT()
            def __init__(self, sink):
                self.sink, self.closed = sink, False
            def run_mic(self, **kwargs):
                calls.append(kwargs)
                self.sink({"event_type": "run_start", "clock_origin_perf_counter": time.perf_counter(),
                           "run_id": kwargs["run_id"]})
                if len(calls) == 1:
                    raise RuntimeError("initial native stream failure")
                active.set()
                kwargs["stop_event"].wait(2)
                self.sink({"event_type": "run_summary", "is_lossless": True})
            def close(self):
                self.closed = True
        def factory(sink):
            pipeline = Pipeline(sink)
            pipelines.append(pipeline)
            return pipeline
        self.app.pipeline_factory = factory
        self.app.start(True, device_id=0)
        wait_for(lambda: self.app.audio_done and self.app.audio_status == "error")
        wait_for(lambda: not self.app.audio_thread.is_alive())
        session_id, old_generation = self.app.session.session_id, self.app.audio_generation
        self.app.command("microphone_retry", {"device_id": 2})
        self.assertTrue(active.wait(1))
        wait_for(lambda: self.app.audio_events.unfinished_tasks == 0)
        self.assertEqual(self.app.session.session_id, session_id)
        self.assertEqual(self.app.audio_generation, old_generation + 1)
        self.assertIsNone(self.app.state()["audio_error"])
        self.assertFalse(self.app.state()["audio_done"])
        self.assertEqual(self.app.state()["audio_input_mode"], "mic")
        self.assertNotEqual(calls[0]["run_id"], calls[1]["run_id"])
        self.assertIn(session_id, calls[0]["run_id"])
        self.assertIn(session_id, calls[1]["run_id"])
        with self.app.lock:
            before = (len(self.app.session.events), self.app.audio_origin,
                      self.app.audio_status, self.app.audio_level, len(self.app.session.segments))
            old_events = [
                {"event_type": "run_start", "clock_origin_perf_counter": 0, "run_id": "old"},
                {"event_type": "audio_stream_started", "clock_origin_perf_counter": 0, "run_id": "old"},
                {"event_type": "audio_level", "dbfs": 0, "peak_dbfs": 0, "received_sec": 0},
                {"event_type": "stt_recovery", "stage": "retry"},
                {"event_type": "segment_result", "run_id": "old", "segment_id": 1},
                {"event_type": "error", "error": "old error"},
            ]
            for event in old_events:
                self.app._process_audio({**event, "audio_generation": old_generation})
            after = (len(self.app.session.events), self.app.audio_origin,
                     self.app.audio_status, self.app.audio_level, len(self.app.session.segments))
            self.assertEqual(after, before)
        with patch("src.microphone.test_input") as probe:
            for action in ("microphone_retry", "microphone_test"):
                with self.assertRaises(ValueError):
                    self.app.command(action, {"device_id": 2})
            probe.assert_not_called()
        self.app.command("stop", {})
        wait_for(lambda: self.app.state()["session"]["status"] == "ended")
        self.assertTrue(all(p.closed for p in pipelines))

    def test_retry_requires_running_mic_session_and_finished_probe(self):
        with self.assertRaises(ValueError):
            self.app.command("microphone_retry", {})
        self.app.start(False)
        self.assertEqual(self.app.state()["audio_input_mode"], "manual")
        with self.assertRaises(ValueError):
            self.app.command("microphone_retry", {})
        self.app.command("stop", {})
        wait_for(lambda: self.app.state()["session"]["status"] == "ended")
        with self.assertRaises(ValueError):
            self.app.command("microphone_retry", {})
        self.app.pipeline_factory = lambda sink: (_ for _ in ()).throw(RuntimeError("no microphone"))
        self.app.start(True)
        wait_for(lambda: self.app.audio_done and not self.app.audio_thread.is_alive())
        with self.app.lock:
            self.app.microphone_test = {"status": "testing"}
            generation = self.app.audio_generation
            with self.assertRaises(ValueError):
                self.app.command("microphone_retry", {})
            self.assertEqual(self.app.audio_generation, generation)
            self.app.microphone_test = {"status": "ok"}

    def test_native_path_stays_loading_until_first_frame_started_event(self):
        entered = threading.Event()
        pipelines = []
        selected = {"id": 2, "name": "Mock native mic", "sample_rate": 44100, "blocksize": 7056}
        class STT:
            def warm_up(self, seconds):
                pass
        class Pipeline:
            stt = STT()
            def __init__(self, config, event_sink, terminal_output):
                self.config, self.sink, self.closed = config, event_sink, False
                pipelines.append(self)
            def run_mic(self, **kwargs):
                self.origin, self.run_id = time.perf_counter(), kwargs["run_id"]
                self.sink({"event_type": "run_start", "clock_origin_perf_counter": self.origin,
                           "run_id": self.run_id})
                entered.set()
                kwargs["stop_event"].wait(2)
            def close(self):
                self.closed = True
        with patch("src.microphone.resolve_input", return_value=selected) as resolve, \
             patch("src.pipeline.SpeechPipeline", Pipeline):
            self.app.start(True, device_id=2)
            self.assertTrue(entered.wait(1))
            wait_for(lambda: self.app.audio_events.unfinished_tasks == 0)
            self.assertEqual(self.app.state()["audio_status"], "loading")
            self.assertEqual(self.app.state()["microphone"]["name"], selected["name"])
            pipeline = pipelines[0]
            self.assertEqual(pipeline.config.audio.device_index, 2)
            self.assertEqual(pipeline.config.audio.device_sample_rate, 44100)
            self.assertEqual(pipeline.config.audio.chunk_size_samples, 7056)
            # The native path also captures its generation in the event sink,
            # so callbacks from a superseded stream cannot activate capture.
            with self.app.lock:
                generation = self.app.audio_generation
                self.app.audio_generation += 1
            pipeline.sink({"event_type": "audio_stream_started", "clock_origin_perf_counter": pipeline.origin,
                           "run_id": pipeline.run_id})
            pipeline.sink({"event_type": "audio_level", "dbfs": -10, "peak_dbfs": -5, "received_sec": 1})
            wait_for(lambda: self.app.audio_events.unfinished_tasks == 0)
            self.assertEqual(self.app.audio_status, "loading")
            self.assertIsNone(self.app.audio_level)
            with self.app.lock:
                self.app.audio_generation = generation
            pipeline.sink({"event_type": "audio_stream_started", "clock_origin_perf_counter": pipeline.origin,
                           "run_id": pipeline.run_id, "device_name": selected["name"], "sample_rate": 44100})
            wait_for(lambda: self.app.audio_status == "recording")
            self.app.command("stop", {})
            wait_for(lambda: self.app.state()["session"]["status"] == "ended")
            resolve.assert_called_once_with(2)
            self.assertTrue(pipeline.closed)

    def test_late_started_or_recovery_events_cannot_hide_error_or_completed_capture(self):
        self.app.start(False)
        with self.app.lock:
            for status, done in (("error", False), ("error", True), ("stopped", True)):
                self.app.audio_status, self.app.audio_done = status, done
                for event in ({"event_type": "audio_stream_started", "clock_origin_perf_counter": time.perf_counter(), "run_id": "late"},
                              {"event_type": "stt_recovery", "stage": "retry"},
                              {"event_type": "stt_recovery", "stage": "resumed"}):
                    self.app._process_audio(event)
                    self.assertEqual(self.app.audio_status, status)
            self.app.audio_done = True


if __name__ == "__main__":
    unittest.main()
