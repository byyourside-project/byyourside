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
  const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), data === undefined ? 8000 : 30000);
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
  try { render(await api(action, data)); return true; } catch (e) { error(e.message); return false; }
  finally { busy = false; pendingCommand = null; renderMicrophoneState(); renderVoiceState(false); }
}
function renderVoiceState(syncSelection = true) {
  const data = current || {}, scope = data.voice_scope === 'all' ? 'all' : 'pace';
  if (syncSelection) {$('voice-enabled').checked = !!data.voice_enabled; $('voice-scope').value = scope;}
  $('voice-enabled').disabled = !data.voice_available || busy;
  $('voice-scope').disabled = !data.voice_available || busy;
  $('voice-test').disabled = data.session?.status !== 'running' || !data.voice_enabled || !data.voice_available || busy;
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
  const reliable = script.reliable === true;
  const effectivePace = reliable ? script.pace : 'waiting';
  const pace = {waiting:'판단 보류',fast:'계획보다 빠름',slow:'계획보다 느림',on_plan:'계획 내'}[effectivePace] || '판단 보류';
  const measured = Number.isFinite(script.measured_elapsed_sec) ? script.measured_elapsed_sec : null;
  const displayMeasurements = reliable && measured !== null && measured > 0;
  $('script-pace-state').textContent = pace;
  $('script-pace-state').className = 'pace-status ' + (['fast','slow','on_plan'].includes(effectivePace) ? effectivePace : 'waiting');
  $('script-progress').textContent = `현재 확인 위치 ${script.position_index+1}/${script.units.length} · 설명 확인 ${Math.round(script.fraction*100)}% · 건너뛴 구간 확인 ${script.missing_ids.length}개` + (script.next_text ? ` · 다음 예정: ${script.next_text}` : ' · 마지막 구간까지 확인');
  $('pace-target').textContent = seconds(Number.isFinite(script.plan_total_duration_sec) ? script.plan_total_duration_sec : deck.total_duration_sec);
  $('pace-measured').textContent = measured !== null && measured > 0 ? seconds(measured) : '확인 대기';
  $('pace-baseline').textContent = Number.isFinite(script.baseline_units_per_min) ? `${Math.round(script.baseline_units_per_min)}글자/분` : '계획 준비 중';
  $('pace-observed').textContent = displayMeasurements && Number.isFinite(script.observed_units_per_min) ? `${Math.round(script.observed_units_per_min)}글자/분` : '판단 보류';
  $('pace-ratio').textContent = displayMeasurements && Number.isFinite(script.ratio) ? `계획의 ${Math.round(script.ratio*100)}%` : '판단 보류';
  $('pace-estimated').textContent = displayMeasurements && ['fast','slow','on_plan'].includes(effectivePace) && Number.isFinite(script.estimated_total_sec) && script.estimated_total_sec > 0 ? seconds(script.estimated_total_sec) : '판단 보류';
  $('pace-reason').textContent = script.pace_reason || (script.pace === 'waiting' || !reliable ? '대본 진행을 확인한 뒤 속도를 안내합니다.' : '확인된 대본 진행량을 목표 시간과 비교했습니다.');
  $('pace-timing-note').textContent = Number.isFinite(script.processing_delay_sec) && script.processing_delay_sec > 0 ? `최근 내용 확인 지연 ${script.processing_delay_sec.toFixed(1)}초 · 이 지연은 속도 계산에서 제외합니다.` : '내용 판단 지연은 속도 계산에서 제외합니다.';
}
function microphoneDeviceId() { return selectedMicrophone === '' ? null : Number(selectedMicrophone); }
function renderMicrophoneState() {
  const data = current || {}, active = !!data.session && data.session.status !== 'ended';
  const running = data.session?.status === 'running', status = data.audio_status;
  const testing = pendingCommand === 'microphone_test' || data.microphone_test?.status === 'testing';
  const selectionUnavailable = selectedMicrophone !== '' && selectedMicrophone === missingMicrophoneId;
  const reconnectable = running && data.audio_input_mode === 'mic' && (status === 'error' || data.audio_done === true || status === 'stopped');
  const usingMicrophone = active ? data.audio_input_mode === 'mic' : $('input-mode').value === 'mic';
  $('microphone-panel').hidden = !usingMicrophone;
  $('microphone-device').disabled = busy || microphonesRefreshing || testing || (active && !reconnectable);
  $('microphone-refresh').disabled = busy || microphonesRefreshing || testing || (active && !reconnectable);
  $('microphone-test').disabled = busy || microphonesRefreshing || testing || active || selectionUnavailable;
  $('microphone-test').textContent = testing ? '입력 확인 중…' : '마이크 입력 확인';
  $('microphone-refresh').textContent = microphonesRefreshing ? '장치 확인 중…' : '장치 새로고침';
  $('microphone-retry').hidden = !reconnectable;
  $('microphone-retry').disabled = busy || microphonesRefreshing || testing || selectionUnavailable;
  $('microphone-retry').textContent = pendingCommand === 'microphone_retry' ? '다시 연결 중…' : '마이크 다시 연결';
  $('start').disabled = active || busy || (usingMicrophone && (microphonesRefreshing || testing || selectionUnavailable));
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
  const s = data.session, deck = s ? s.deck : data.deck, index = s ? s.index : 0;
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
  const slide = deck.slides[index], running = s && s.status === 'running';
  $('deck-title').textContent = deck.title;
  const script = s?.script_progress, plan = deck.script_plan;
  if (plan && !$('script-text').value) {$('script-text').value = deck.script_text; $('script-duration').value = deck.total_duration_sec;}
  $('script-progress-card').hidden = !script;
  if (script) renderScriptPace(script, deck);
  $('script-plan').textContent = plan ? `대본 ${plan.units.length}구간 · 목표 ${seconds(deck.total_duration_sec)} · 기준 분당 ${Math.round(plan.baseline_units_per_min)}글자 (공백·문장부호 제외)` : '대본과 목표 시간을 넣으면 구간별 예정 시간과 기준 속도를 계산합니다.';
  for (const id of ['script-text','script-duration','prepare-script']) $(id).disabled = !!s && s.status !== 'ended';
  renderVoiceState();

  $('connection').textContent = '로컬 연결됨';
  $('connection-dot').style.background = '#5b9470';
  $('audio-status').textContent = ({idle:'준비',manual:'전사 입력 모드',loading:'모델·마이크 준비 중',recording:'● 마이크 입력 수신 중',recovering:'● 음성 입력 유지 · STT 복구 중',stopped:'음성 입력 종료',error:'마이크 입력 오류'})[data.audio_status] || data.audio_status;
  $('session-status').textContent = !s ? '발표 준비' : s.status === 'running' && data.audio_status === 'loading' ? '음성 입력 준비 중' : s.status === 'running' && data.audio_input_mode === 'mic' && ['error', 'stopped'].includes(data.audio_status) ? '발표 진행 중 · 음성 입력 중단' : ({running:'발표 진행 중',stopping:'마지막 발화 처리 중',ended:'발표 종료'})[s.status];
  renderMicrophoneState();
  $('stop').disabled = !running;
  $('input-mode').disabled = !!s && s.status !== 'ended';
  $('upload').disabled = !!s && s.status !== 'ended';
  $('export').disabled = !s;
  $('previous').disabled = !running || index === 0;
  $('next').disabled = !running || index === deck.slides.length - 1;
  $('slide-count').textContent = `SLIDE ${String(index+1).padStart(2,'0')} / ${String(deck.slides.length).padStart(2,'0')}`;
  $('slide-title').textContent = slide.title;
  $('slide-target').textContent = `목표 시간 ${seconds(slide.target_duration_sec)}`;
  $('slides').replaceChildren(...deck.slides.map((item, i) => {
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
    li.append(node('span', state.status === 'explained' ? '✓' : state.status === 'uncertain' ? '?' : '·', 'point-icon ' + state.status));
    const text = node('div',point.text,'point-text');
    const planned = plan?.units.find(p => p.keypoint_id === point.keypoint_id);
    if (planned) text.append(node('small',`예정 ${seconds(planned.planned_start_sec)}–${seconds(planned.planned_end_sec)}`));
    text.append(node('small',`${point.required ? '필수' : '선택'} · ${state.reason}`));
    if (state.evidence_segment_ids?.length) text.append(node('small','근거: ' + state.evidence_segment_ids.join(', ')));
    li.append(text,node('span',script?.missing_ids.includes(point.keypoint_id) ? '건너뜀 확인' : statuses[state.status],'point-state')); return li;
  }));
  $('point-count').textContent = `${explained} / ${slide.keypoints.length} 확인`;
  const processing = s?.judgment_status === 'waiting_for_silence' ? '문장을 모으는 중입니다. 말을 마치고 잠깐 쉬면 판단합니다. ' : s?.judgment_status === 'evaluating' ? '내용 판단 중입니다. 결과를 기다려 주세요. ' : '';
  $('coach-note').textContent = processing + (data.coach === 'phrase_baseline' ? '문장 매칭 모드 · 전사 오타나 다른 표현은 놓칠 수 있습니다.' : '의미 판단 모드 · 발화 근거로 확인합니다. 모델 처리에 몇 초 걸릴 수 있습니다.');
  const elapsed = s ? s.elapsed_sec : 0, remaining = s ? s.remaining_sec : deck.total_duration_sec;
  $('remaining').textContent = seconds(remaining); $('remaining').className = 'time' + (remaining < 0 ? ' over' : '');
  $('elapsed').textContent = seconds(elapsed); $('slide-elapsed').textContent = seconds(s ? s.slide_elapsed_sec : 0);
  $('progress').style.width = Math.min(100,elapsed / deck.total_duration_sec * 100) + '%';
  const alerts = s ? s.alerts : [], alertKey = JSON.stringify(alerts);
  if (alertKey !== lastAlerts) {
    $('alert-region').replaceChildren(...alerts.map(a => { const e = node('div',undefined,'alert'); const source = deck.slides.find(x => x.slide_id === a.slide_id); e.append(node('strong',source ? `${source.title} · 확인` : '시간 안내'),node('span',a.message)); return e; }));
    lastAlerts = alertKey;
  }
  const segments = s ? s.segments : [];
  $('transcripts').replaceChildren(...(segments.length ? segments.map(segment => {
    const e = node('div',undefined,'utterance'); e.append(node('small',`${seconds(segment.start_sec)}–${seconds(segment.end_sec)} · ${segment.slide_ids.map(id => deck.slides.find(x=>x.slide_id===id)?.title || id).join(' / ')} · ${segment.endpoint_reason}`),node('p',segment.text)); return e;
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
$('previous').addEventListener('click',() => command('navigate',{index:current.session.index-1}));
$('next').addEventListener('click',() => command('navigate',{index:current.session.index+1}));
$('utterance-form').addEventListener('submit',async e => {e.preventDefault(); if (!$('utterance').value.trim()) return; if (await command('utterance',{text:$('utterance').value,endpoint_reason:$('endpoint').value})) $('utterance').value='';});
$('upload').addEventListener('click',() => $('deck-file').click());
$('deck-file').addEventListener('change',async e => {try {const file=e.target.files[0]; if(file) {if(file.size>1048576) throw new Error('자료 파일은 1MB 이하로 준비해 주세요.'); await command('deck',JSON.parse(await file.text()));}} catch(err) {error(err.message);} e.target.value='';});
$('export').addEventListener('click',async () => {try {const data=await api('export'); const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=node('a');a.href=url;a.download=`presentation_${data.session_id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);} catch(e) {error(e.message);}});
async function poll() {const revision = commandRevision; try {if(!busy) {const data = await api('state'); if (!busy && revision === commandRevision) render(data);}} catch(e) {$('connection').textContent='서버 연결 끊김';$('connection-dot').style.background='#c17960';} finally {setTimeout(poll,200);}}
poll();
refreshMicrophones();

$('prepare-script').addEventListener('click',async () => {await command('script',{text:$('script-text').value,duration_sec:Number($('script-duration').value)});});
$('voice-enabled').addEventListener('change',() => command('voice',{enabled:$('voice-enabled').checked,scope:$('voice-scope').value}));
$('voice-scope').addEventListener('change',() => command('voice',{enabled:$('voice-enabled').checked,scope:$('voice-scope').value}));
$('voice-test').addEventListener('click',() => command('voice_test',{}));
