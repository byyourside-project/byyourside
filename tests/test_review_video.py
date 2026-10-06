import hashlib
import http.client
import json
import subprocess
import tempfile
import threading
import unittest
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import imageio_ffmpeg
import numpy as np

from src.review_media import prepare_media, probe_media
from src.review_server import ReviewApp, make_handler
from src.review_worker import main as run_worker


class ReviewVideoHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = ReviewApp(self.temp.name)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, route, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=5)
        connection.request(method, route, body, headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def saved_video(self):
        job_id = 'a' * 32
        folder = self.app.folder(job_id)
        folder.mkdir()
        video = b'video-' + bytes(range(100))
        (folder / 'video.mp4').write_bytes(video)
        (folder / 'audio.wav').write_bytes(b'audio-original')
        raw = {'duration': 8, 'title': '영상 연습', 'segments': [
            {'start': 1, 'end': 7, 'text': '발표를 준비하면서 겪는 어려움을 함께 살펴보겠습니다.'}],
            'media': {'kind': 'video', 'duration': 8, 'has_audio': True, 'has_video': True},
            'gaze': {'status': 'unavailable', 'label': '시선 분석 이용 불가',
                     'summary': '얼굴 모델 미설치', 'camera_facing_ratio': None, 'intervals': []}}
        self.app.write_json(folder / 'raw.json', raw)
        self.app.save_report(job_id, raw, {'script': '', 'target_seconds': None, 'edits': {}})
        return job_id, folder, video

    def test_video_upload_uses_sanitized_storage_name_and_keeps_original_bytes(self):
        body = b'original-video-bytes'
        status, _, data = self.request('POST', '/api/upload', body, {'X-Audio-Extension': '.MOV'})
        self.assertEqual(status, 201)
        folder = self.app.folder(json.loads(data)['id'])
        self.assertEqual((folder / 'source.mov').read_bytes(), body)
        self.assertEqual(json.loads((folder / 'upload.json').read_text(encoding='utf-8'))['source'], 'source.mov')

    def test_video_range_seek_including_suffix_uses_video_not_decoded_audio(self):
        job_id, _, video = self.saved_video()
        cases = [('bytes=6-15', video[6:16], f'bytes 6-15/{len(video)}'),
                 ('bytes=-4', video[-4:], f'bytes {len(video)-4}-{len(video)-1}/{len(video)}'),
                 ('bytes=100-', video[100:], f'bytes 100-{len(video)-1}/{len(video)}')]
        for value, expected, content_range in cases:
            with self.subTest(range=value):
                status, headers, body = self.request('GET', '/api/media/' + job_id, headers={'Range': value})
                self.assertEqual(status, 206)
                self.assertEqual(headers['Content-Type'], 'video/mp4')
                self.assertEqual(headers['Content-Range'], content_range)
                self.assertEqual(body, expected)
        for value in ('bytes=-0', 'bytes=106-', 'bytes=9-4', 'bytes=1-2,4-5'):
            with self.subTest(invalid_range=value):
                status, headers, body = self.request('GET', '/api/media/' + job_id, headers={'Range': value})
                self.assertEqual(status, 416)
                self.assertEqual(headers['Content-Range'], 'bytes */106')
                self.assertEqual(body, b'')

    def test_saved_video_reopens_after_restart_and_transcript_edit_keeps_gaze(self):
        job_id, folder, original_video = self.saved_video()
        raw_before = (folder / 'raw.json').read_bytes()
        restarted = ReviewApp(self.temp.name)
        self.server.RequestHandlerClass = make_handler(restarted)
        status, _, data = self.request('GET', '/api/jobs/' + job_id)
        self.assertEqual(status, 200)
        report = json.loads(data)['report']
        self.assertEqual(report['media']['kind'], 'video')
        self.assertEqual(report['gaze']['status'], 'unavailable')
        self.assertIn('pace', report)
        status, _, data = self.request('POST', '/api/reviews/' + job_id,
                                       json.dumps({'edits': {'1': '전사를 수정했습니다.'}}, ensure_ascii=False).encode())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)['gaze'], report['gaze'])
        self.assertEqual((folder / 'raw.json').read_bytes(), raw_before)
        self.assertEqual((folder / 'video.mp4').read_bytes(), original_video)
        self.assertEqual(restarted.db.list_reviews()[0]['id'], job_id)

    def test_missing_prepared_video_does_not_silently_serve_audio_as_video(self):
        job_id, folder, _ = self.saved_video()
        (folder / 'video.mp4').unlink()
        status, _, body = self.request('GET', '/api/media/' + job_id)
        self.assertEqual(status, 404)
        self.assertIn('error', json.loads(body))

    def test_video_and_audio_size_limits_are_checked_before_creating_job(self):
        with patch('src.review_server.MAX_VIDEO_UPLOAD', 20), patch('src.review_server.MAX_UPLOAD', 8):
            status, _, _ = self.request('POST', '/api/upload', b'x' * 12, {'X-Audio-Extension': '.mp4'})
            self.assertEqual(status, 201)
            before = set(self.app.storage.iterdir())
            for ext, body in (('.mp4', b'x' * 21), ('.wav', b'x' * 12), ('.exe', b'x'),
                              ('../../outside.mp4', b'x'), ('.mp4', b'')):
                with self.subTest(extension=ext):
                    status, _, _ = self.request('POST', '/api/upload', body, {'X-Audio-Extension': ext})
                    self.assertEqual(status, 400)
                    self.assertEqual(set(self.app.storage.iterdir()), before)

    def test_script_upload_endpoint_decodes_korean_and_rejects_wrong_type(self):
        status, _, body = self.request('POST', '/api/script', '발표 대본입니다.'.encode('cp949'),
                                       {'X-Script-Extension': '.txt'})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['text'], '발표 대본입니다.')
        status, _, _ = self.request('POST', '/api/script', b'video', {'X-Script-Extension': '.mp4'})
        self.assertEqual(status, 400)


class ReviewVideoMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        cls.silent = cls.root / 'silent.mp4'
        cls.delayed = cls.root / 'delayed.mp4'
        common = [cls.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y']
        subprocess.run(common + ['-f', 'lavfi', '-i', 'color=c=black:s=160x120:r=24:d=4',
                                  '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(cls.silent)],
                       check=True, capture_output=True, timeout=30)
        subprocess.run(common + ['-f', 'lavfi', '-i', 'color=c=black:s=160x120:r=24:d=4',
                                  '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000:duration=1',
                                  '-filter_complex', '[1:a]asetpts=PTS+2/TB[a]', '-map', '0:v', '-map', '[a]',
                                  '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(cls.delayed)],
                       check=True, capture_output=True, timeout=30)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_delayed_audio_retains_video_clock_and_silent_tail(self):
        with tempfile.TemporaryDirectory() as temporary:
            original_hash = hashlib.sha256(self.delayed.read_bytes()).hexdigest()
            media = prepare_media(self.delayed, temporary)
            pcm = Path(temporary) / 'decoded.wav'
            subprocess.run([self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                            '-i', media['analysis_source'], '-map', '0:a:0', '-ar', '16000',
                            '-ac', '1', '-c:a', 'pcm_s16le', str(pcm)],
                           check=True, capture_output=True, timeout=30)
            with wave.open(str(pcm), 'rb') as reader:
                samples = np.frombuffer(reader.readframes(reader.getnframes()), dtype='<i2')
            audible = np.flatnonzero(np.abs(samples.astype(np.int32)) > 1000)
            self.assertGreater(len(audible), 0)
            self.assertAlmostEqual(audible[0] / 16000, 2, delta=.15)
            self.assertGreaterEqual(len(samples) / 16000, 3.95)
            self.assertLess(abs(len(samples) / 16000 - media['duration']), .15)
            self.assertEqual(hashlib.sha256(self.delayed.read_bytes()).hexdigest(), original_hash)
            self.assertEqual(media['kind'], 'video')
            self.assertTrue(media['has_audio'])

    def test_silent_video_remains_video_and_worker_skips_speech_inference(self):
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary)
            media = prepare_media(self.silent, job)
            self.assertTrue(media['has_video'])
            self.assertFalse(media['has_audio'])
            (job / 'request.json').write_text(json.dumps({'source': 'source.mp4', 'title': '무음 영상',
                                                         'models_dir': str(job / 'missing-models')}), encoding='utf-8')
            with patch('src.review_worker.prepare_media', return_value=media), \
                    patch('src.review_worker.analyze_file') as analyze, \
                    patch('sys.argv', ['review_worker', str(job)]):
                self.assertEqual(run_worker(), 0)
            analyze.assert_not_called()
            raw = json.loads((job / 'raw.json').read_text(encoding='utf-8'))
            self.assertEqual(raw['segments'], [])
            self.assertEqual(raw['gaze']['status'], 'unavailable')
            self.assertIsNone(raw['gaze']['camera_facing_ratio'])
            self.assertTrue(raw['media_warnings'])
            with wave.open(str(job / 'audio.wav'), 'rb') as reader:
                self.assertAlmostEqual(reader.getnframes() / reader.getframerate(), raw['duration'], places=3)

    def test_corrupt_media_fails_as_a_clear_job_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary)
            (job / 'source.mp4').write_bytes(b'not video')
            (job / 'request.json').write_text(json.dumps({'source': 'source.mp4', 'title': '손상된 영상',
                                                         'models_dir': str(job / 'models')}), encoding='utf-8')
            with patch('sys.argv', ['review_worker', str(job)]):
                self.assertEqual(run_worker(), 1)
            self.assertFalse((job / 'raw.json').exists())
            error = json.loads((job / 'error.json').read_text(encoding='utf-8'))
            self.assertIn('파일', error['error'])

    def test_video_report_clock_covers_audio_packet_rounding(self):
        for audio_duration in (4.0, 4.05):
            with self.subTest(audio_duration=audio_duration), tempfile.TemporaryDirectory() as temporary:
                job = Path(temporary)
                (job / 'request.json').write_text(json.dumps({'source': 'source.mp4', 'title': '시간 검증',
                                                             'models_dir': str(job / 'models')}), encoding='utf-8')
                media = {'kind': 'video', 'has_audio': True, 'duration': 4.03,
                         'analysis_source': str(job / 'video.mp4')}
                raw_audio = {'duration': audio_duration, 'segments': []}
                with patch('src.review_worker.prepare_media', return_value=media), \
                        patch('src.review_worker.analyze_file', return_value=raw_audio), \
                        patch('src.review_gaze.analyze_gaze', return_value={'status': 'insufficient_evidence'}), \
                        patch('sys.argv', ['review_worker', str(job)]):
                    self.assertEqual(run_worker(), 0)
                raw = json.loads((job / 'raw.json').read_text(encoding='utf-8'))
                self.assertEqual(raw['duration'], max(audio_duration, 4.03))
                self.assertEqual(raw['audio_duration'], audio_duration)

    def test_probe_rejects_overlong_or_unreadable_duration(self):
        for output in (b'Duration: 00:20:01.00\nStream #0:0: Video: h264',
                       b'Duration: N/A\nStream #0:0: Video: h264'):
            with self.subTest(output=output), \
                    patch('src.review_media.subprocess.run', return_value=subprocess.CompletedProcess([], 1, stderr=output)), \
                    self.assertRaises(ValueError):
                probe_media('ignored.mp4')


if __name__ == '__main__':
    unittest.main()
