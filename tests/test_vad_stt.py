import unittest
import numpy as np
import scipy.io.wavfile as wavfile
from src.config import VadConfig, SttConfig, PipelineConfig
from src.vad import VadProcessor
from src.stt import SttEngine
from src.pipeline import SpeechPipeline

class TestVadAndStt(unittest.TestCase):
    def setUp(self):
        self.vad_config = VadConfig()
        self.stt_config = SttConfig()
        self.sample_wav = "models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/test_wavs/ko.wav"

    def test_stt_transcription(self):
        stt = SttEngine(self.stt_config)
        sr, raw = wavfile.read(self.sample_wav)
        samples = (raw.astype(np.float32) / 32768.0)
        text, infer_ms = stt.transcribe(samples, sr)
        self.assertIn("생각", text)
        self.assertGreater(infer_ms, 0)
        self.assertLess(stt.cumulative_rtf, 0.5)

    def test_vad_processing_and_flush(self):
        vad = VadProcessor(self.vad_config)
        sr, raw = wavfile.read(self.sample_wav)
        samples = (raw.astype(np.float32) / 32768.0)
        window = 512
        segments = []
        for i in range(0, len(samples), window):
            chunk = samples[i:i + window]
            segments.extend(vad.process_chunk(chunk))
        segments.extend(vad.flush())
        self.assertGreaterEqual(len(segments), 1)
        self.assertGreater(segments[0].duration_ms, 1000)

    def test_pipeline_direct_wav(self):
        pipeline = SpeechPipeline()
        result = pipeline.run_wav_direct(self.sample_wav)
        self.assertIn(result.mode, ["wav_vad", "wav_direct", "wav_direct_stt"])
        self.assertGreaterEqual(result.segment_count, 1)
        self.assertLess(result.throughput_rtf, 0.5)
        self.assertEqual(result.overrun_count, 0)
        self.assertEqual(result.dropped_audio_chunks, 0)

    def test_pipeline_replay(self):
        pipeline = SpeechPipeline()
        result = pipeline.run_replay(self.sample_wav, speed=2.0)
        self.assertEqual(result.mode, "replay")
        self.assertGreaterEqual(result.segment_count, 1)
        self.assertLess(result.cumulative_rtf, 0.5)
        self.assertEqual(result.overrun_count, 0)
        self.assertEqual(result.dropped_audio_chunks, 0)

if __name__ == "__main__":
    unittest.main()
