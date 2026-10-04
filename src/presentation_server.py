"""Local-only presentation UI and runtime. Heavy inference never holds the UI lock."""
import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from src.presentation import Session, PhraseCoach, validate_deck


class PresentationApp:
    def __init__(self, deck, coach=None, output_dir="logs/presentation_sessions", pipeline_factory=None):
        self.deck = validate_deck(deck)
        self.coach = coach or PhraseCoach()
        self.output_dir = output_dir
        self.pipeline_factory = pipeline_factory
        self.lock = threading.RLock()
        self.session = None
        self.audio_status = "idle"
        self.audio_thread = None
        self.audio_stop = threading.Event()
        self.audio_events = queue.Queue(maxsize=512)
        self.jobs = queue.Queue(maxsize=8)
        self.shutdown = threading.Event()
        self.audio_origin = 0.0
        self.audio_done = True
        self.output_path = None
        self.event_overflow = threading.Event()
        self.last_saved = 0.0
        self.quality_flags = set()
        self.workers = [threading.Thread(target=self._pump, daemon=True), threading.Thread(target=self._coach_loop, daemon=True)]
        for worker in self.workers:
            worker.start()

    def state(self):
        with self.lock:
            return {"deck": self.deck, "session": self.session.snapshot() if self.session else None,
                    "audio_status": self.audio_status, "coach": self.coach.name, "output_path": self.output_path}

    def start(self, microphone=False):
        with self.lock:
            if self.session and self.session.status != "ended":
                raise ValueError("이미 진행 중인 발표가 있습니다.")
            self.session = Session(self.deck)
            self.session._event("coach_configured", provider=self.coach.name,
                                model=getattr(self.coach, "model", None),
                                model_info=getattr(self.coach, "model_info", None),
                                warm_up_metrics=getattr(self.coach, "warm_up_metrics", None),
                                timeout_sec=getattr(self.coach, "timeout", None))
            self.output_path = self.session.save(self.output_dir)
            self.audio_stop.clear()
            self.audio_done = not microphone
            self.audio_origin = 0.0
            self.quality_flags.clear()
            self.audio_status = "loading" if microphone else "manual"
            if microphone:
                self.audio_thread = threading.Thread(target=self._audio_loop, args=(self.session,), daemon=True)
                self.audio_thread.start()
        return self.state()

    def _enqueue_audio(self, event):
        try:
            self.audio_events.put_nowait(event)
        except queue.Full:
            self.event_overflow.set()

    def _audio_loop(self, session):
        pipeline = None
        try:
            if self.pipeline_factory is None:
                from src.pipeline import SpeechPipeline
                pipeline = SpeechPipeline(event_sink=self._enqueue_audio, terminal_output=False)
            else:
                pipeline = self.pipeline_factory(self._enqueue_audio)
            pipeline.stt.warm_up(.5)
            if not self.audio_stop.is_set():
                with self.lock:
                    self.audio_status = "recording"
                pipeline.run_mic(duration_seconds=7200, run_id=f"presentation_{session.session_id}",
                                 stop_event=self.audio_stop, snapshot_interval_sec=10)
        except Exception as exc:
            # Failures remain visible and are persisted even if initialization fails.
            with self.lock:
                session.issue(f"마이크/STT 오류: {exc}")
                self.audio_status = "error"
        finally:
            try:
                if pipeline is not None:
                    pipeline.close()
            except Exception as exc:
                with self.lock:
                    session.issue(f"음성 처리 종료 오류: {exc}")
            with self.lock:
                self.audio_done = True
                if self.audio_status != "error":
                    self.audio_status = "stopped"

    def _schedule(self, session, job):
        if job is None:
            return
        try:
            self.jobs.put_nowait((session, job))
        except queue.Full:
            session.fail_job(job, "코칭 요청이 밀려 판단을 보류했습니다.")

    def _coach_loop(self):
        while not self.shutdown.is_set() or not self.jobs.empty():
            try:
                session, job = self.jobs.get(timeout=.1)
            except queue.Empty:
                continue
            began = None
            try:
                with self.lock:
                    stale = not session.job_is_current(job)
                    if stale:
                        session.discard_job(job)
                if not stale:
                    began = time.perf_counter()
                    response = self.coach.evaluate(job)
                    with self.lock:
                        session.apply(job, response)
                        session._event("coaching_inference", provider=self.coach.name,
                                       outcome="response_validated",
                                       version=job["version"], revision=job["revision"],
                                       wall_ms=(time.perf_counter() - began) * 1000,
                                       metrics=getattr(self.coach, "last_metrics", {}))
            except Exception as exc:
                with self.lock:
                    session.fail_job(job, f"내용 판단 오류: {exc}")
                    if began is not None:
                        session._event("coaching_inference", provider=self.coach.name,
                                       outcome="error", error_type=type(exc).__name__,
                                       version=job["version"], revision=job["revision"],
                                       wall_ms=(time.perf_counter() - began) * 1000)
            finally:
                self.jobs.task_done()

    def _process_audio(self, event):
        session = self.session
        if not session:
            return
        kind = event.get("event_type")
        if kind == "run_start":
            self.audio_origin = event["clock_origin_perf_counter"] - session.origin
            session._event("audio_started", offset_sec=self.audio_origin, run_id=event["run_id"])
        elif kind == "segment_result":
            self._schedule(session, session.ingest({
                "segment_id": f"{event['run_id']}:{event['segment_id']}", "text": event["text"],
                "start_sec": self.audio_origin + event["audio_start_ms"] / 1000,
                "end_sec": self.audio_origin + event["audio_end_ms"] / 1000,
                "status": event.get("status", "OK"), "endpoint_reason": event["endpoint_reason"],
                "stt_inference_ms": event["stt_inference_ms"],
                "estimated_feedback_delay_ms": event["delay_after_speech_ms"]}))
        elif kind == "run_summary":
            session._event("audio_summary", summary=event)
            if not event.get("is_lossless", True) or event.get("overrun_count", 0):
                session.issue("음성 캡처 또는 구간 손실이 발생했습니다. 원시 로그를 확인해 주세요.")
        elif kind == "capture_snapshot":
            session._event("capture_snapshot", snapshot=event)
            for field in ("overrun_count", "dropped_chunks", "dropped_segments"):
                if event.get(field, 0) and field not in self.quality_flags:
                    self.quality_flags.add(field)
                    session.issue(f"음성 입력 손실 감지: {field}={event[field]}")
        elif kind == "error":
            session.issue(f"음성 처리 오류: {event.get('error', 'unknown')}")

    def _pump(self):
        while not self.shutdown.wait(.05):
            with self.lock:
                session = self.session
                if not session:
                    continue
                if self.event_overflow.is_set():
                    self.event_overflow.clear()
                    session.issue("음성 이벤트 큐가 가득 차 전달 손실이 발생했습니다. 원시 STT 로그는 별도로 보존됩니다.")
                for _ in range(64):
                    try:
                        event = self.audio_events.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        self._process_audio(event)
                    except Exception as exc:
                        session.issue(f"음성 이벤트 오류: {exc}")
                    finally:
                        self.audio_events.task_done()
                session.tick()
                if session.status == "stopping" and self.audio_done and self.audio_events.empty() and self.jobs.unfinished_tasks == 0:
                    session.finish()
                    self.output_path = session.save(self.output_dir)
                if session.status == "running" and time.perf_counter() - self.last_saved >= 1:
                    self.output_path = session.save(self.output_dir)
                    self.last_saved = time.perf_counter()

    def command(self, action, body):
        if action == "start":
            if not isinstance(body.get("microphone", False), bool):
                raise ValueError("microphone은 bool이어야 합니다.")
            return self.start(body.get("microphone", False))
        with self.lock:
            if action == "deck":
                if self.session and self.session.status != "ended":
                    raise ValueError("발표 종료 후 자료를 변경해 주세요.")
                self.deck = validate_deck(body)
            elif action in ("navigate", "stop", "utterance"):
                if not self.session or self.session.status != "running":
                    raise ValueError("발표를 먼저 시작해 주세요.")
                if action == "navigate":
                    self.session.navigate(body.get("index"))
                elif action == "stop":
                    self.session.stop()
                    self.audio_stop.set()
                else:
                    if self.audio_status in ("loading", "recording"):
                        raise ValueError("마이크 모드에서는 직접 전사를 입력할 수 없습니다.")
                    now = self.session.elapsed()
                    self._schedule(self.session, self.session.ingest({
                        "segment_id": body.get("segment_id", f"manual:{len(self.session.segments) + 1}"),
                        "text": body.get("text"), "start_sec": body.get("start_sec", max(self.session.visits[-1]["start_sec"], now - 4)),
                        "end_sec": body.get("end_sec", now), "status": body.get("status", "OK"),
                        "endpoint_reason": body.get("endpoint_reason", "silence")}))
            else:
                raise ValueError("알 수 없는 요청입니다.")
        return self.state()

    def close(self):
        with self.lock:
            if self.session and self.session.status == "running":
                self.session.stop()
            self.audio_stop.set()
        if self.audio_thread:
            self.audio_thread.join(timeout=15)
        # Let the pump drain final VAD/STT events before ending the session.
        deadline = time.perf_counter() + 10
        while self.session and self.session.status == "stopping" and time.perf_counter() < deadline:
            time.sleep(.05)
        with self.lock:
            if self.session:
                if self.session.status != "ended":
                    self.session.issue("서버 종료 시 처리 완료를 기다리는 제한 시간을 초과했습니다.")
                    self.session.finish()
                self.output_path = self.session.save(self.output_dir)
        self.shutdown.set()
        for worker in self.workers:
            worker.join(timeout=3)


