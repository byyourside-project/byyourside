'use strict';
const $ = id => document.getElementById(id);
let selected = null, report = null, loadingVersion = 0, busy = false;
let recorder = null, stream = null, recordingTimer = null;
const labels = {explained: '설명 확인', uncertain: '판단 보류', unconfirmed: '미확인'};
const time = seconds => `${Math.floor(Math.round(seconds) / 60)}분 ${Math.round(seconds) % 60}초`;
const stamp = value => new Date(value).toLocaleString('ko-KR');
function node(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined) result.textContent = text;
  if (className) result.className = className;
  return result;
}
function error(message = '') { $('error').textContent = message; $('error').hidden = !message; }
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '요청에 실패했습니다.');
  return result;
}
function lock(value) {
  busy = value;
  for (const id of ['ask', 'save-supplement', 'refresh', 'export']) $(id).disabled = value;
  $('record').disabled = value && !recorder;
  document.querySelectorAll('.session').forEach(button => { button.disabled = value; });
}
function showAnswer(answer, compact = false) {
  const box = node('article', undefined, compact ? 'question-history' : 'answer');
  box.append(node('strong', answer.question), node('p', answer.message));
  for (const source of answer.sources) {
    const quote = node('div', undefined, 'source');
    quote.append(node('small', `${source.source_id} · ${source.title}`), node('div', source.text));
    box.append(quote);
  }
  box.append(node('p', answer.limitation, 'note'));
  return box;
}
function render(data) {
  report = data; $('detail').hidden = false; $('empty').hidden = true;
  $('title').textContent = data.title;
  $('saved-at').textContent = `${stamp(data.saved_at)} 저장 · ${data.status === 'ended' ? '종료된 발표' : '종료 전 저장본'}`;
  const metrics = [ ['실제 소요', time(data.actual_sec), `목표 ${time(data.target_sec)}`],
    ['계획과 차이', `${data.difference_sec > 0 ? '+' : ''}${Math.round(data.difference_sec)}초`, '전체 소요 시간 기준'],
    ['설명 확인', `${data.counts.explained}개`, `보류 ${data.counts.uncertain} · 미확인 ${data.counts.unconfirmed}`] ];
  $('metrics').replaceChildren(...metrics.map(([label, value, note]) => {
    const box = node('div', undefined, 'metric'); box.append(node('span', label), node('strong', value), node('small', note)); return box;
  }));
  $('suggestions').replaceChildren(...data.suggestions.map(item => node('li', item)));
  $('quality').replaceChildren(...[...data.issues, ...data.limitations].map(item => node('li', item)));
  $('supplement').value = data.qa.supplement;
  $('questions').replaceChildren(...data.qa.questions.slice().reverse().map(item => showAnswer(item, true)));
  if (!data.qa.questions.length) $('questions').append(node('p', '아직 저장된 질문이 없습니다.', 'muted'));
  $('slides').replaceChildren(...data.slides.map(slide => {
    const box = node('article', undefined, 'slide');
    box.append(node('h3', slide.title), node('p', `${slide.visited ? `실제 ${time(slide.actual_sec)} · 방문 ${slide.visits.length}회` : '방문 기록 없음'} / 목표 ${time(slide.target_sec)}`, 'muted'));
    for (const point of slide.points) {
      const item = node('div', undefined, 'point');
      item.append(node('span', labels[point.status], `badge ${point.status}`), node('span', point.text));
      if (point.reason) item.append(node('p', point.reason, 'note'));
      for (const ev of point.evidence) item.append(node('p', `${ev.start_sec.toFixed(1)}–${ev.end_sec.toFixed(1)}초 · ${ev.text}`, 'evidence'));
      box.append(item);
    }
    return box;
  }));
  $('transcript').replaceChildren(...data.transcript.map(segment => {
    const row = node('div', undefined, 'transcript-row');
    row.append(node('small', `${segment.start_sec.toFixed(1)}–${segment.end_sec.toFixed(1)}초${segment.status === 'OK' ? '' : ' · 인식 확인 필요'}`), node('span', segment.text)); return row;
  }));
  if (!data.transcript.length) $('transcript').append(node('p', '발화 기록이 없습니다.', 'muted'));
}
async function select(id) {
  if (busy) return;
  const version = ++loadingVersion;
  lock(true); error();
  try {
    const data = await api(`/api/after/report?session_id=${encodeURIComponent(id)}`);
    if (version !== loadingVersion) return;
    selected = id; render(data); $('answer').replaceChildren(); $('question').value = '';
    $('qa-status').textContent = ''; $('supplement-status').textContent = '';
    document.querySelectorAll('.session').forEach(button => {
      button.classList.toggle('selected', button.dataset.id === id);
      button.setAttribute('aria-pressed', String(button.dataset.id === id));
    });
  } catch (exc) { error(exc.message); } finally { if (version === loadingVersion) lock(false); }
}
async function refresh() {
  if (busy) return;
  lock(true); error();
  let next;
  try {
    const data = await api('/api/after/sessions');
    $('history-note').textContent = `${data.sessions.length}개의 발표${data.unreadable_count ? ` · 읽을 수 없는 기록 ${data.unreadable_count}개` : ''}`;
    $('sessions').replaceChildren(...data.sessions.map(item => {
      const button = node('button', undefined, 'session'); button.type = 'button'; button.dataset.id = item.session_id;
      button.append(node('strong', item.title), node('small', stamp(item.saved_at)), node('small', `${time(item.actual_sec)} · ${item.status === 'ended' ? '종료' : '종료 전 저장본'}`));
      button.addEventListener('click', () => select(item.session_id)); return button;
    }));
    next = data.sessions.some(item => item.session_id === selected) ? selected : data.sessions[0]?.session_id;
    if (!next) { selected = null; report = null; $('detail').hidden = true; $('empty').hidden = false; }
  } catch (exc) { error(exc.message); } finally { lock(false); }
  if (next) await select(next);
}
$('refresh').addEventListener('click', refresh);
$('save-supplement').addEventListener('click', async () => {
  if (!selected || busy) return;
  lock(true); error();
  try { await api('/api/after/supplement', {session_id: selected, text: $('supplement').value}); $('supplement-status').textContent = '저장했습니다. 다음 질문부터 적용됩니다.'; }
  catch (exc) { error(exc.message); } finally { lock(false); }
});
$('question-form').addEventListener('submit', async event => {
  event.preventDefault(); if (!selected || busy) return;
  lock(true); error(); $('qa-status').textContent = '발표 자료를 확인하고 있습니다.';
  try {
    // Preserve edits even when the separate save button was not pressed.
    await api('/api/after/supplement', {session_id: selected, text: $('supplement').value});
    const answer = await api('/api/after/ask', {session_id: selected, question: $('question').value});
    $('answer').replaceChildren(showAnswer(answer));
    const data = await api(`/api/after/report?session_id=${encodeURIComponent(selected)}`);
    $('questions').replaceChildren(...data.qa.questions.slice().reverse().map(item => showAnswer(item, true)));
    $('qa-status').textContent = '질문과 검색 근거를 저장했습니다.';
  } catch (exc) { error(exc.message); $('qa-status').textContent = '요청을 완료하지 못했습니다.'; } finally { lock(false); }
});
$('export').addEventListener('click', async () => {
  if (!selected || busy) return;
  lock(true); error();
  try {
    const result = await api(`/api/after/export?session_id=${encodeURIComponent(selected)}`);
    const url = URL.createObjectURL(new Blob(['\ufeff' + result.markdown], {type: 'text/markdown;charset=utf-8'}));
    const link = node('a'); link.href = url; link.download = `발표리뷰_${selected}.md`;
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (exc) { error(exc.message); } finally { lock(false); }
});
function releaseMic() {
  if (recordingTimer) clearTimeout(recordingTimer); recordingTimer = null;
  stream?.getTracks().forEach(track => track.stop()); stream = null;
}
$('record').addEventListener('click', async () => {
  if (recorder) { if (recorder.state === 'recording') recorder.stop(); return; }
  if (busy) return;
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { error('이 브라우저는 녹음을 지원하지 않습니다. 질문을 직접 입력해 주세요.'); return; }
  lock(true); error(); $('audio-status').textContent = '마이크 연결 중…';
  try {
    const live = await api('/api/state');
    if (live.session && ['running', 'stopping'].includes(live.session.status)) throw new Error('발표 종료 처리가 끝난 뒤 질문을 녹음해 주세요.');
    stream = await navigator.mediaDevices.getUserMedia({audio: true});
    const chunks = []; let bytes = 0, tooLarge = false, failed = false;
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = event => {
      bytes += event.data.size;
      if (bytes > 12 * 1024 * 1024) { tooLarge = true; if (recorder?.state === 'recording') recorder.stop(); }
      else chunks.push(event.data);
    };
    recorder.onerror = () => { failed = true; releaseMic(); error('녹음 중 오류가 발생했습니다. 질문을 직접 입력해 주세요.'); };
    recorder.onstop = async () => {
      const type = recorder.mimeType; recorder = null; releaseMic(); $('record').textContent = '질문 녹음'; $('record').disabled = true;
      $('audio-status').textContent = '기기에서 음성을 인식하는 중…';
      try {
        if (failed || tooLarge) throw new Error('녹음이 중단되었거나 12MB를 초과했습니다. 더 짧게 녹음해 주세요.');
        const response = await fetch('/api/after/transcribe', {method: 'POST', headers: {'Content-Type': type || 'application/octet-stream'}, body: new Blob(chunks, {type})});
        const result = await response.json(); if (!response.ok) throw new Error(result.error);
        $('question').value = result.question; $('question').focus(); $('audio-status').textContent = '전사를 확인하고 “관련 자료 찾기”를 눌러 주세요.';
      } catch (exc) { error(exc.message); $('audio-status').textContent = '인식 실패 · 다시 녹음하거나 직접 입력하세요.'; } finally { lock(false); }
    };
    recorder.start(1000); $('record').disabled = false; $('record').textContent = '녹음 마치기';
    $('audio-status').textContent = '녹음 중 · 60초 이내 자동 종료';
    // Small margin prevents container timestamps from exceeding the server's 60 s limit.
    recordingTimer = setTimeout(() => { if (recorder?.state === 'recording') recorder.stop(); }, 59000);
  } catch (exc) { recorder = null; releaseMic(); lock(false); error(`마이크를 열지 못했습니다: ${exc.message}`); $('audio-status').textContent = '질문을 직접 입력할 수 있습니다.'; }
});
window.addEventListener('pagehide', () => { if (recorder) { recorder.onstop = null; if (recorder.state === 'recording') recorder.stop(); recorder = null; } releaseMic(); });
api('/api/state').then(state => { if (state.review_url) { $('review-link').href = state.review_url; $('review-link').hidden = false; } }).catch(() => {});
refresh();
