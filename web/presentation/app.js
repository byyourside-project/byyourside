'use strict';
const $ = id => document.getElementById(id);
let current = null, busy = false, lastAlerts = '';
const statuses = {unconfirmed:'미확인', explained:'설명됨', uncertain:'판단불가'};
const seconds = n => `${n < 0 ? '+' : ''}${String(Math.floor(Math.abs(n) / 60)).padStart(2,'0')}:${String(Math.floor(Math.abs(n) % 60)).padStart(2,'0')}`;
function node(tag, text, cls) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; }
function error(message) { $('error').textContent = message; $('error').hidden = !message; }
async function api(action, data) {
  const response = await fetch('/api/' + action, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '요청을 처리할 수 없습니다.');
  return result;
}
async function command(action, data) {
  if (busy) return;
  busy = true;
  error('');
  try { render(await api(action, data)); } catch (e) { error(e.message); }
  finally { busy = false; }
}
function render(data) {
  current = data;
  const s = data.session, deck = s ? s.deck : data.deck, index = s ? s.index : 0;
  const slide = deck.slides[index], running = s && s.status === 'running';
  $('deck-title').textContent = deck.title;
  const script = s?.script_progress, plan = deck.script_plan;
  if (plan && !$('script-text').value) {$('script-text').value = deck.script_text; $('script-duration').value = deck.total_duration_sec;}
  $('script-progress-card').hidden = !script;
  if (script) {
    const pace = {waiting:'속도 판단 대기',fast:'계획보다 빠름',slow:'계획보다 느림',on_plan:'계획 범위'}[script.pace];
    $('script-progress').textContent = `현재 확인 위치 ${script.position_index+1}/${script.units.length} · 설명 확인 ${Math.round(script.fraction*100)}% · ${pace} · 건너뛴 구간 확인 ${script.missing_ids.length}개` + (script.estimated_total_sec ? ` · 예상 전체 시간 ${seconds(script.estimated_total_sec)}` : '') + (script.next_text ? ` · 다음 예정: ${script.next_text}` : ' · 마지막 구간까지 확인');
  }
  $('script-plan').textContent = plan ? `대본 ${plan.units.length}구간 · 목표 ${seconds(deck.total_duration_sec)} · 기준 분당 ${Math.round(plan.baseline_units_per_min)}글자 (공백·문장부호 제외)` : '대본과 목표 시간을 넣으면 구간별 예정 시간과 기준 속도를 계산합니다.';
  for (const id of ['script-text','script-duration','prepare-script']) $(id).disabled = !!s && s.status !== 'ended';
  $('voice-enabled').disabled = !data.voice_available;
  $('voice-test').disabled = !s || !data.voice_enabled;
  $('voice-status').textContent = !data.voice_available ? '이 환경의 로컬 음성 출력을 사용할 수 없습니다.' : data.voice_enabled ? '음성 안내 켜짐 · 이어폰 출력 장치를 확인하세요.' : '음성 안내 꺼짐';
  if (data.voice_last_event) $('voice-status').textContent += ' · ' + ({voice_queued:'안내 대기',voice_started:'안내 재생 중',voice_completed:'안내 재생 완료',voice_cancelled:'지난 안내 취소',voice_failed:'음성 출력 실패'})[data.voice_last_event.type];

  $('connection').textContent = '로컬 연결됨';
  $('connection-dot').style.background = '#5b9470';
  $('audio-status').textContent = ({idle:'준비',manual:'전사 입력 모드',loading:'모델 준비 중',recording:'● 마이크 사용 중',recovering:'● 음성 입력 유지 · STT 복구 중',stopped:'음성 입력 종료',error:'음성 입력 오류'})[data.audio_status] || data.audio_status;
  $('session-status').textContent = !s ? '발표 준비' : ({running:'발표 진행 중',stopping:'마지막 발화 처리 중',ended:'발표 종료'})[s.status];
  $('start').disabled = s && s.status !== 'ended';
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
  const manual = running && !['loading','recording','recovering'].includes(data.audio_status);
  $('utterance').disabled = !manual; $('submit').disabled = !manual; $('endpoint').disabled = !manual;
  $('utterance-form').hidden = ['loading','recording','recovering'].includes(data.audio_status);
  $('issues').replaceChildren(...(s?.issues.length ? s.issues.map(issue => node('p',issue,'issue')) : [node('p',s ? `이벤트 ${s.event_count}개 기록 · ${s.status === 'ended' ? '종료 결과 저장됨' : '자동 저장 중'}` : '발표를 시작하면 기록합니다.','muted')]));
  $('save-path').textContent = data.output_path || '';
}
$('start').addEventListener('click',() => command('start',{microphone:$('input-mode').value === 'mic',voice:$('voice-enabled').checked}));
$('stop').addEventListener('click',() => command('stop',{}));
$('previous').addEventListener('click',() => command('navigate',{index:current.session.index-1}));
$('next').addEventListener('click',() => command('navigate',{index:current.session.index+1}));
$('utterance-form').addEventListener('submit',async e => {e.preventDefault(); if (!$('utterance').value.trim()) return; await command('utterance',{text:$('utterance').value,endpoint_reason:$('endpoint').value}); if ($('error').hidden) $('utterance').value='';});
$('upload').addEventListener('click',() => $('deck-file').click());
$('deck-file').addEventListener('change',async e => {try {const file=e.target.files[0]; if(file) {if(file.size>1048576) throw new Error('자료 파일은 1MB 이하로 준비해 주세요.'); await command('deck',JSON.parse(await file.text()));}} catch(err) {error(err.message);} e.target.value='';});
$('export').addEventListener('click',async () => {try {const data=await api('export'); const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=node('a');a.href=url;a.download=`presentation_${data.session_id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);} catch(e) {error(e.message);}});
async function poll() {try {if(!busy) render(await api('state'));} catch(e) {$('connection').textContent='서버 연결 끊김';$('connection-dot').style.background='#c17960';} finally {setTimeout(poll,200);}}
poll();

$('prepare-script').addEventListener('click',async () => {await command('script',{text:$('script-text').value,duration_sec:Number($('script-duration').value)});});
$('voice-enabled').addEventListener('change',() => command('voice',{enabled:$('voice-enabled').checked}));
$('voice-test').addEventListener('click',() => command('voice_test',{}));
