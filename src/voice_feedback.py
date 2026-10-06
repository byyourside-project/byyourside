"""Bounded asynchronous macOS local speech output. No shell or network voices."""
from dataclasses import dataclass, field
import itertools
import queue
import shutil
import subprocess
import threading
import time


@dataclass
class _PendingVoice:
    session: object
    alert: dict
    ready: threading.Event = field(default_factory=threading.Event)
    cancel_reason: str | None = None
    cancel_reported: bool = False


class VoiceFeedback:
    def __init__(self, emit, valid, executable=None, *, max_duration_sec=20.0, close_timeout_sec=3.0):
        self.executable = executable or (shutil.which("say") if __import__('sys').platform == 'darwin' else None)
        self.emit, self.valid = emit, valid
        self.max_duration_sec = max_duration_sec
        self.close_timeout_sec = close_timeout_sec
        self.queue = queue.PriorityQueue(maxsize=8)
        self.sequence = itertools.count()
        self.stop = threading.Event()
        self.process = None
        self._state_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self.last_callback_error = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _emit(self, session, kind, alert, **fields):
        # Callers may acquire the application's lock. Never invoke them while
        # holding our state/cleanup mutexes, including cancellation callbacks.
        try:
            self.emit(session, kind, alert, **fields)
            return True
        except Exception as exc:
            with self._state_lock:
                self.last_callback_error = f"{type(exc).__name__}: {exc}"
            return False

    def _cancel(self, item, reason):
        with self._state_lock:
            if item.cancel_reason is None:
                item.cancel_reason = reason
            report = item.ready.is_set() and not item.cancel_reported
            if report:
                item.cancel_reported = True
            reason = item.cancel_reason
        if report:
            self._emit(item.session, "voice_cancelled", item.alert, reason=reason)

    def enqueue(self, session, alert):
        item = _PendingVoice(session, dict(alert))
        failure = None
        with self._state_lock:
            if self.stop.is_set():
                failure = "shutdown"
            elif not self.executable:
                failure = "unavailable"
            else:
                try:
                    self.queue.put_nowait((-alert["priority"], alert["shown_sec"], next(self.sequence), item))
                except queue.Full:
                    failure = "queue_full"
        if failure:
            self._emit(session, "voice_failed" if failure == "unavailable" else "voice_cancelled", alert,
                       reason="로컬 음성 출력은 macOS say가 필요합니다." if failure == "unavailable" else failure)
            return False
        announced = self._emit(session, "voice_queued", alert)
        with self._state_lock:
            if not announced and item.cancel_reason is None:
                item.cancel_reason = "event_callback_failed"
            item.ready.set()
            cancellation = item.cancel_reason
        if cancellation:
            self._cancel(item, cancellation)
        return True

    def _reap(self, process, deadline=None):
        # Only subprocess operations run under this mutex. Serializing the
        # worker and close() avoids concurrent wait/kill ownership races.
        deadline = time.monotonic() + 1.0 if deadline is None else deadline
        if not self._cleanup_lock.acquire(timeout=max(0, deadline - time.monotonic())):
            raise TimeoutError("음성 출력 프로세스 회수가 진행 중입니다.")
        try:
            try:
                alive = process.poll() is None
            except Exception:
                alive = True
            force_kill = False
            if alive:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                except Exception:
                    force_kill = True
            if not force_kill:
                try:
                    return process.wait(timeout=max(0, min(.25, deadline - time.monotonic())))
                except Exception:
                    # A failed wait must not leave a child behind either.
                    force_kill = True
            if force_kill:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                return process.wait(timeout=max(0, min(.75, deadline - time.monotonic())))
        finally:
            self._cleanup_lock.release()

    def _run(self):
        while not self.stop.is_set():
            try:
                _, _, _, item = self.queue.get(timeout=.1)
            except queue.Empty:
                continue
            process = None
            try:
                while not item.ready.wait(.05):
                    if self.stop.is_set():
                        self._cancel(item, "shutdown")
                        break
                if self.stop.is_set():
                    self._cancel(item, "shutdown")
                    continue
                with self._state_lock:
                    cancellation = item.cancel_reason
                if cancellation:
                    self._cancel(item, cancellation)
                    continue
                if not self.valid(item.session, item.alert):
                    self._cancel(item, "expired_or_changed")
                    continue
                if self.stop.is_set():
                    self._cancel(item, "shutdown")
                    continue
                if not self._emit(item.session, "voice_started", item.alert):
                    self._cancel(item, "event_callback_failed")
                    continue
                # started callbacks can change session/voice configuration.
                if not self.valid(item.session, item.alert):
                    self._cancel(item, "expired_or_changed")
                    continue
                if self.stop.is_set():
                    self._cancel(item, "shutdown")
                    continue
                process = subprocess.Popen([self.executable, "-v", "Yuna", item.alert["message"][:250]],
                                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)
                with self._state_lock:
                    self.process = process
                began, cancelled = time.monotonic(), None
                while process.poll() is None:
                    if self.stop.wait(.05):
                        cancelled = "shutdown"
                        break
                    if not self.valid(item.session, item.alert):
                        cancelled = "expired_or_changed"
                        break
                    if time.monotonic() - began > self.max_duration_sec:
                        cancelled = "timeout"
                        break
                code = self._reap(process)
                process = None
                with self._state_lock:
                    self.process = None
                if self.stop.is_set():
                    cancelled = "shutdown"
                elif not self.valid(item.session, item.alert):
                    cancelled = "expired_or_changed"
                if cancelled:
                    self._cancel(item, cancelled)
                else:
                    self._emit(item.session, "voice_completed" if code == 0 else "voice_failed", item.alert, returncode=code)
            except Exception as exc:
                cleanup_error = None
                if process is not None:
                    try:
                        self._reap(process)
                        process = None
                    except Exception as cleanup_exc:
                        cleanup_error = str(cleanup_exc)
                        # Preserve ownership of the child and prevent another
                        # queued process from replacing an unreaped handle.
                        self.stop.set()
                self._emit(item.session, "voice_failed", item.alert, reason=str(exc), cleanup_error=cleanup_error)
            finally:
                with self._state_lock:
                    # Keep an unreaped process reachable so close() can retry.
                    self.process = process
                self.queue.task_done()

    def close(self):
        deadline = time.monotonic() + self.close_timeout_sec
        self.stop.set()
        with self._state_lock:
            process = self.process
        cleanup_error = None
        if process is not None:
            try:
                self._reap(process, deadline)
            except Exception as exc:
                cleanup_error = exc
        while True:
            try:
                _, _, _, item = self.queue.get_nowait()
            except queue.Empty:
                break
            self._cancel(item, "shutdown")
            self.queue.task_done()
        self.thread.join(timeout=max(0, deadline - time.monotonic()))
        # Popen may have been in flight when shutdown took its first snapshot.
        with self._state_lock:
            process = self.process
        if process is not None:
            try:
                self._reap(process, deadline)
                with self._state_lock:
                    if self.process is process:
                        self.process = None
                cleanup_error = None
            except Exception as exc:
                cleanup_error = exc
        if cleanup_error is not None:
            raise RuntimeError(f"음성 출력 프로세스를 종료하지 못했습니다: {cleanup_error}") from cleanup_error
        if self.thread.is_alive():
            raise RuntimeError("음성 출력 작업이 종료 제한 시간 안에 끝나지 않았습니다.")