def make_server(app, port=8765):
    root = Path(__file__).resolve().parent.parent / "web" / "presentation"
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_json(self, status, data):
            payload = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.headers.get("Host") not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
                self.send_json(403, {"error": "로컬 화면에서 요청해 주세요."})
                return
            route = urlparse(self.path).path
            if route == "/api/state":
                self.send_json(200, app.state())
            elif route == "/api/export":
                with app.lock:
                    if not app.session:
                        self.send_json(404, {"error": "저장할 세션이 없습니다."})
                    else:
                        self.send_json(200, {**app.session.snapshot(), "segments": app.session.segments,
                                             "events": app.session.events, "alerts": app.session.alerts,
                                             "active_alerts": app.session.snapshot()["alerts"], "schema_version": 1})
            elif route in ("/", "/app.js", "/style.css"):
                name = "index.html" if route == "/" else route[1:]
                payload = (root / name).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8"}[name])
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(payload)
            else:
                self.send_json(404, {"error": "없는 경로입니다."})

        def do_POST(self):
            # Reject cross-origin requests and DNS rebinding on loopback.
            expected_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            host = self.headers.get("Host")
            origin = self.headers.get("Origin")
            if host not in expected_hosts or (origin and origin not in {f"http://{h}" for h in expected_hosts}):
                self.send_json(403, {"error": "로컬 화면에서 요청해 주세요."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1048576:
                    raise ValueError("요청 크기가 올바르지 않습니다.")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("JSON 객체가 필요합니다.")
                self.send_json(200, app.command(urlparse(self.path).path.removeprefix("/api/"), body))
            except (ValueError, KeyError, TypeError) as exc:
                self.send_json(400, {"error": str(exc)})
            except Exception as exc:
                self.send_json(500, {"error": f"서버 처리 실패: {exc}"})
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
