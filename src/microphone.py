"""Physical input selection and bounded Core Audio recovery.

PortAudio is refreshed only while this process owns no input stream. Recording
and input tests share the same lock; neither silently selects another device.
"""
import math
import threading
import time
from contextlib import contextmanager

import numpy as np
import sounddevice as sd


_lock = threading.Lock()
_cached_devices = []


class MicrophoneError(RuntimeError):
    pass


def _refresh():
    # sounddevice has no public refresh API. These paired lifecycle wrappers
    # reset stale Core Audio handles (e.g. after sleep/device changes). Calling
    # them with an active stream is unsafe, so all our input paths hold _lock.
    sd._terminate()
    sd._initialize()


def _devices():
    global _cached_devices
    default = sd.default.device[0]
    _cached_devices = [{"id": i, "name": d["name"],
                        "input_channels": d["max_input_channels"],
                        "default_sample_rate": int(round(d["default_samplerate"])),
                        "is_default": i == default}
                       for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
    return [dict(d) for d in _cached_devices]


def input_devices():
    # The picker remains responsive during recording; do not reset PortAudio.
    if not _lock.acquire(blocking=False):
        devices = [dict(d) for d in _cached_devices]
    else:
        try:
            _refresh()
            devices = _devices()
        except Exception as exc:
            return {"devices": [], "default_device_id": None, "error": str(exc)}
        finally:
            _lock.release()
    return {"devices": devices,
            "default_device_id": next((d["id"] for d in devices if d["is_default"]), None)}


def validate_device_id(device_id):
    if device_id is not None and (isinstance(device_id, bool) or not isinstance(device_id, int) or device_id < 0):
        raise ValueError("마이크 장치 번호는 0 이상의 정수 또는 null이어야 합니다.")


def _select(device_id):
    validate_device_id(device_id)
    devices = _devices()
    match = next((d for d in devices if d["id"] == device_id), None) if device_id is not None else next((d for d in devices if d["is_default"]), None)
    if match is None:
        raise MicrophoneError("선택한 마이크 입력 장치를 찾지 못했습니다. 장치 목록을 새로고침하고 마이크를 선택해 주세요.")
    return match


def native_blocksize(sample_rate, target_rate=16000, window_size=512):
    # An exact multiple of a VAD window avoids padding/drift when 44.1 kHz
    # input is converted to 16 kHz separately for each callback.
    divisor = math.gcd(sample_rate, target_rate)
    target_samples = math.lcm(window_size, target_rate // divisor)
    return target_samples * sample_rate // target_rate


def resolve_input(device_id=None):
    validate_device_id(device_id)
    if not _lock.acquire(blocking=False):
        raise MicrophoneError("다른 마이크 입력이 사용 중입니다. 입력 확인이나 녹음을 종료한 뒤 다시 연결해 주세요.")
    try:
        _refresh()
        device = _select(device_id)
        sd.check_input_settings(device=device["id"], channels=1, dtype="float32", samplerate=device["default_sample_rate"])
        return {**device, "sample_rate": device["default_sample_rate"],
                "blocksize": native_blocksize(device["default_sample_rate"])}
    except MicrophoneError:
        raise
    except Exception as exc:
        raise MicrophoneError(explain_error(exc)) from exc
    finally:
        _lock.release()


def explain_error(exc):
    memory_hint = "큰 로컬 모델의 메모리 부하 때문에 맥 오디오 버퍼를 열지 못할 수도 있습니다. 모델을 줄인 뒤 다시 연결해 주세요. " if -9986 in getattr(exc, "args", ()) else ""
    return (f"마이크 입력을 열지 못했습니다: {exc}. " + memory_hint +
            "장치를 다시 선택해 연결해 주세요. 계속 실패하면 macOS 시스템 설정 → 개인정보 보호 및 보안 → 마이크에서 "
            "서버를 실행한 앱(Codex 또는 터미널)의 접근을 확인해 주세요.")


@contextmanager
def open_input_stream(*, device, samplerate, channels, dtype, blocksize, callback, expected_name=None):
    if not _lock.acquire(blocking=False):
        raise MicrophoneError("다른 마이크 입력이 사용 중입니다.")
    stream = None
    try:
        # Before any callback, reinitialize cached native handles and resolve
        # explicitly. A selected ID disappearing is an error, not a fallback.
        _refresh()
        selected = _select(device)
        if expected_name is not None and selected["name"] != expected_name:
            raise MicrophoneError("선택한 마이크 장치가 바뀌었습니다. 장치 목록을 새로고침하고 다시 선택해 주세요.")
        sd.check_input_settings(device=selected["id"], channels=channels, dtype=dtype, samplerate=samplerate)
        for attempt in range(2):
            try:
                stream = sd.InputStream(device=selected["id"], samplerate=samplerate,
                                        channels=channels, dtype=dtype,
                                        blocksize=blocksize, callback=callback)
                stream.start()
                break
            except sd.PortAudioError as exc:
                if stream is not None:
                    stream.close()
                    stream = None
                # Retry only native stream startup failure, never a recording
                # failure. Preserve the selected device across the refresh.
                if attempt or not any(code in exc.args for code in (-9986, -9999, -9985)):
                    raise MicrophoneError(explain_error(exc)) from exc
                _refresh()
                refreshed = _select(selected["id"])
                if refreshed["name"] != selected["name"]:
                    raise MicrophoneError("마이크 장치가 바뀌었습니다. 장치 목록을 새로고침하고 다시 선택해 주세요.")
        yield stream
    finally:
        try:
            if stream is not None:
                stream.close()
        finally:
            _lock.release()


def test_input(device_id=None, duration=.7):
    device = resolve_input(device_id)
    callbacks = [0]
    maximum = [0.0]
    def callback(indata, frames, info, status):
        callbacks[0] += 1
        maximum[0] = max(maximum[0], float(np.max(np.abs(indata))))
    with open_input_stream(device=device["id"], samplerate=device["sample_rate"], channels=1,
                           dtype="float32", blocksize=device["blocksize"], callback=callback,
                           expected_name=device["name"]):
        time.sleep(duration)
    if not callbacks[0]:
        raise MicrophoneError("마이크가 열렸지만 입력 프레임을 받지 못했습니다. macOS 입력 장치와 접근 권한을 확인해 주세요.")
    return {"status": "ok", "device": device, "callbacks": callbacks[0],
            "peak_dbfs": round(20 * math.log10(max(maximum[0], 1e-6)), 1)}
