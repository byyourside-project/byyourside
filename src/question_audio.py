"""Short question transcription. Temporary recordings are removed after each request."""
import tempfile
import threading
from pathlib import Path


class QuestionAudio:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.Lock()

    def transcribe(self, payload):
        if not 0 < len(payload) <= 12 * 1024 * 1024:
            raise ValueError('질문 음성은 12MB 이하로 녹음해 주세요.')
        if not self.lock.acquire(blocking=False):
            raise ValueError('앞선 질문 음성을 처리하고 있습니다. 잠시 후 다시 시도해 주세요.')
        try:
            from src.review_audio import analyze_file
            directory = self.root / 'logs' / 'question_audio'
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=directory) as temporary:
                folder = Path(temporary)
                source = folder / 'question.media'
                source.write_bytes(payload)
                result = analyze_file(source, folder / 'decoded.wav', self.root / 'models', max_duration=60)
            question = ' '.join(segment['text'].strip() for segment in result['segments'] if segment['text'].strip())
            if not question:
                raise ValueError('말소리를 인식하지 못했습니다. 다시 녹음하거나 질문을 입력해 주세요.')
            if len(question) > 1000:
                raise ValueError('인식된 질문이 너무 깁니다. 질문을 짧게 나눠 주세요.')
            return {'question': question, 'duration_sec': result['duration'], 'engine': result['engine']}
        finally:
            self.lock.release()
