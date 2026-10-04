"""Loopback-only review UI. Audio and reports remain in recordings/reviews/; recordings/reviews.db indexes them."""
import json
import mimetypes
import re
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from src.review_analysis import build_review
from src.review_audio import model_paths
from src.review_db import ReviewDB

MAX_UPLOAD = 80 * 1024 * 1024
EXTENSIONS = {'.wav', '.m4a', '.mp3', '.flac', '.ogg', '.webm', '.aac'}
MAX_TITLE = 80


def clean_title(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > MAX_TITLE:
        raise ValueError(f'제목은 1~{MAX_TITLE}자로 입력해 주세요.')
    return value.strip()


class ReviewApp:
    def __init__(self, root, models_dir=None):
        self.root = Path(root).resolve()
        self.storage = self.root / 'recordings' / 'reviews'
        self.storage.mkdir(parents=True, exist_ok=True)
        self.models_dir = Path(models_dir or self.root / 'models').resolve()
        self.jobs = {}
        self.analysis_lock = threading.Lock()
        self.data_lock = threading.Lock()
        self.db = ReviewDB(self.root / 'recordings' / 'reviews.db')
        self.db.sync(self.storage)

    def folder(self, job_id):
        if not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise ValueError('잘못된 기록 번호입니다.')
        return self.storage / job_id

    def status(self):
        return {'models_ready': all(p.is_file() for p in model_paths(self.models_dir)),
                'demo_available': (self.root / 'audio/pilot_001/asr_draft.json').is_file(),
                'busy': self.analysis_lock.locked()}

    def save_report(self, job_id, raw, options):
        report = build_review(raw, options.get('script', ''), options.get('target_seconds'), options.get('edits'))
        if options.get('title'):
            report['title'] = options['title']
        report.update(id=job_id, audio_url=f'/api/media/{job_id}', download_url=f'/api/export/{job_id}')
        folder = self.folder(job_id)
        self.write_json(folder / 'options.json', options)
        self.write_json(folder / 'report.json', report)
        self.db.upsert(report)
        return report

    def delete_review(self, job_id):
        folder = self.folder(job_id)
        if self.jobs.get(job_id, {}).get('status') == 'running':
            raise ValueError('분석 중인 기록은 삭제할 수 없습니다.')
        if not folder.is_dir() and not self.db.exists(job_id):
            raise FileNotFoundError('기록을 찾지 못했습니다.')
        if folder.is_dir():
            shutil.rmtree(folder)  # Files first: if this fails the record stays listed and can be retried.
        self.db.delete(job_id)
        self.jobs.pop(job_id, None)

    @staticmethod
    def write_json(path, data):
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        temporary.replace(path)

    def demo(self):
        pilot = self.root / 'audio' / 'pilot_001'
        if not (pilot / 'asr_draft.json').is_file():
            raise ValueError('이 PC에는 예제 녹음이 없습니다. 음성 파일을 선택해 주세요.')
        asr = json.loads((pilot / 'asr_draft.json').read_text(encoding='utf-8'))
        metrics = json.loads((pilot / 'file_metrics.json').read_text(encoding='utf-8'))
        raw = {'title': '첫 연습 · 55초 녹음', 'duration': metrics['duration_sec'],
               'peak_dbfs': metrics['peak_dbfs'], 'engine': asr['engine'],
               'segments': [{'start': s['vad_start_sec'], 'end': s['vad_end_sec'], 'text': s['text']}
                            for s in asr['segments']]}
        reference = pilot / 'speaker_attested_reference.txt'
        options = {'script': reference.read_text(encoding='utf-8') if reference.exists() else '',
                   'target_seconds': None, 'edits': {}}
        job_id = uuid.uuid4().hex
        folder = self.folder(job_id)
        folder.mkdir()
        shutil.copyfile(pilot / 'recording_16k.wav', folder / 'audio.wav')
        self.write_json(folder / 'raw.json', raw)
        return self.save_report(job_id, raw, options)

    def start_analysis(self, job_id, options):
        folder = self.folder(job_id)
        if not (folder / 'upload.json').is_file():
            raise ValueError('먼저 음성 파일을 선택해 주세요.')
        # Validate before spawning expensive inference.
        build_review({'duration': 1, 'segments': []}, options.get('script', ''), options.get('target_seconds'))
        if not self.status()['models_ready']:
            raise ValueError('모델 준비가 필요합니다. 실행 안내의 모델 다운로드 단계를 진행해 주세요.')
        if not self.analysis_lock.acquire(blocking=False):
            raise ValueError('다른 녹음을 분석하고 있습니다. 완료 후 다시 시도해 주세요.')
        try:
            upload = json.loads((folder / 'upload.json').read_text(encoding='utf-8'))
            request = {'source': upload['source'], 'title': upload['title'], 'models_dir': str(self.models_dir)}
            self.write_json(folder / 'request.json', request)
            self.jobs[job_id] = {'status': 'running', 'message': '말한 구간을 찾고 대본과 비교하고 있어요.'}
            threading.Thread(target=self._analyze, args=(job_id, options), daemon=True).start()
        except Exception:
            self.analysis_lock.release()
            raise

    def _analyze(self, job_id, options):
        folder = self.folder(job_id)
        try:
            result = subprocess.run([sys.executable, '-m', 'src.review_worker', str(folder)],
                                    cwd=self.root, capture_output=True, timeout=240)
            if result.returncode:
                error_path = folder / 'error.json'
                error = json.loads(error_path.read_text(encoding='utf-8'))['error'] if error_path.exists() else '음성 분석에 실패했습니다. 모델과 실행 환경을 확인해 주세요.'
                raise ValueError(error)
            raw = json.loads((folder / 'raw.json').read_text(encoding='utf-8'))
            self.save_report(job_id, raw, options)
            self.jobs[job_id] = {'status': 'done'}
        except subprocess.TimeoutExpired:
            self.jobs[job_id] = {'status': 'error', 'error': '분석 제한 시간(4분)을 넘었습니다. 더 짧은 녹음으로 시도해 주세요.'}
        except Exception as exc:
            self.jobs[job_id] = {'status': 'error', 'error': str(exc)}
        finally:
            self.analysis_lock.release()


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(30)

        def log_message(self, format, *args):
            pass  # Do not put transcript or source filenames in console logs.

        def trusted_request(self):
            port = self.server.server_address[1]
            allowed = {f'127.0.0.1:{port}', f'localhost:{port}'}
            if self.headers.get('Host') not in allowed:
                return False
            origin = self.headers.get('Origin')
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme != 'http' or parsed.netloc not in allowed:
                    return False
            return True

        def send_json(self, data, status=200):
            payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(payload)

        def read_body(self, maximum):
            try:
                length = int(self.headers.get('Content-Length', '-1'))
            except ValueError:
                raise ValueError('파일 크기를 확인하지 못했습니다.')
            if length < 0 or length > maximum:
                raise ValueError('파일 또는 요청이 너무 큽니다. 녹음은 80MB 이하로 선택해 주세요.')
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError('전송이 중단되었습니다. 다시 시도해 주세요.')
            return body

        def json_body(self):
            value = json.loads(self.read_body(256 * 1024))
            if not isinstance(value, dict):
                raise ValueError('요청 형식이 올바르지 않습니다.')
            return value

        def serve_file(self, path, audio=False, download=False):
            if not path.is_file():
                return self.send_json({'error': '파일을 찾지 못했습니다.'}, 404)
            size = path.stat().st_size
            start, end, status = 0, size - 1, 200
            request_range = self.headers.get('Range') if audio else None
            if request_range:
                match = re.fullmatch(r'bytes=(\d+)-(\d*)', request_range)
                if not match or int(match[1]) >= size:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{size}')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                start = int(match[1])
                end = min(int(match[2]), size - 1) if match[2] else size - 1
                if end < start:
                    return self.send_json({'error': '잘못된 재생 범위입니다.'}, 416)
                status = 206
            self.send_response(status)
            self.send_header('Content-Type', 'audio/wav' if audio else mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(end - start + 1))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'")
            if audio:
                self.send_header('Accept-Ranges', 'bytes')
            if status == 206:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            if download:
                self.send_header('Content-Disposition', 'attachment; filename="presentation-review.json"')
            self.end_headers()
            with path.open('rb') as source:
                source.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    block = source.read(min(65536, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)

        def do_GET(self):
            if not self.trusted_request():
                return self.send_json({'error': '이 PC에서 열린 화면으로 접속해 주세요.'}, 403)
            route = urlsplit(self.path).path
            try:
                if route in ('/', '/app.js', '/styles.css'):
                    return self.serve_file(app.root / 'web' / ('index.html' if route == '/' else route[1:]))
                if route == '/api/status':
                    return self.send_json(app.status())
                if route == '/api/reviews':
                    return self.send_json({'reviews': app.db.list_reviews()})
                match = re.fullmatch(r'/api/(jobs|media|export)/([a-f0-9]{32})', route)
                if match:
                    kind, job_id = match.groups()
                    folder = app.folder(job_id)
                    if kind == 'jobs':
                        state = app.jobs.get(job_id)
                        if state and state['status'] != 'done':
                            return self.send_json(state)
                        if (folder / 'report.json').is_file():
                            return self.send_json({'status': 'done', 'report': json.loads((folder / 'report.json').read_text(encoding='utf-8'))})
                        return self.send_json({'error': '분석 기록을 찾지 못했습니다.'}, 404)
                    if not (folder / 'report.json').is_file():
                        return self.send_json({'error': '분석 결과가 아직 없습니다.'}, 404)
                    return self.serve_file(folder / ('audio.wav' if kind == 'media' else 'report.json'), audio=kind == 'media', download=kind == 'export')
                return self.send_json({'error': '페이지를 찾지 못했습니다.'}, 404)
            except (ValueError, OSError) as exc:
                return self.send_json({'error': str(exc)}, 400)

        def do_POST(self):
            if not self.trusted_request():
                return self.send_json({'error': '이 PC에서 열린 화면으로 접속해 주세요.'}, 403)
            route = urlsplit(self.path).path
            try:
                if route == '/api/demo':
                    with app.data_lock:
                        return self.send_json(app.demo())
                if route == '/api/upload':
                    suffix = self.headers.get('X-Audio-Extension', '').lower()
                    if suffix not in EXTENSIONS:
                        raise ValueError('WAV, M4A, MP3, FLAC, OGG, WEBM, AAC 파일을 선택해 주세요.')
                    payload = self.read_body(MAX_UPLOAD)
                    if not payload:
                        raise ValueError('비어 있는 파일입니다.')
                    job_id = uuid.uuid4().hex
                    folder = app.folder(job_id)
                    folder.mkdir()
                    (folder / ('source' + suffix)).write_bytes(payload)
                    app.write_json(folder / 'upload.json', {'source': 'source' + suffix,
                                                            'title': f'연습 {datetime.now():%m/%d %H:%M}'})
                    return self.send_json({'id': job_id}, 201)
                if route == '/api/analyze':
                    data = self.json_body()
                    job_id = data.get('id', '')
                    app.start_analysis(job_id, {'script': data.get('script', ''), 'target_seconds': data.get('target_seconds'), 'edits': {}})
                    return self.send_json({'id': job_id, 'status': 'running'}, 202)
                match = re.fullmatch(r'/api/reviews/([a-f0-9]{32})', route)
                if match:
                    data = self.json_body()
                    if 'title' in data:
                        data['title'] = clean_title(data['title'])
                    with app.data_lock:
                        folder = app.folder(match[1])
                        raw = json.loads((folder / 'raw.json').read_text(encoding='utf-8'))
                        options = json.loads((folder / 'options.json').read_text(encoding='utf-8'))
                        for key in ('script', 'target_seconds', 'edits', 'title'):
                            if key in data:
                                options[key] = data[key]
                        return self.send_json(app.save_report(match[1], raw, options))
                return self.send_json({'error': '요청을 찾지 못했습니다.'}, 404)
            except (ValueError, OSError, TypeError) as exc:
                return self.send_json({'error': str(exc)}, 400)

        def do_DELETE(self):
            if not self.trusted_request():
                return self.send_json({'error': '이 PC에서 열린 화면으로 접속해 주세요.'}, 403)
            match = re.fullmatch(r'/api/reviews/([a-f0-9]{32})', urlsplit(self.path).path)
            if not match:
                return self.send_json({'error': '요청을 찾지 못했습니다.'}, 404)
            try:
                with app.data_lock:
                    app.delete_review(match[1])
                return self.send_json({'id': match[1], 'deleted': True})
            except FileNotFoundError as exc:
                return self.send_json({'error': str(exc)}, 404)
            except (ValueError, OSError) as exc:
                message = str(exc) if isinstance(exc, ValueError) else '파일을 지우지 못했습니다. 재생 중이면 멈춘 뒤 다시 시도해 주세요.'
                return self.send_json({'error': message}, 400)
    return Handler


def serve(root, port=8765, models_dir=None):
    app = ReviewApp(root, models_dir)
    server = ThreadingHTTPServer(('127.0.0.1', port), make_handler(app))
    print(f'발표 연습 화면: http://127.0.0.1:{server.server_address[1]}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
