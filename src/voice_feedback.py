"""Bounded asynchronous macOS local speech output. No shell or network voices."""
import queue
import itertools
import shutil
import subprocess
import threading
import time


class VoiceFeedback:
    def __init__(self, emit, valid, executable=None):
        self.executable = executable or (shutil.which("say") if __import__('sys').platform == 'darwin' else None)
        self.emit, self.valid = emit, valid
        self.queue = queue.PriorityQueue(maxsize=8)
        self.sequence = itertools.count()
        self.stop = threading.Event()
        self.process = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def enqueue(self, session, alert):
        if not self.executable:
            self.emit(session, "voice_failed", alert, reason="로컬 음성 출력은 macOS say가 필요합니다.")
            return
        try:
            self.queue.put_nowait((-alert["priority"], alert["shown_sec"], next(self.sequence), session, dict(alert)))
            self.emit(session, "voice_queued", alert)
        except queue.Full:
            self.emit(session, "voice_cancelled", alert, reason="queue_full")

    def _run(self):
        while not self.stop.is_set():
            try:
                _, _, _, session, alert = self.queue.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                if not self.valid(session, alert):
                    self.emit(session, "voice_cancelled", alert, reason="expired_or_changed")
                    continue
                self.emit(session, "voice_started", alert)
                self.process = subprocess.Popen([self.executable, "-v", "Yuna", alert["message"][:250]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                began = time.monotonic()
                cancelled = False
                while self.process.poll() is None:
                    if self.stop.wait(.05) or not self.valid(session, alert) or time.monotonic()-began > 20:
                        self.process.terminate()
                        cancelled = True
                        break
                try:
                    code = self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    code = self.process.wait(timeout=2)
                self.emit(session, "voice_cancelled" if cancelled else "voice_completed" if code == 0 else "voice_failed", alert, returncode=code)
            except Exception as exc:
                self.emit(session, "voice_failed", alert, reason=str(exc))
            finally:
                self.process = None
                self.queue.task_done()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)
        while True:
            try:
                _, _, _, session, alert = self.queue.get_nowait()
            except queue.Empty:
                break
            self.emit(session, "voice_cancelled", alert, reason="shutdown")
            self.queue.task_done()
