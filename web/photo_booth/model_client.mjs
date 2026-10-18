// Connect final STT transcripts to the model; keep camera/state logic in the app.
export function createPhotoModelClient({url = 'http://127.0.0.1:8771', fetchImpl = globalThis.fetch} = {}) {
  let latest = 0;
  const actions = new Set(['select_layout', 'set_frame', 'set_timer', 'capture_photo',
    'no_action', 'set_shooting_view', 'adjust_view']);

  return async function applyTranscript(text, handlers, {confirmCapture} = {}) {
    const request = ++latest;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    let result;
    try {
      const response = await fetchImpl(`${url}/api/photo/interpret`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text}), signal: controller.signal,
      });
      if (!response.ok) throw new Error(`명령 모델 응답 오류 (${response.status})`);
      result = await response.json();
    } finally {
      clearTimeout(timer);
    }
    if (request !== latest) throw new Error('더 최근의 명령이 있어 이전 결과를 적용하지 않았습니다.');
    if (!actions.has(result.action) || !result.args || typeof result.args !== 'object' || Array.isArray(result.args)) {
      throw new Error('허용되지 않은 명령 응답입니다.');
    }
    if (result.action === 'no_action') {
      if (typeof handlers.no_action === 'function') await handlers.no_action(result.args);
      return {...result, executed: false};
    }
    // The measured model can confuse shooting-view requests with shutter requests.
    if (result.action === 'capture_photo') {
      if (typeof confirmCapture !== 'function' || !await confirmCapture(result)) {
        return {...result, executed: false, confirmation_required: true};
      }
      if (request !== latest) throw new Error('확인 중 새 명령이 들어와 촬영하지 않았습니다.');
    }
    const handler = handlers[result.action];
    if (typeof handler !== 'function') throw new Error('앱에 연결되지 않은 기능입니다.');
    // Existing handlers must still enforce countdown/busy/layout/zoom bounds.
    await handler(result.args);
    return {...result, executed: true};
  };
}
