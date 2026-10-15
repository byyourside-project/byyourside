'use strict';
const $ = id => document.getElementById(id);
let current = null, busy = false, lastAlerts = '', commandRevision = 0, pendingCommand = null;
let microphonesRefreshing = false, microphoneDevicesError = '', selectedMicrophone = '', microphoneSelectionChanged = false;
let missingMicrophoneId = null, knownMicrophoneIds = null;
let microphoneSelectionInitialized = false, microphoneSelectionTouched = false;
const microphoneNames = new Map();
const statuses = {unconfirmed:'미확인', explained:'설명됨', uncertain:'판단불가'};
const seconds = n => `${n < 0 ? '+' : ''}${String(Math.floor(Math.abs(n) / 60)).padStart(2,'0')}:${String(Math.floor(Math.abs(n) % 60)).padStart(2,'0')}`;
function node(tag, text, cls) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; }
function error(message) { $('error').textContent = message; $('error').hidden = !message; }
async function api(action, data) {
  const requestTimeout = data === undefined ? 8000 : action === 'start' ? 75000 : 30000;
  const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), requestTimeout);
  try {
    const response = await fetch('/api/' + action, {signal:controller.signal, ...(data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '요청을 처리할 수 없습니다.');
    return result;
  } catch (e) {
    if (e.name === 'AbortError') throw new Error('서버 응답이 지연됩니다. 잠시 뒤 다시 시도해 주세요.');
    throw e;
  } finally { clearTimeout(timeout); }
}
async function command(action, data) {
  if (busy) return false;
  const captureRequested = action === 'microphone_test' || action === 'microphone_retry' || (action === 'start' && data?.microphone === true);
  if (captureRequested && selectedMicrophone !== '' && selectedMicrophone === missingMicrophoneId) {
    error('선택한 마이크가 연결 해제되었습니다. 시스템 기본 마이크나 연결된 다른 장치를 직접 선택해 주세요.');
    return false;
  }
  busy = true;
  pendingCommand = action;
  commandRevision++;
  error('');
  renderMicrophoneState();
  renderVoiceState(false);
  renderPreparationState();
  try { render(await api(action, data)); if (action === 'start' && current?.session?.status === 'running') $('live-feedback').scrollIntoView?.({behavior:'smooth',block:'start'}); return true; } catch (e) { error(e.message); return false; }
  finally { commandRevision++; busy = false; pendingCommand = null; if (action === 'start' && current) render(current); else {renderMicrophoneState(); renderVoiceState(false); renderPreparationState();} }
}
function isStartPreparing() { return current?.start_preparing === true || pendingCommand === 'start'; }
function renderPreparationState() {
  const data = current || {}, preparing = isStartPreparing(), active = !!data.session && data.session.status !== 'ended';
  for (const id of ['script-text','script-duration','script-slide-durations','prepare-script','input-mode','upload','deck-file']) $(id).disabled = preparing || active;
  const failed = data.coach_status?.status === 'error' && !preparing;
  const failureMessage = typeof data.coach_status?.message === 'string' && data.coach_status.message.trim() ? data.coach_status.message.trim() : '준비를 완료하지 못했습니다.';
  const failureLabel = failureMessage.startsWith('모델 준비 실패') ? failureMessage : `모델 준비 실패: ${failureMessage}`;
  $('coach-preparation').hidden = !preparing && !failed;
  $('coach-preparation').classList.toggle('failed', failed);
  $('coach-preparation').textContent = preparing ? '모델 준비 중 · 아직 발표를 시작하지 마세요. 준비가 끝난 뒤 발표 타이머와 마이크가 시작됩니다.' : failed ? `${failureLabel} 다시 발표 시작을 눌러 재시도할 수 있습니다.` : '';
  renderLiveFeedback(current);
}
function renderLiveFeedback(data, disconnected = false) {
  if (!data) return;
  const s = data.session, script = s?.script_progress, tracking = script?.tracking, timing = script?.slide_timing;
  const index = tracking?.slide_index ?? s?.index ?? 0;
  const slide = (data.deck.script_plan?.slides || data.deck.slides)[index];
  const preparing = isStartPreparing(), ended = s?.status === 'ended', active = !!s && !ended && !preparing;
  const pending = ['evaluating','waiting_for_silence'].includes(s?.judgment_status) || s?.status === 'stopping';
  const spent = preparing ? 0 : timing?.elapsed_sec ?? s?.slide_elapsed_sec ?? 0;
  const target = slide.target_duration_sec, remaining = target - spent;
  $('feedback-clock-label').textContent = disconnected ? '시간 확인 중단' : remaining < 0 ? '이 슬라이드 초과 시간' : '이 슬라이드 남은 시간';
  $('feedback-remaining').textContent = disconnected ? '—' : seconds(Math.abs(remaining));
  $('feedback-remaining').className = remaining < 0 ? 'over' : '';
  $('feedback-budget').textContent = `${index+1}번 슬라이드 · 경과 ${seconds(spent)} / 목표 ${seconds(target)}`;
  $('feedback-position').textContent = tracking && !preparing ? `현재 문장 ${tracking.index+1}/${script.units.length} · ${tracking.phase === 'complete' ? '발화 완료' : tracking.phase === 'partial' ? '말하는 중' : '위치 확인 중'}` : '대본 위치 확인 대기';

  let pace = 'waiting', title = '첫 문장을 기다려요', ratio = null, delta = null;
  let update = !s || preparing ? '발표 준비' : pending ? '최신 발화 확인 중' : '위치 확인 중';
  let detail = '현재 문장을 확인하면 계획과 비교해 드립니다.';
  let advice = !s ? '평소처럼 발표를 시작해 주세요.' : '첫 문장을 확인하고 있어요. 자연스럽게 이어가세요.';
  let basis = '대본 위치로 추정한 진행 속도입니다. 실제 발음 속도나 내용 정확도와는 다릅니다.';
  const audioProblem = active && data.audio_input_mode === 'mic' && ['error','stopped','recovering','loading'].includes(data.audio_status);
  const age = tracking && Number.isFinite(tracking.audio_end_sec) && tracking.audio_end_sec >= 0 ? Math.max(0, s.elapsed_sec - tracking.audio_end_sec) : null;
  // Use the last measured speech position while inference runs. Never treat
  // the polling clock as new speech evidence or carry pace across a resync.
  const usable = timing?.reliable === true && Number.isFinite(timing.ratio) && timing.ratio > 0 &&
    Number.isFinite(timing.measured_elapsed_sec) && Number.isFinite(timing.planned_elapsed_sec) &&
    age !== null && age <= 15 && !tracking.misses && ['partial','complete'].includes(tracking.phase);
  const final = ended ? timing?.records?.at(-1) : null;
  if (disconnected) {
    title = '서버 연결을 확인해 주세요'; update = '연결 끊김';
    detail = '속도와 시간 갱신이 멈췄습니다.'; advice = '연결이 복구되면 다시 표시합니다.';
  } else if (preparing) {
    title = '발표를 준비하고 있어요'; update = '시작 대기';
    detail = '준비가 끝나면 타이머와 음성 인식이 시작됩니다.'; advice = '발표 시작 안내를 기다려 주세요.';
  } else if (audioProblem || s?.issues?.length) {
    title = audioProblem ? '음성 입력 확인이 필요해요' : '속도 판단을 잠시 보류해요'; update = ended ? '발표 종료' : '입력 확인 필요';
    detail = '입력이 불확실해 빠름·느림을 표시하지 않습니다.'; advice = '마이크 입력과 기록 상태를 확인해 주세요.';
  } else if (ended && final?.end_source === 'session_end' && final.reliable && final.actual_duration_sec > 0) {
    ratio = final.target_duration_sec / final.actual_duration_sec;
    delta = final.actual_duration_sec - final.target_duration_sec;
    basis = '마지막 슬라이드의 체류 시간 기준입니다. 내용 확인 결과는 별도로 확인해 주세요.';
  } else if (usable && !ended) {
    ratio = timing.ratio;
    delta = timing.measured_elapsed_sec - timing.planned_elapsed_sec;
    basis = `${age.toFixed(1)}초 전 발화 기준 · 대본 위치로 추정 · 인식·판정 대기 시간은 속도 계산에서 제외`;
  } else if (ended) {
    title = '발표를 마쳤어요'; update = '발표 종료';
    detail = '속도를 확정할 수 있는 근거가 부족합니다.'; advice = '아래에서 문장별 확인 결과를 살펴보세요.';
  } else if (tracking?.misses || tracking?.phase === 'manual' || (age !== null && !usable)) {
    title = '대본 위치를 확인하고 있어요'; update = pending ? '최신 발화 확인 중' : '속도 판단 보류';
    detail = age > 15 ? '최근 발화가 확인되면 속도를 다시 표시합니다.' : '현재 위치가 불확실해 속도를 잠시 보류합니다.';
    advice = '다음 문장을 이어가거나 대본에서 ‘여기서 이어가기’를 눌러 주세요.';
  }
  if (ratio !== null) {
    pace = ratio > 1.25 + 1e-9 ? 'fast' : ratio < .75 - 1e-9 ? 'slow' : 'on_plan';
    title = {fast:'계획보다 빨라요',slow:'계획보다 느려요',on_plan:'계획에 맞게 진행 중'}[pace];
    const difference = Math.round(Math.abs(delta));
    detail = difference < 1 ? '이 슬라이드 계획과 거의 같아요.' : `이 슬라이드 계획보다 약 ${difference}초 ${delta < 0 ? '앞서고 있어요' : '뒤처지고 있어요'}.`;
    advice = {fast:'다음 설명은 조금 여유 있게 이어가세요.',slow:'남은 핵심 내용을 중심으로 조금 속도를 높여 보세요.',on_plan:'지금 흐름을 유지하세요.'}[pace];
    update = ended ? '발표 종료 · 마지막 슬라이드' : pending ? '최신 발화 확인 중 · 직전 속도' : '마지막 확인 속도';
    if (ended) advice = '아래에서 문장별 확인 결과와 슬라이드 기록을 살펴보세요.';
  }
  $('live-feedback').className = `card live-feedback ${pace}${active ? ' is-active' : ''}`;
  if ($('feedback-state').textContent !== title) $('feedback-state').textContent = title;
  $('feedback-ratio').textContent = ratio !== null ? `${Math.round(ratio*100)}%` : '—';
  $('feedback-update').textContent = update;
  $('feedback-delta').textContent = detail;
  $('feedback-advice').textContent = advice;
  $('feedback-basis').textContent = basis;
  $('feedback-marker').hidden = ratio === null;
  $('feedback-marker').style.left = ratio !== null ? `${Math.max(2,Math.min(98,(ratio-.5)*100))}%` : '50%';
  $('feedback-gauge').setAttribute('aria-label', ratio !== null ? `${title}. 계획 대비 진행 속도 ${Math.round(ratio*100)}%. ${detail}` : title);
}
function renderVoiceState(syncSelection = true) {
  const data = current || {}, scope = data.voice_scope === 'all' ? 'all' : 'pace';
  if (syncSelection && !(busy && pendingCommand === 'start')) {$('voice-enabled').checked = !!data.voice_enabled; $('voice-scope').value = scope;}
  $('voice-enabled').disabled = !data.voice_available || busy || isStartPreparing();
  $('voice-scope').disabled = !data.voice_available || busy || isStartPreparing();
  $('voice-test').disabled = data.session?.status !== 'running' || !data.voice_enabled || !data.voice_available || busy || isStartPreparing();
  const scopeLabel = scope === 'pace' ? '빠름·느림만' : '전체 안내';
  $('voice-status').textContent = !data.voice_available ? '이 환경의 로컬 음성 출력을 사용할 수 없습니다.' : data.voice_enabled ? `음성 안내 켜짐 · ${scopeLabel} · 이어폰 출력을 확인하세요.` : `음성 안내 꺼짐 · 선택 범위: ${scopeLabel}`;
  const voiceEventLabels = {voice_queued:'안내 대기',voice_started:'안내 재생 중',voice_completed:'안내 재생 완료',voice_cancelled:'지난 안내 취소',voice_failed:'음성 출력 실패'};
  if (data.voice_last_event) {
    const event = data.voice_last_event;
    $('voice-status').textContent += ' · ' + (voiceEventLabels[event.type] || event.type);
    if (['voice_queued','voice_started','voice_completed'].includes(event.type) && typeof event.message === 'string' && event.message.trim()) $('voice-status').textContent += ' · ' + event.message;
    if (event.type === 'voice_failed' && typeof event.reason === 'string' && event.reason.trim()) $('voice-status').textContent += ' · ' + event.reason;
  }
}
function renderScriptPace(script, deck) {
  $('script-slide-timing').hidden = !script.tracking;
  $('legacy-pace-metrics').hidden = !!script.tracking;
  if (script.tracking) {renderScriptTracking(script); return;}
  const display = script.display_pace && typeof script.display_pace === 'object' && !Array.isArray(script.display_pace) ? script.display_pace : null;
  const source = display || script, reliable = source.reliable === true;
  const effectivePace = reliable ? source.pace : 'waiting';
  const firstWaiting = display && (source.reason_code ? source.reason_code === 'no_confirmed_sentence' : !source.latest_keypoint_id && !/오류|손실|불확실|누락|보류/.test(source.reason || ''));
  const pace = display ? ({fast:'빠름',slow:'느림',on_plan:'적당함'}[effectivePace] || (firstWaiting ? '첫 문장 확인 대기' : '판단 보류')) : ({waiting:'판단 보류',fast:'계획보다 빠름',slow:'계획보다 느림',on_plan:'계획 내'}[effectivePace] || '판단 보류');
  const measured = Number.isFinite(source.measured_elapsed_sec) ? source.measured_elapsed_sec : null;
  const displayMeasurements = reliable && measured !== null && measured > 0;
  const latest = display ? script.units.find(unit => unit.keypoint_id === source.latest_keypoint_id) : null;
  const latestIndex = latest ? script.units.indexOf(latest) : -1;
  const duration = value => Number.isFinite(value) && value >= 0 ? `${value.toFixed(1)}초` : '—';
  const sentenceLabels = {fast:'빠름',on_plan:'적당함',slow:'느림'};
  const validSentence = unit => unit?.status === 'explained' && Number.isFinite(unit.actual_duration_sec) && unit.actual_duration_sec >= 0 && Number.isFinite(unit.sentence_ratio) && unit.sentence_ratio > 0 && !!sentenceLabels[unit.sentence_pace];
  $('script-pace-state').textContent = pace;
  $('script-pace-state').className = 'pace-status ' + (['fast','slow','on_plan'].includes(effectivePace) ? effectivePace : 'waiting');
  $('script-progress').textContent = `현재 확인 위치 ${script.position_index+1}/${script.units.length} · 설명 확인 ${Math.round(script.fraction*100)}% · 건너뛴 구간 확인 ${script.missing_ids.length}개` + (script.next_text ? ` · 다음 예정: ${script.next_text}` : ' · 마지막 구간까지 확인');
  $('pace-target').textContent = seconds(Number.isFinite(script.plan_total_duration_sec) ? script.plan_total_duration_sec : deck.total_duration_sec);
  $('pace-status-note').hidden = !display || (!source.pending && !source.final);
  $('pace-status-note').textContent = source.final ? '발표 종료 · 최종 확인 결과입니다.' : source.pending ? (reliable ? '다음 발화 확인 중 · 마지막 확인의 수치를 표시합니다.' : '다음 발화 확인 중 · 신뢰할 수 있는 속도 근거를 기다립니다.') : '';
  $('pace-measured-label').textContent = display ? '지연 제외 누적 시간' : '확인된 발화 종료';
  $('pace-measured').textContent = display ? (displayMeasurements ? duration(measured) : '판단 보류') : measured !== null && measured > 0 ? seconds(measured) : '확인 대기';
  $('pace-planned-metric').hidden = !display;
  $('pace-planned').textContent = displayMeasurements ? duration(source.planned_elapsed_sec) : '판단 보류';
  $('pace-baseline-metric').hidden = !!display;
  $('pace-observed-metric').hidden = !!display;
  $('pace-latest-metric').hidden = !display;
  $('pace-latest').textContent = displayMeasurements && validSentence(latest) ? `${latestIndex+1}번 · ${sentenceLabels[latest.sentence_pace]}` : '확인 대기';
  $('pace-baseline').textContent = Number.isFinite(script.baseline_units_per_min) ? `${Math.round(script.baseline_units_per_min)}글자/분` : '계획 준비 중';
  $('pace-observed').textContent = displayMeasurements && Number.isFinite(source.observed_units_per_min) ? `${Math.round(source.observed_units_per_min)}글자/분` : '판단 보류';
  $('pace-ratio').textContent = displayMeasurements && Number.isFinite(source.ratio) ? `계획의 ${Math.round(source.ratio*100)}%` : '판단 보류';
  $('pace-estimated').textContent = displayMeasurements && ['fast','slow','on_plan'].includes(effectivePace) && Number.isFinite(source.estimated_total_sec) && source.estimated_total_sec > 0 ? seconds(source.estimated_total_sec) : '판단 보류';
  $('pace-reason').textContent = (display ? source.reason : script.pace_reason) || (effectivePace === 'waiting' ? '대본 진행을 확인한 뒤 속도를 안내합니다.' : '확인된 대본 진행량을 목표 시간과 비교했습니다.');
  const timingValid = displayMeasurements && validSentence(latest) && [latest.completed_at_sec, latest.processing_delay_sec, latest.adjusted_completed_at_sec].every(value => Number.isFinite(value) && value >= 0) && Math.abs(latest.completed_at_sec - latest.processing_delay_sec - latest.adjusted_completed_at_sec) < .05;
  $('pace-timing-note').textContent = timingValid ? `${latestIndex+1}번 문장: 완료 표시 ${duration(latest.completed_at_sec)} − 처리 지연 ${duration(latest.processing_delay_sec)} = 보정 시각 ${duration(latest.adjusted_completed_at_sec)}${latest.timing_estimated ? ' · 시각 추정' : ''}` : Number.isFinite(source.processing_delay_sec) && source.processing_delay_sec > 0 && !display ? `최근 내용 확인 지연 ${source.processing_delay_sec.toFixed(1)}초 · 이 지연은 속도 계산에서 제외합니다.` : '내용 판단 지연은 속도 계산에서 제외합니다.';
  $('sentence-timing').hidden = !display;
  $('sentence-timing-rows').replaceChildren(...(display ? script.units.map((unit, index) => {
    const row = node('tr', undefined, unit.keypoint_id === source.latest_keypoint_id ? 'latest' : '');
    const valid = validSentence(unit);
    row.append(node('th', `${index+1}번`), node('td', duration(unit.planned_duration_sec)), node('td', valid ? duration(unit.actual_duration_sec) : '—'));
    const status = node('td');
    status.append(node('span', valid ? sentenceLabels[unit.sentence_pace] : unit.status === 'uncertain' ? '판단 보류' : unit.status === 'explained' ? '시간 확인 대기' : '미확인', 'sentence-pace ' + (valid ? unit.sentence_pace : 'waiting')));
    if (valid && unit.timing_estimated) status.append(node('small', '시각 추정', 'timing-estimated'));
    row.append(status, node('td', valid ? duration(unit.processing_delay_sec) : '—'));
    return row;
  }) : []));
  $('pace-explanation').textContent = display ? '전체 계획 대비 속도는 확인한 문장들의 누적 배정 시간과 지연을 뺀 완료 시각을 비교합니다. 문장 속도는 해당 문장의 배정 시간과 실제 소요를 비교합니다. 음성 안내는 별도의 안정화 조건을 적용합니다.' : '계획 속도는 100%입니다. 실제 발음 속도와 다르며, 추적이 불확실하면 판단을 보류합니다.';
}
function renderScriptTracking(script) {
  const tracking = script.tracking, timing = script.slide_timing;
  const labels = {fast:'계획보다 앞섬',slow:'계획보다 늦음',on_plan:'계획 범위',waiting:'위치 확인 대기'};
  const pace = timing.reliable ? timing.pace : 'waiting';
  const duration = n => `${n.toFixed(1)}초`;
  $('script-pace-state').textContent = current?.session?.status === 'ended' ? '발표 종료' : labels[pace];
  $('script-pace-state').className = 'pace-status ' + pace;
  const phase = {waiting:'확인 대기',manual:'직접 선택',partial:'말하는 중',complete:'발화 완료'}[tracking.phase];
  $('script-progress').textContent = `현재 문장 ${tracking.index+1}/${script.units.length} · ${phase} · 내용 일치 확인 ${Math.round(script.fraction*100)}%`;
  $('tracking-status').textContent = tracking.reason + (tracking.recovery ? ' 현재 슬라이드에서 탐색 범위를 넓혔습니다.' : '');
  $('slide-live-time').textContent = `${tracking.slide_index+1}번 슬라이드 · 경과 ${duration(timing.elapsed_sec)} / 목표 ${duration(timing.target_duration_sec)}`;
  const delta = timing.schedule_delta_sec;
  $('slide-schedule').textContent = Number.isFinite(delta) ? `현재 대본 위치 기준 전체 계획보다 약 ${duration(Math.abs(delta))} ${delta > 0 ? '늦음' : '앞섬'} · 추정` : '전체 계획 차이는 현재 위치를 확인한 뒤 표시합니다.';
  $('slide-timing-rows').replaceChildren(...timing.records.map(record => {
    const row = node('tr');
    row.append(node('th', `${record.slide_index+1}번`), node('td', duration(record.target_duration_sec)),
      node('td', record.reliable ? duration(record.actual_duration_sec) : '판단 보류'),
      node('td', record.reliable ? `${duration(Math.abs(record.delta_sec))} ${record.delta_sec > 0 ? '늦음' : '앞섬'}` : '경계 불명확'),
      node('td', !record.reliable ? '경계 확인 필요' : record.estimated ? '발화로 추정' : '직접 전환·종료'));
    return row;
  }));
  $('pace-status-note').hidden = false;
  $('pace-status-note').textContent = '화면의 진행은 대본 위치 기준 추정입니다. 건너뛴 문장은 내용 확인 완료로 처리하지 않습니다.';
  $('pace-reason').textContent = '내용이 다르거나 STT에 오타가 있어도 위치를 찾을 수 있습니다. 불명확하면 이동을 보류합니다.';
  $('pace-timing-note').textContent = '현재 위치가 다르면 위 문장의 “여기서 이어가기”를 누르세요. 이전·다음 버튼은 슬라이드 전환 시각도 기록합니다.';
  $('sentence-timing').hidden = true;
  $('pace-explanation').textContent = '발음 속도가 아니라 목표 시간 대비 진행을 안내합니다. 자동 경계는 추정치이며 내용 생략 여부는 별도로 확인하세요.';
}
function microphoneDeviceId() { return selectedMicrophone === '' ? null : Number(selectedMicrophone); }
function renderMicrophoneState() {
  const data = current || {}, active = !!data.session && data.session.status !== 'ended';
  const preparing = isStartPreparing();
  const running = data.session?.status === 'running', status = data.audio_status;
  const testing = pendingCommand === 'microphone_test' || data.microphone_test?.status === 'testing';
  const selectionUnavailable = selectedMicrophone !== '' && selectedMicrophone === missingMicrophoneId;
  const reconnectable = running && data.audio_input_mode === 'mic' && (status === 'error' || data.audio_done === true || status === 'stopped');
  const usingMicrophone = active ? data.audio_input_mode === 'mic' : $('input-mode').value === 'mic';
  $('microphone-panel').hidden = !usingMicrophone;
  $('microphone-device').disabled = busy || preparing || microphonesRefreshing || testing || (active && !reconnectable);
  $('microphone-refresh').disabled = busy || preparing || microphonesRefreshing || testing || (active && !reconnectable);
  $('microphone-test').disabled = busy || preparing || microphonesRefreshing || testing || active || selectionUnavailable;
  $('microphone-test').textContent = testing ? '입력 확인 중…' : '마이크 입력 확인';
  $('microphone-refresh').textContent = microphonesRefreshing ? '장치 확인 중…' : '장치 새로고침';
  $('microphone-retry').hidden = !reconnectable;
  $('microphone-retry').disabled = busy || preparing || microphonesRefreshing || testing || selectionUnavailable;
  $('microphone-retry').textContent = pendingCommand === 'microphone_retry' ? '다시 연결 중…' : '마이크 다시 연결';
  $('start').disabled = active || busy || preparing || (usingMicrophone && (microphonesRefreshing || testing || selectionUnavailable));
  const device = data.microphone;
  $('microphone-state').textContent = testing ? '마이크를 열어 입력 확인 중' : selectionUnavailable ? '선택한 장치 연결 해제됨' : active && status === 'loading' ? '음성 모델과 입력 장치 준비 중' : active && status === 'recording' ? (device?.name || '마이크') + ' · 입력 수신 중' : active && status === 'recovering' ? '마이크 입력 유지 · 전사 복구 중' : reconnectable ? '마이크 연결 확인이 필요합니다.' : device ? `${device.name} · ${Math.round(device.sample_rate)} Hz` : '발표 전에 입력을 확인하세요.';
  const level = active && !testing ? data.audio_level : null, test = testing || active || microphoneSelectionChanged ? null : data.microphone_test;
  const peak = level && Number.isFinite(level.peak_dbfs) ? level.peak_dbfs : Number.isFinite(test?.peak_dbfs) ? test.peak_dbfs : null;
  $('microphone-meter').value = peak === null ? 0 : Math.max(0, Math.min(60, peak + 60));
  $('microphone-level-text').textContent = peak === null ? '아직 입력을 확인하지 않았습니다.' : `최대 ${peak.toFixed(1)} dBFS` + (Number.isFinite(level?.received_sec) ? ` · 입력 ${level.received_sec.toFixed(1)}초 수신` : '') + (peak < -55 ? ' · 신호가 약하거나 조용합니다.' : '');
  let message = microphoneDevicesError;
  if (testing) message = '한 문장 말해 보세요. 입력 신호와 연결 상태를 확인합니다.';
  if (!message && !active && microphoneSelectionChanged) message = '선택한 장치의 입력을 다시 확인하세요.';
  if (!message && test?.status === 'testing') message = '한 문장 말해 보세요. 입력 신호와 연결 상태를 확인합니다.';
  if (!message && test?.status === 'ok') message = '마이크 연결을 확인했습니다.' + (Number.isFinite(test.peak_dbfs) && test.peak_dbfs < -55 ? ' 신호가 약합니다. 마이크에 가까이 말하거나 입력 음량을 확인하세요.' : ' 발표를 시작할 수 있습니다.');
  if (!message && test?.status === 'error') message = test.error || '마이크 입력을 열 수 없습니다.';
  if (reconnectable && !testing) message = data.audio_error || [...(data.session?.issues || [])].reverse().find(issue => /마이크|PortAudio|음성 입력/.test(issue)) || message || '입력 장치를 확인한 뒤 마이크를 다시 연결하세요.';
  if (test?.hint && (test.status === 'error' || reconnectable)) message += (message ? ' ' : '') + test.hint;
  if (selectionUnavailable) message = (reconnectable && message ? message + ' ' : '') + '선택한 장치가 연결 해제되었습니다. 시스템 기본 마이크나 연결된 다른 장치를 직접 선택해 주세요.';
  $('microphone-message').textContent = message;
  $('microphone-message').classList.toggle('microphone-error', !testing && (!!microphoneDevicesError || test?.status === 'error' || reconnectable || selectionUnavailable));
}
async function refreshMicrophones() {
  if (microphonesRefreshing) return;
  microphonesRefreshing = true;
  microphoneDevicesError = '';
  renderMicrophoneState();
  try {
    const data = await api('microphones');
    knownMicrophoneIds = new Set((data.devices || []).map(device => String(device.id)));
    const options = [node('option', '시스템 기본 마이크')];
    options[0].value = '';
    for (const device of data.devices || []) {
      microphoneNames.set(String(device.id), device.name);
      const option = node('option', `${device.name}${device.is_default ? ' · 기본' : ''}`);
      option.value = String(device.id);
      options.push(option);
    }
    const missing = selectedMicrophone !== '' && !options.some(option => option.value === selectedMicrophone);
    missingMicrophoneId = missing ? selectedMicrophone : null;
    if (missing) {
      microphoneSelectionChanged = true;
      const option = node('option', `${microphoneNames.get(selectedMicrophone) || '선택한 마이크'} · 연결 해제됨 · 다시 선택 필요`);
      option.value = selectedMicrophone;
      options.push(option);
    }
    $('microphone-device').replaceChildren(...options);
    $('microphone-device').value = selectedMicrophone;
    microphoneDevicesError = data.error || (!(data.devices || []).length ? '사용할 수 있는 입력 장치가 없습니다. 장치를 연결하고 새로고침하세요.' : '');
  } catch (e) { microphoneDevicesError = '입력 장치를 확인할 수 없습니다. ' + e.message; }
  finally { microphonesRefreshing = false; renderMicrophoneState(); }
}
function render(data) {
  current = data;
  const s = data.session, deck = s ? s.deck : data.deck;
  const index = s?.script_progress?.tracking?.slide_index ?? (s ? s.index : 0);
  if (!microphoneSelectionInitialized) {
    microphoneSelectionInitialized = true;
    const requested = data.audio_requested_device_id;
    if (!microphoneSelectionTouched && s && s.status !== 'ended' && data.audio_input_mode === 'mic' && (requested === null || Number.isInteger(requested))) {
      selectedMicrophone = requested === null ? '' : String(requested);
      if (selectedMicrophone !== '') {
        if (data.microphone?.device_id === requested) microphoneNames.set(selectedMicrophone, data.microphone.name);
        const unavailable = knownMicrophoneIds !== null && !knownMicrophoneIds.has(selectedMicrophone);
        if (unavailable) missingMicrophoneId = selectedMicrophone;
        if (!Array.from($('microphone-device').children).some(option => option.value === selectedMicrophone)) {
          const option = node('option', `${microphoneNames.get(selectedMicrophone) || '기존 선택 마이크'} · ${unavailable ? '연결 해제됨 · 다시 선택 필요' : '장치 목록 확인 중'}`);
          option.value = selectedMicrophone;
          $('microphone-device').append(option);
        }
      }
      $('microphone-device').value = selectedMicrophone;
    }
  }
  const script = s?.script_progress, plan = deck.script_plan;
  const displaySlides = plan?.slides ? plan.slides.map(item => ({...item, keypoints: deck.slides.flatMap(x => x.keypoints).filter(p => item.keypoint_ids.includes(p.keypoint_id))})) : deck.slides;
  const slide = displaySlides[index], running = s && s.status === 'running';
  $('deck-title').textContent = deck.title;
  if (plan && !$('script-text').value) {$('script-text').value = deck.script_text; $('script-duration').value = deck.total_duration_sec; $('script-slide-durations').value = (deck.script_slide_durations_sec || []).join(', ');}
  $('script-progress-card').hidden = !script;
  if (script) renderScriptPace(script, deck);
  $('script-plan').textContent = plan ? `대본 ${plan.units.length}문장 · ${displaySlides.length}슬라이드 · 목표 ${seconds(deck.total_duration_sec)} · 슬라이드별 ${displaySlides.map(item => item.target_duration_sec.toFixed(1)+'초').join(' / ')}` : '대본과 목표 시간을 넣으면 슬라이드별 예정 시간을 계산합니다.';
  renderPreparationState();
  renderVoiceState();

  $('connection').textContent = '로컬 연결됨';
  $('connection-dot').style.background = '#5b9470';
  $('audio-status').textContent = isStartPreparing() ? '모델 준비 중' : data.coach_status?.status === 'error' && (!s || s.status === 'ended') ? '모델 준비 실패' : ({idle:'준비',manual:'전사 입력 모드',loading:'모델·마이크 준비 중',recording:'● 마이크 입력 수신 중',recovering:'● 음성 입력 유지 · STT 복구 중',stopped:'음성 입력 종료',error:'마이크 입력 오류'})[data.audio_status] || data.audio_status;
  $('session-status').textContent = isStartPreparing() ? '발표 시작 대기' : !s ? '발표 준비' : s.status === 'running' && data.audio_status === 'loading' ? '음성 입력 준비 중' : s.status === 'running' && data.audio_input_mode === 'mic' && ['error', 'stopped'].includes(data.audio_status) ? '발표 진행 중 · 음성 입력 중단' : ({running:'발표 진행 중',stopping:'마지막 발화 처리 중',ended:'발표 종료'})[s.status];
  renderMicrophoneState();
  $('stop').disabled = !running;
  $('export').disabled = !s;
  $('previous').disabled = !running || index === 0;
  $('next').disabled = !running || index === displaySlides.length - 1;
  $('slide-count').textContent = `SLIDE ${String(index+1).padStart(2,'0')} / ${String(displaySlides.length).padStart(2,'0')}`;
  $('slide-title').textContent = slide.title;
  $('slide-target').textContent = `목표 시간 ${seconds(slide.target_duration_sec)}`;
  $('slides').replaceChildren(...displaySlides.map((item, i) => {
    const button = node('button', undefined, `slide-button${index === i ? ' active' : ''}`);
    button.disabled = !running;
    if (index === i) button.setAttribute('aria-current','step');
    button.append(node('span', String(i+1).padStart(2,'0'), 'slide-number'));
    const copy = node('span',item.title,'slide-copy'); copy.append(node('small',seconds(item.target_duration_sec)));
    button.append(copy); button.addEventListener('click',() => command('navigate',{index:i})); return button;
  }));
  let explained = 0;
  $('keypoints').replaceChildren(...slide.keypoints.map(point => {
    const state = s ? s.states[point.keypoint_id] : {status:'unconfirmed',reason:'아직 확인되지 않았습니다.'};
    if (state.status === 'explained') explained++;
    const li = node('li');
    if (script?.tracking && plan.units[script.tracking.index]?.keypoint_id === point.keypoint_id) li.className = 'current-sentence';
    li.append(node('span', state.status === 'explained' ? '✓' : state.status === 'uncertain' ? '?' : '·', 'point-icon ' + state.status));
    const text = node('div',point.text,'point-text');
    const planned = plan?.units.find(p => p.keypoint_id === point.keypoint_id);
    if (planned) text.append(node('small',`예정 ${seconds(planned.planned_start_sec)}–${seconds(planned.planned_end_sec)}`));
    text.append(node('small',`${point.required ? '필수' : '선택'} · ${state.reason}`));
    if (state.evidence_segment_ids?.length) text.append(node('small','근거: ' + state.evidence_segment_ids.join(', ')));
    if (plan?.slides) {
      const seek = node('button', '여기서 이어가기', 'secondary resync-button');
      seek.disabled = !running;
      seek.addEventListener('click', () => command('resync', {index:plan.units.findIndex(p => p.keypoint_id === point.keypoint_id)}));
      text.append(seek);
    }
    li.append(text,node('span',script?.missing_ids.includes(point.keypoint_id) ? '건너뜀 확인' : statuses[state.status],'point-state')); return li;
  }));
  $('point-count').textContent = `${explained} / ${slide.keypoints.length} 확인`;
  const processing = s?.judgment_status === 'waiting_for_silence' ? '최신 발화를 앞 문장과 이어서 확인하고 있습니다. ' : s?.judgment_status === 'evaluating' ? '최신 발화의 내용을 확인하고 있습니다. ' : '';
  $('judgment-status').textContent = !s ? '발표를 시작하면 설명 여부를 확인합니다.' : `${explained}개 설명 확인` + (s.judgment_status === 'waiting_for_silence' ? ' · 최신 문장 연결 중' : s.judgment_status === 'evaluating' ? ' · 최신 발화 판단 중' : ' · 확인 결과 반영됨');
  if (processing && explained) $('judgment-status').textContent += ' · 앞서 확인한 항목은 계속 표시합니다.';
  $('coach-note').textContent = processing + (data.coach === 'phrase_baseline' ? '문장 매칭 모드 · 전사 오타나 다른 표현은 놓칠 수 있습니다.' : '의미 판단 모드 · 발화 근거로 확인합니다. 모델 처리에 몇 초 걸릴 수 있습니다.');
  const elapsed = s ? s.elapsed_sec : 0, remaining = s ? s.remaining_sec : deck.total_duration_sec;
  $('remaining').textContent = seconds(remaining); $('remaining').className = 'time' + (remaining < 0 ? ' over' : '');
  $('elapsed').textContent = seconds(elapsed); $('slide-elapsed').textContent = seconds(script?.slide_timing?.elapsed_sec ?? (s ? s.slide_elapsed_sec : 0));
  $('progress').style.width = Math.min(100,elapsed / deck.total_duration_sec * 100) + '%';
  const alerts = s ? s.alerts : [], alertKey = JSON.stringify(alerts);
  if (alertKey !== lastAlerts) {
    $('alert-region').replaceChildren(...alerts.map(a => { const e = node('div',undefined,'alert'); const source = deck.slides.find(x => x.slide_id === a.slide_id); e.append(node('strong',source ? `${source.title} · 확인` : '시간 안내'),node('span',a.message)); return e; }));
    lastAlerts = alertKey;
  }
  const segments = s ? s.segments : [];
  const explainedEvidence = new Set(slide.keypoints.filter(point => s?.states[point.keypoint_id]?.status === 'explained').flatMap(point => s.states[point.keypoint_id].evidence_segment_ids || []));
  const uncertainEvidence = new Set(slide.keypoints.filter(point => s?.states[point.keypoint_id]?.status === 'uncertain').flatMap(point => s.states[point.keypoint_id].evidence_segment_ids || []));
  $('transcripts').replaceChildren(...(segments.length ? segments.map(segment => {
    const e = node('div',undefined,'utterance');
    const received = !segment.status || segment.status === 'OK';
    const stage = !received ? '전사 오류' : explainedEvidence.has(segment.segment_id) ? '설명 근거 확인' : uncertainEvidence.has(segment.segment_id) ? '내용 확인 필요' : '전사 수신';
    const endpoint = {silence:'짧은 쉼',hard_max_duration:'이어 말하기',hard_cut_continuation:'이어 말하기',soft_max_duration:'이어 말하기',flush:'마지막 발화'}[segment.endpoint_reason] || segment.endpoint_reason;
    const delay = Number.isFinite(segment.estimated_feedback_delay_ms) ? ` · 전사 약 ${(segment.estimated_feedback_delay_ms / 1000).toFixed(2)}초` : '';
    e.append(node('small',`${seconds(segment.start_sec)}–${seconds(segment.end_sec)} · ${segment.slide_ids.map(id => deck.slides.find(x=>x.slide_id===id)?.title || id).join(' / ')} · ${endpoint}${delay}`),node('span',stage,'transcript-stage' + (explainedEvidence.has(segment.segment_id) ? ' confirmed' : !received ? ' failed' : '')),node('p',segment.text)); return e;
  }) : [node('div','확정된 발화가 여기에 표시됩니다.','empty')]));
  const manual = running && data.audio_input_mode === 'manual';
  $('utterance').disabled = !manual; $('submit').disabled = !manual; $('endpoint').disabled = !manual;
  $('utterance-form').hidden = data.audio_input_mode !== 'manual';
  $('issues').replaceChildren(...(s?.issues.length ? s.issues.map(issue => node('p',issue,'issue')) : [node('p',s ? `이벤트 ${s.event_count}개 기록 · ${s.status === 'ended' ? '종료 결과 저장됨' : '자동 저장 중'}` : '발표를 시작하면 기록합니다.','muted')]));
  $('save-path').textContent = data.output_path || '';
}
$('start').addEventListener('click',() => command('start',{microphone:$('input-mode').value === 'mic',device_id:microphoneDeviceId(),voice:$('voice-enabled').checked,voice_scope:$('voice-scope').value}));
$('input-mode').addEventListener('change',renderMicrophoneState);
$('microphone-device').addEventListener('change',e => {selectedMicrophone = e.target.value; microphoneSelectionChanged = true; microphoneSelectionTouched = true; renderMicrophoneState();});
$('microphone-refresh').addEventListener('click',refreshMicrophones);
$('microphone-test').addEventListener('click',() => {microphoneSelectionChanged = false; return command('microphone_test',{device_id:microphoneDeviceId()});});
$('microphone-retry').addEventListener('click',() => command('microphone_retry',{device_id:microphoneDeviceId()}));
$('stop').addEventListener('click',() => command('stop',{}));
$('previous').addEventListener('click',() => command('navigate',{index:(current.session.script_progress?.tracking?.slide_index ?? current.session.index)-1}));
$('next').addEventListener('click',() => command('navigate',{index:(current.session.script_progress?.tracking?.slide_index ?? current.session.index)+1}));
$('utterance-form').addEventListener('submit',async e => {e.preventDefault(); if (!$('utterance').value.trim()) return; if (await command('utterance',{text:$('utterance').value,endpoint_reason:$('endpoint').value})) $('utterance').value='';});
$('upload').addEventListener('click',() => $('deck-file').click());
$('deck-file').addEventListener('change',async e => {try {const file=e.target.files[0]; if(file) {if(file.size>1048576) throw new Error('자료 파일은 1MB 이하로 준비해 주세요.'); await command('deck',JSON.parse(await file.text()));}} catch(err) {error(err.message);} e.target.value='';});
$('export').addEventListener('click',async () => {try {const data=await api('export'); const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=node('a');a.href=url;a.download=`presentation_${data.session_id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);} catch(e) {error(e.message);}});
async function poll() {const revision = commandRevision; try {if(!busy || pendingCommand === 'start') {const data = await api('state'); if (revision === commandRevision && (!busy || pendingCommand === 'start')) render(data);}} catch(e) {$('connection').textContent='서버 연결 끊김';$('connection-dot').style.background='#c17960';renderLiveFeedback(current,true);} finally {setTimeout(poll,200);}}
poll();
refreshMicrophones();

$('prepare-script').addEventListener('click',async () => {
  const custom = $('script-slide-durations').value.trim();
  await command('script',{text:$('script-text').value,duration_sec:Number($('script-duration').value),
    slide_durations_sec:custom ? custom.split(',').map(value => Number(value.trim())) : null});
});
$('voice-enabled').addEventListener('change',() => command('voice',{enabled:$('voice-enabled').checked,scope:$('voice-scope').value}));
$('voice-scope').addEventListener('change',() => command('voice',{enabled:$('voice-enabled').checked,scope:$('voice-scope').value}));
$('voice-test').addEventListener('click',() => command('voice_test',{}));
