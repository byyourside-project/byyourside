const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

class Element {
  constructor(tag) {
    this.tagName = tag;
    this.textContent = '';
    this.value = '';
    this.hidden = false;
    this.disabled = false;
    this.style = {};
    this.listeners = {};
    this.classes = new Set();
    this.classList = {toggle: (name, enabled) => {if (enabled) this.classes.add(name); else this.classes.delete(name);}};
  }
  addEventListener(name, callback) {this.listeners[name] = callback;}
  append(...elements) {this.children = (this.children || []).concat(elements);}
  replaceChildren(...elements) {this.children = elements;}
  setAttribute() {}
}

const elements = new Map();
const document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new Element(id));
    return elements.get(id);
  },
  createElement(tag) {return new Element(tag);},
};
document.getElementById('input-mode').value = 'mic';
const deck = {
  title: '테스트', total_duration_sec: 90,
  slides: [{slide_id: 's1', title: '도입', target_duration_sec: 90, keypoints: []}],
};
const base = {
  deck, session: null, audio_status: 'idle', audio_input_mode: null,
  voice_available: false, voice_enabled: false, coach: 'local_http', audio_done: true,
};
const session = {
  deck, index: 0, status: 'running', elapsed_sec: 1, remaining_sec: 89,
  slide_elapsed_sec: 1, states: {}, alerts: [], segments: [],
  issues: ['마이크/STT 오류: Internal PortAudio error'], event_count: 1,
};
let responseState = base;
let deviceList = [
  {id: 0, name: '내장 마이크', is_default: true},
  {id: 1, name: 'USB 마이크', is_default: false},
];
let deferredProbe = null;
let deferredStart = null, deferredState = null;
const requests = [];
const requestTimeouts = [];
const context = vm.createContext({
  document, console, AbortController, Blob, URL,
  setTimeout(callback, milliseconds) {requestTimeouts.push(milliseconds); return 1;}, clearTimeout() {},
  fetch: async (url, options) => {
    requests.push({url, options});
    if (url === '/api/start' && deferredStart) {
      return await new Promise(resolve => {deferredStart.resolve = resolve;});
    }
    if (url === '/api/state' && deferredState) {
      return await new Promise(resolve => {deferredState.resolve = resolve;});
    }
    if (url === '/api/microphone_test' && deferredProbe) {
      return await new Promise(resolve => {deferredProbe.resolve = resolve;});
    }
    return {
      ok: true,
      json: async () => url === '/api/microphones' ? {devices: deviceList} : responseState,
    };
  },
});
vm.runInContext(fs.readFileSync('web/presentation/app.js', 'utf8'), context);
let passed = 0;
function check(condition, label) {
  assert(condition, label);
  console.log(`PASS ${++passed}: ${label}`);
}
function render(data) {context.data = data; vm.runInContext('render(data)', context);}
function select(value) {
  elements.get('microphone-device').value = value;
  elements.get('microphone-device').listeners.change({target: {value}});
}

(async () => {
  await new Promise(resolve => setImmediate(resolve));
  check(!requests.some(request => request.options.method === 'POST'), '초기화는 마이크를 자동으로 열지 않음');
  render({...base, session, audio_input_mode: 'mic', audio_status: 'error', audio_done: true,
    audio_error: '마이크/STT 오류: Internal PortAudio error'});
  check(elements.get('utterance-form').hidden, '마이크 오류를 직접 입력 모드로 오인하지 않음');
  check(!elements.get('microphone-retry').hidden && !elements.get('microphone-device').disabled,
    '마이크 오류에서는 장치 변경과 재시도가 가능');
  check(elements.get('microphone-message').textContent.includes('PortAudio'), '원래 마이크 오류를 표시');
  select('1');
  render(context.data);
  check(elements.get('microphone-device').value === '1', '폴링이 사용자 장치 선택을 덮어쓰지 않음');
  render({...context.data, audio_status: 'loading', audio_done: false});
  check(!elements.get('audio-status').textContent.includes('수신 중') && elements.get('microphone-retry').hidden,
    '로딩을 입력 수신으로 표시하거나 중복 재시도를 허용하지 않음');
  render({...context.data, audio_status: 'manual', audio_input_mode: 'manual'});
  check(!elements.get('utterance-form').hidden && !elements.get('utterance').disabled,
    '명시적인 manual 세션에서 직접 전사 입력을 허용');
  render({...base, microphone_test: {status: 'ok', device: {id: 0, name: '내장 마이크'}, peak_dbfs: -90}});
  check(elements.get('microphone-message').textContent.includes('다시 확인'),
    '장치 변경 후 이전 장치의 테스트 성공 결과를 무효화');
  deviceList = [{id: 0, name: '내장 마이크', is_default: true}];
  await vm.runInContext('refreshMicrophones()', context);
  check(elements.get('microphone-device').value === '1' && elements.get('microphone-device').children.some(
    option => option.value === '1' && option.textContent.includes('연결 해제됨 · 다시 선택 필요')),
    '연결 해제된 ID와 옵션을 유지하고 기본 마이크로 자동 전환하지 않음');
  check(elements.get('microphone-test').disabled && elements.get('start').disabled,
    '연결 해제된 선택에서는 입력 확인과 발표 시작을 차단');
  render({...base, session, audio_input_mode: 'mic', audio_status: 'error', audio_done: true});
  check(elements.get('microphone-retry').disabled, '연결 해제된 선택에서는 재연결도 차단');
  const before = requests.length;
  const blocked = await vm.runInContext("command('start', {microphone:true, device_id:1})", context);
  check(blocked === false && requests.length === before, '프로그램으로 클릭해도 연결 해제된 장치를 캡처하지 않음');
  render(base);
  select('0');
  check(!elements.get('microphone-test').disabled && !elements.get('start').disabled,
    '사용자가 연결된 다른 장치를 직접 선택하면 작업 허용');
  deferredProbe = {};
  render({...base, microphone_test: {status: 'error', error: '이전 입력 오류', hint: '이전 접근 권한 안내', peak_dbfs: -90}});
  const pending = elements.get('microphone-test').listeners.click();
  check(elements.get('microphone-test').textContent === '입력 확인 중…' && elements.get('microphone-test').disabled,
    'blocking 입력 확인 요청 중 즉시 진행 상태를 표시하고 중복 클릭 차단');
  check(!elements.get('microphone-message').textContent.includes('이전') && !elements.get('microphone-message').classes.has('microphone-error'),
    '입력 확인 요청 중 이전 오류와 힌트 및 오류 색을 숨김');
  const request = requests.find(item => item.url === '/api/microphone_test');
  check(JSON.parse(request.options.body).device_id === 0, '선택한 장치 ID를 입력 확인 API에 전달');
  deferredProbe.resolve({ok: true, json: async () => ({...base, microphone_test: {status: 'ok', peak_dbfs: -25}})});
  await pending;
  check(elements.get('microphone-message').textContent.includes('연결을 확인') && !elements.get('microphone-test').disabled,
    '입력 확인 완료 후 결과를 표시하고 버튼을 복구');
  check(!elements.get('microphone-message').textContent.includes('이전'), '입력 확인 완료 후에도 이전 결과가 섞이지 않음');
  vm.runInContext('microphoneSelectionInitialized = false; microphoneSelectionTouched = false;', context);
  render({...base, session, audio_input_mode: 'mic', audio_requested_device_id: 0, audio_status: 'error',
    microphone: {device_id: 0, name: '내장 마이크', sample_rate: 48000}});
  check(elements.get('microphone-device').value === '0', '활성 마이크 세션을 처음 읽으면 요청한 장치를 복원');
  select('');
  render({...context.data, audio_requested_device_id: 0});
  check(elements.get('microphone-device').value === '', '한 번 복원 후에는 사용자 장치 선택을 폴링이 변경하지 않음');
  vm.runInContext('microphoneSelectionInitialized = false;', context);
  select('0');
  render({...context.data, audio_requested_device_id: null});
  check(elements.get('microphone-device').value === '0', '첫 상태 응답 전에 장치를 직접 골랐으면 서버 상태로 덮어쓰지 않음');
  vm.runInContext('microphoneSelectionInitialized = false; microphoneSelectionTouched = false;', context);
  render({...context.data, audio_requested_device_id: 9});
  check(elements.get('microphone-device').value === '9' && elements.get('microphone-retry').disabled,
    '활성 세션의 사라진 요청 장치도 보존하고 임의 대체 장치로 재연결하지 않음');
  render({...base, voice_available: true, voice_enabled: false, voice_scope: 'pace'});
  check(elements.get('voice-scope').value === 'pace' && elements.get('voice-enabled').checked === false,
    '음성 안내는 기본 꺼짐이며 빠름·느림만 선택');
  elements.get('voice-scope').value = 'all';
  responseState = {...base, voice_available: true, voice_enabled: false, voice_scope: 'all'};
  await elements.get('voice-scope').listeners.change();
  const scopeRequest = requests.filter(item => item.url === '/api/voice').at(-1);
  check(JSON.parse(scopeRequest.options.body).scope === 'all' && JSON.parse(scopeRequest.options.body).enabled === false,
    '전체 안내 범위로 바꿔도 음성을 자동으로 켜지 않고 API 계약을 전달');
  elements.get('voice-enabled').checked = true;
  responseState = {...responseState, voice_enabled: true};
  await elements.get('voice-enabled').listeners.change();
  const enabledRequest = requests.filter(item => item.url === '/api/voice').at(-1);
  check(JSON.parse(enabledRequest.options.body).enabled === true && JSON.parse(enabledRequest.options.body).scope === 'all' && elements.get('voice-enabled').checked,
    '음성 안내를 켤 때 현재 선택 범위를 함께 전달하고 서버 설정을 복원');
  render({...base, voice_available: true, voice_enabled: false, voice_scope: 'pace'});
  elements.get('input-mode').value = 'manual';
  responseState = {...base, voice_available: true, voice_enabled: false, voice_scope: 'pace'};
  await elements.get('start').listeners.click();
  const startRequest = requests.filter(item => item.url === '/api/start').at(-1);
  check(JSON.parse(startRequest.options.body).voice === false && JSON.parse(startRequest.options.body).voice_scope === 'pace',
    '발표 시작 시 음성 꺼짐과 빠름·느림 범위를 함께 전달');
  const progress = {
    position_index: 0, units: [{keypoint_id: 'one'}, {keypoint_id: 'two'}],
    fraction: 0.5, missing_ids: [], next_text: '다음 설명', reliable: true,
    pace: 'fast', pace_reason: '계획 대비 빠른 진행이 유지되어 안내합니다.',
    measured_elapsed_sec: 30, processing_delay_sec: 4, plan_total_duration_sec: 60,
    baseline_units_per_min: 100, observed_units_per_min: 140, ratio: 1.4, estimated_total_sec: 43,
  };
  const withPace = script_progress => ({...base, audio_input_mode: 'manual',
    session: {...session, issues: [], script_progress}});
  render(withPace(progress));
  check(elements.get('script-pace-state').textContent === '계획보다 빠름' && elements.get('pace-ratio').textContent === '계획의 140%' && elements.get('pace-observed').textContent === '140글자/분',
    '빠름 상태와 계획 대비 속도를 화면에 명확히 표시');
  check(elements.get('pace-target').textContent === '01:00' && elements.get('pace-measured').textContent === '00:30' && elements.get('pace-estimated').textContent === '00:43',
    '목표 시간·실제 확인 발화 종료·안정화된 예상 전체 시간을 구분');
  check(elements.get('pace-timing-note').textContent.includes('4.0초') && elements.get('pace-timing-note').textContent.includes('계산에서 제외'),
    '모델 처리 지연을 속도 계산에서 제외한다고 표시');
  render(withPace({...progress, pace: 'slow', ratio: 0.6, observed_units_per_min: 60}));
  check(elements.get('script-pace-state').textContent === '계획보다 느림' && elements.get('pace-ratio').textContent === '계획의 60%',
    '느림 상태와 계획 대비 속도를 표시');
  render(withPace({...progress, pace: 'on_plan', ratio: 1, observed_units_per_min: 100}));
  check(elements.get('script-pace-state').textContent === '계획 내' && elements.get('pace-ratio').textContent === '계획의 100%',
    '계획 내 상태와 기준 속도를 표시');
  render(withPace({...progress, pace: 'waiting', pace_reason: '음성 인식과 내용 판단을 기다리고 있습니다.'}));
  check(elements.get('script-pace-state').textContent === '판단 보류' && elements.get('pace-reason').textContent.includes('기다리고') && elements.get('pace-estimated').textContent === '판단 보류',
    '최소 표본·안정화 대기 때 보류 이유를 표시하고 예상 시간을 확정하지 않음');
  render(withPace({...progress, reliable: false, pace_reason: '입력 손실이 있어 속도 판단을 보류합니다.'}));
  check(elements.get('script-pace-state').textContent === '판단 보류' && elements.get('pace-ratio').textContent === '판단 보류' && elements.get('pace-observed').textContent === '판단 보류' && elements.get('pace-reason').textContent.includes('입력 손실'),
    '근거가 불확실하면 이전 빠름 상태와 수치 대신 보류·원인을 표시');
  check(!requests.some(item => item.url === '/api/voice_test'), '검증 및 초기화 중 실제 안내 음성 재생을 요청하지 않음');
  check(fs.readFileSync('web/presentation/index.html', 'utf8').includes('기기의 소리가 꺼져 있으면 음성 안내가 들리지 않습니다.'),
    '음소거된 출력에서는 음성을 들을 수 없음을 안내');
  const voiceState = {...base, session, voice_available: true, voice_enabled: true, voice_scope: 'pace'};
  render({...voiceState, session: {...session, status: 'ended'}});
  check(elements.get('voice-test').disabled, '발표가 끝난 상태에서는 안내 음성 시험을 비활성화');
  render({...voiceState, session: {...session, status: 'stopping'}});
  check(elements.get('voice-test').disabled, '마지막 발화를 처리하는 동안에도 안내 음성 시험을 비활성화');
  render(voiceState);
  check(!elements.get('voice-test').disabled, '진행 중이며 음성 안내가 켜져 있을 때만 음성 시험 허용');
  render({...voiceState, voice_last_event: {type: 'voice_started', key: 'pace:slow:0',
    message: '계획보다 느리게 진행하고 있습니다. 남은 내용을 조금 더 빠르게 이어가 주세요.'}});
  check(elements.get('voice-status').textContent.includes('재생 중') && elements.get('voice-status').textContent.includes('계획보다 느리게'),
    '재생 중인 느림 안내의 실제 문구를 화면에 표시');
  render({...voiceState, voice_last_event: {type: 'voice_completed', key: 'pace:fast:0',
    message: '계획보다 빠르게 진행하고 있습니다. 조금 천천히 말씀해 주세요.'}});
  check(elements.get('voice-status').textContent.includes('재생 완료') && elements.get('voice-status').textContent.includes('계획보다 빠르게'),
    '완료된 빠름 안내의 실제 문구를 화면에 표시');
  render(withPace({...progress, evidence_age_sec: 35, reliable: false,
    pace: 'waiting', pace_reason: '최근 내용 확인 근거가 오래되어 기다리고 있습니다.'}));
  check(elements.get('pace-timing-note').textContent.includes('내용 확인 지연 4.0초') && !elements.get('pace-timing-note').textContent.includes('35'),
    '오래 쉬었어도 고정된 처리 지연을 근거 경과 시간과 혼동하지 않음');
  check(!requests.some(item => item.url === '/api/voice_test'), '음성 시험 버튼과 안내 문구 회귀 검사에서도 실제 재생을 요청하지 않음');
  const confirmedDeck = {...deck, slides: [{...deck.slides[0], keypoints: [
    {keypoint_id: 'first', text: '첫 문장의 설명', required: true},
    {keypoint_id: 'second', text: '둘째 문장의 설명', required: true},
  ]}]};
  const confirmedSession = {...session, deck: confirmedDeck, issues: [], judgment_status: 'evaluating',
    states: {first: {status: 'explained', reason: '첫 문장 확인', evidence_segment_ids: ['u1']},
      second: {status: 'unconfirmed', reason: '아직 확인되지 않았습니다.', evidence_segment_ids: []}},
    segments: [{segment_id: 'u1', text: '첫 문장의 설명', start_sec: 3.09, end_sec: 6.40,
      status: 'OK', endpoint_reason: 'silence', slide_ids: ['s1'], estimated_feedback_delay_ms: 334.01},
    {segment_id: 'u2', text: '둘째 문장의 설명', start_sec: 9.07, end_sec: 10.30,
      status: 'OK', endpoint_reason: 'silence', slide_ids: ['s1'], estimated_feedback_delay_ms: 354.4}],
    script_progress: {...progress, reliable: false, pace: 'waiting', pace_reason: '최근 발화 판단 대기'}};
  render({...base, deck: confirmedDeck, session: confirmedSession, audio_input_mode: 'mic', audio_status: 'recording'});
  check(elements.get('point-count').textContent === '1 / 2 확인' && elements.get('keypoints').children[0].children[2].textContent === '설명됨',
    '최신 발화를 판단 중이어도 먼저 확인된 문장의 체크와 확인 개수를 유지');
  check(elements.get('judgment-status').textContent.includes('최신 발화 판단 중') && elements.get('judgment-status').textContent.includes('앞서 확인한'),
    '새 발화 판단과 앞서 완료된 확인 상태를 구분');
  check(elements.get('transcripts').children[0].children[1].textContent === '설명 근거 확인' && elements.get('transcripts').children[1].children[1].textContent === '전사 수신',
    '앞선 발화의 설명 근거와 새 발화의 전사 수신을 구분하여 표시');
  check(elements.get('transcripts').children[0].children[0].textContent.includes('전사 약 0.33초'),
    '전사 지연은 모델 판단 지연과 별도로 표시');
  render({...base, deck: confirmedDeck, session: {...confirmedSession, judgment_status: 'waiting_for_silence'}, audio_input_mode: 'mic'});
  check(elements.get('point-count').textContent === '1 / 2 확인' && elements.get('judgment-status').textContent.includes('최신 문장 연결 중') && elements.get('script-progress').textContent.includes('설명 확인 50%'),
    '이어 말한 조각을 모을 때도 앞선 문장 확인과 대본 진행량을 유지');
  const preparingState = {...base, voice_available: true, start_preparing: true,
    coach_status: {status: 'preparing', message: '첫 판단 모델을 준비합니다.'}};
  render(preparingState);
  check(elements.get('start').disabled && elements.get('microphone-device').disabled && elements.get('microphone-test').disabled && elements.get('input-mode').disabled && elements.get('upload').disabled && elements.get('prepare-script').disabled && elements.get('voice-scope').disabled,
    '서버 모델 준비 중에는 시작 조건과 마이크·자료·음성 설정을 동결');
  check(!elements.get('coach-preparation').hidden && elements.get('coach-preparation').textContent.includes('아직 발표를 시작하지 마세요') && elements.get('session-status').textContent === '발표 시작 대기',
    '세션 생성 전 모델 준비 상태를 명확히 안내');
  render({...base, coach_status: {status: 'error', message: '로컬 모델 요청이 시간 초과했습니다.'}});
  check(!elements.get('coach-preparation').hidden && elements.get('coach-preparation').textContent.includes('시간 초과') && elements.get('audio-status').textContent === '모델 준비 실패' && !elements.get('start').disabled,
    '세션이 없어도 준비 실패 이유를 표시하고 다시 시작할 수 있게 함');
  render({...base, voice_available: true, voice_scope: 'pace'});
  deferredStart = {};
  const startPending = elements.get('start').listeners.click();
  check(elements.get('input-mode').disabled && !elements.get('coach-preparation').hidden,
    '발표 시작 클릭 즉시 준비 상태를 표시하고 조작을 동결');
  responseState = preparingState;
  const statesBefore = requests.filter(item => item.url === '/api/state').length;
  await vm.runInContext('poll()', context);
  check(requests.filter(item => item.url === '/api/state').length === statesBefore + 1 && elements.get('coach-preparation').textContent.includes('모델 준비 중'),
    '발표 시작 응답을 기다리는 동안에도 상태 조회를 유지');
  deferredState = {};
  const oldPoll = vm.runInContext('poll()', context);
  const startedState = {...base, session: {...session, issues: []}, audio_input_mode: 'mic',
    audio_status: 'recording', start_preparing: false, coach_status: {status: 'ready', message: '준비 완료'}};
  deferredStart.resolve({ok: true, json: async () => startedState});
  await startPending;
  deferredState.resolve({ok: true, json: async () => preparingState});
  await oldPoll;
  check(elements.get('coach-preparation').hidden && elements.get('audio-status').textContent.includes('마이크 입력 수신 중'),
    '늦게 도착한 준비 상태가 완료된 시작 응답과 새 세션을 덮어쓰지 않음');
  deferredStart = null;
  deferredState = null;
  render({...base, voice_available: true});
  select('0');
  const failedState = {...base, voice_available: true, start_preparing: false,
    coach_status: {status: 'error', message: '모델 준비 실패: 로컬 요청 시간 초과'}};
  render(failedState);
  check((elements.get('coach-preparation').textContent.match(/모델 준비 실패/g) || []).length === 1,
    '서버 오류에 접두어가 포함되어도 모델 준비 실패 문구를 중복하지 않음');
  render({...base, voice_available: true});
  deferredStart = {};
  const failedStart = elements.get('start').listeners.click();
  check(requestTimeouts.at(-1) === 75000, '발표 시작만 서버의 60초 모델 준비를 기다릴 수 있도록 75초 제한 사용');
  responseState = preparingState;
  await vm.runInContext('poll()', context);
  deferredStart.resolve({ok: false, json: async () => ({error: '모델 준비 실패: 로컬 요청 시간 초과'})});
  check(await failedStart === false, '모델 준비 요청 실패를 성공한 발표 시작으로 취급하지 않음');
  deferredStart = null;
  responseState = failedState;
  await vm.runInContext('poll()', context);
  check(!elements.get('start').disabled && !elements.get('input-mode').disabled && !elements.get('upload').disabled && !elements.get('prepare-script').disabled && !elements.get('microphone-device').disabled && !elements.get('voice-scope').disabled,
    '시작 실패 후 최신 상태를 받으면 준비 잠금을 해제하고 장치·자료 변경과 재시도 허용');
  await vm.runInContext("api('voice', {enabled:false, scope:'pace'})", context);
  check(requestTimeouts.at(-1) === 30000, '다른 변경 요청의 30초 제한은 유지');
  await vm.runInContext("api('state')", context);
  check(requestTimeouts.at(-1) === 8000, '상태 조회의 8초 제한은 유지');
  const timedUnits = [
    {keypoint_id: 'one', text: '대본 전문을 표에 반복하지 않습니다.', status: 'explained',
      planned_duration_sec: 20, completed_at_sec: 12.4, processing_delay_sec: 2.4,
      adjusted_completed_at_sec: 10, actual_duration_sec: 10, sentence_ratio: 2, sentence_pace: 'fast', timing_estimated: false},
    {keypoint_id: 'two', text: '둘째 문장입니다.', status: 'unconfirmed', planned_duration_sec: 40},
  ];
  const firstDisplay = {pace: 'fast', reliable: true, reason: '첫 문장의 확인 시점에서 계산했습니다.',
    latest_keypoint_id: 'one', planned_elapsed_sec: 20, measured_elapsed_sec: 10,
    ratio: 2, estimated_total_sec: 30, processing_delay_sec: 2.4, completed_at_sec: 12.4,
    observed_units_per_min: 200, pending: false, final: false};
  const immediateProgress = {...progress, pace: 'waiting', reliable: false, units: timedUnits, display_pace: firstDisplay};
  render(withPace(immediateProgress));
  check(elements.get('script-pace-state').textContent === '빠름' && elements.get('pace-measured').textContent === '10.0초' && elements.get('pace-ratio').textContent === '계획의 200%',
    '첫 문장 10초 확인 직후 voice 안정화 대기와 별도로 화면 속도를 표시');
  check(elements.get('pace-planned').textContent === '20.0초' && elements.get('pace-latest').textContent === '1번 · 빠름' && elements.get('pace-estimated').textContent === '00:30',
    '전체 누적 배정 시간과 최근 문장 속도 기준을 구분');
  const firstRow = elements.get('sentence-timing-rows').children[0];
  check(firstRow.children[1].textContent === '20.0초' && firstRow.children[2].textContent === '10.0초' && firstRow.children[3].children[0].textContent === '빠름' && firstRow.children[4].textContent === '2.4초',
    '문장별 배정·실제 소요·속도·제외 지연을 행으로 표시');
  check(elements.get('pace-timing-note').textContent.includes('12.4초 − 처리 지연 2.4초 = 보정 시각 10.0초'),
    '완료 표시 시각에서 실제 처리 지연을 뺀 보정 식을 표시');
  check(!JSON.stringify(elements.get('sentence-timing-rows').children).includes(timedUnits[0].text),
    '문장 시간 표에 대본 전문을 중복하지 않음');
  render(withPace({...immediateProgress, display_pace: {...firstDisplay, pending: true}}));
  check(elements.get('script-pace-state').textContent === '빠름' && elements.get('pace-ratio').textContent === '계획의 200%' && elements.get('pace-status-note').textContent.includes('마지막 확인의 수치'),
    '다음 발화 처리 중에도 마지막 확인의 속도와 수치를 유지');
  const endedTiming = withPace({...immediateProgress, display_pace: {...firstDisplay, final: true}});
  endedTiming.session.status = 'ended';
  render(endedTiming);
  check(elements.get('script-pace-state').textContent === '빠름' && elements.get('pace-ratio').textContent === '계획의 200%' && elements.get('pace-status-note').textContent.includes('최종 확인'),
    '종료 후에도 최종 확인 속도와 시간 수치를 유지');
  for (const delay of [2, 4]) {
    render(withPace({...immediateProgress, units: [{...timedUnits[0], completed_at_sec: 10 + delay, processing_delay_sec: delay}, timedUnits[1]],
      display_pace: {...firstDisplay, completed_at_sec: 10 + delay, processing_delay_sec: delay}}));
    check(elements.get('pace-measured').textContent === '10.0초' && elements.get('pace-timing-note').textContent.includes(`처리 지연 ${delay.toFixed(1)}초 = 보정 시각 10.0초`),
      `실제 ${delay}초 처리 지연을 적용하며 고정 지연을 가정하지 않음`);
  }
  render(withPace({...immediateProgress, display_pace: {...firstDisplay, reliable: false, pace: 'waiting', reason: '앞선 대본의 누락을 확인해야 합니다.'}}));
  check(elements.get('script-pace-state').textContent === '판단 보류' && elements.get('pace-measured').textContent === '판단 보류' && elements.get('pace-ratio').textContent === '판단 보류' && !elements.get('pace-timing-note').textContent.includes('12.4초'),
    '대본 사이 누락이 있으면 전체 속도와 보정 수치를 확정하지 않음');
  render(withPace({...immediateProgress, display_pace: {...firstDisplay, reliable: false, pace: 'waiting', final: true, reason: '입력 오류가 있어 판단을 보류합니다.'}}));
  check(elements.get('script-pace-state').textContent === '판단 보류' && elements.get('pace-estimated').textContent === '판단 보류',
    '오류가 있는 종료 결과는 과거 빠름·예상 시간으로 잘못 표시하지 않음');
  render(withPace({...immediateProgress, units: [{...timedUnits[0], status: 'uncertain'}, timedUnits[1]],
    display_pace: {...firstDisplay, reliable: false, pace: 'waiting', reason: '정정 응답을 확인해야 합니다.'}}));
  check(elements.get('sentence-timing-rows').children[0].children[2].textContent === '—' && elements.get('sentence-timing-rows').children[0].children[3].children[0].textContent === '판단 보류' && elements.get('sentence-timing-rows').children[0].children[4].textContent === '—',
    '정정·잘못된 응답 후 남아 있는 문장 시간 메타데이터를 완료 기록처럼 표시하지 않음');
  render(withPace({...immediateProgress, units: [{...timedUnits[0], timing_estimated: true}, timedUnits[1]]}));
  check(elements.get('pace-timing-note').textContent.includes('시각 추정') && elements.get('sentence-timing-rows').children[0].children[3].children[1].textContent === '시각 추정',
    '오래된 근거 대신 보정한 완료 시각은 추정임을 표시');
  render(withPace({...immediateProgress, display_pace: {...firstDisplay, reliable: false, pace: 'waiting', latest_keypoint_id: null, measured_elapsed_sec: null, ratio: null, reason: '첫 문장을 확인하고 있습니다.'}}));
  check(elements.get('script-pace-state').textContent === '첫 문장 확인 대기' && elements.get('pace-ratio').textContent === '판단 보류',
    '첫 확인 전에는 숫자를 확정하지 않고 첫 문장 대기를 표시');
  render(withPace({...immediateProgress, display_pace: {...firstDisplay, reliable: false, pace: 'waiting', latest_keypoint_id: null, measured_elapsed_sec: null, reason: '입력 오류가 있어 보류합니다.'}}));
  check(elements.get('script-pace-state').textContent === '판단 보류', '첫 문장 전 오류도 단순 대기와 구분');
  render(withPace({...immediateProgress, display_pace: {...firstDisplay, reliable: false, pace: 'waiting', latest_keypoint_id: null,
    measured_elapsed_sec: null, reason_code: 'uncertain_judgment', reason: '전사를 확인하고 있습니다.'}}));
  check(elements.get('script-pace-state').textContent === '판단 보류', '보류 상태 코드를 사용하여 문구 변화에도 오류를 첫 문장 대기로 오인하지 않음');
  render(withPace({...immediateProgress, display_pace: {...firstDisplay, reliable: false, pace: 'waiting', latest_keypoint_id: null,
    measured_elapsed_sec: null, reason_code: 'no_confirmed_sentence', reason: '아직 완료 구간을 받지 못했습니다.'}}));
  check(elements.get('script-pace-state').textContent === '첫 문장 확인 대기', '첫 문장 대기 상태 코드로 사용자 문구와 독립적으로 상태 표시');
  for (const [pace, label] of [['on_plan','적당함'], ['slow','느림']]) {
    render(withPace({...immediateProgress, display_pace: {...firstDisplay, pace}}));
    check(elements.get('script-pace-state').textContent === label, `즉시 화면 속도 ${label} 상태를 표시`);
  }
  check(elements.get('voice-enabled').checked === false && !requests.some(item => item.url === '/api/voice_test'),
    '즉시 속도 표시와 시간 표 검증 중에도 음성을 자동으로 켜거나 재생하지 않음');
  const slideUnits = [
    {keypoint_id:'one', text:'첫 슬라이드입니다.', slide_index:0, planned_duration_sec:30},
    {keypoint_id:'two', text:'실험 결과입니다.', slide_index:1, planned_duration_sec:30},
  ];
  const trackingDeck = {...deck, script_text:'첫 슬라이드입니다.\n<<슬라이드 넘김>>\n실험 결과입니다.', total_duration_sec:60,
    slides:[{slide_id:'script',title:'대본',target_duration_sec:60,keypoints:slideUnits}],
    script_plan:{units:slideUnits, slides:[
      {slide_id:'script-slide-1',title:'슬라이드 1',target_duration_sec:30,keypoint_ids:['one']},
      {slide_id:'script-slide-2',title:'슬라이드 2',target_duration_sec:30,keypoint_ids:['two']},
    ]}};
  const trackingState = {...base,deck:trackingDeck,session:{...session,deck:trackingDeck,issues:[],
    states:{one:{status:'unconfirmed',reason:'미확인'},two:{status:'uncertain',reason:'수치 확인 필요'}},
    script_progress:{units:slideUnits,fraction:0,missing_ids:[],
      tracking:{index:1,phase:'partial',slide_index:1,reason:'현재 문장을 찾았습니다.',recovery:false},
      slide_timing:{elapsed_sec:5,target_duration_sec:30,reliable:true,pace:'fast',schedule_delta_sec:-8,
        records:[{slide_index:0,target_duration_sec:30,actual_duration_sec:20,delta_sec:-10,reliable:true,estimated:true}]}}}};
  render(trackingState);
  check(elements.get('slide-count').textContent === 'SLIDE 02 / 02' && elements.get('keypoints').children.length === 1,
    '논리 슬라이드와 해당 슬라이드 문장을 표시');
  check(elements.get('script-progress').textContent.includes('현재 문장 2/2') && elements.get('script-progress').textContent.includes('내용 일치 확인 0%'),
    '발화 위치 이동과 내용 확인 비율을 분리');
  check(elements.get('legacy-pace-metrics').hidden && !elements.get('script-slide-timing').hidden && elements.get('sentence-timing').hidden,
    '새 대본 모드에서는 슬라이드 시간 기록을 표시');
  check(elements.get('slide-timing-rows').children[0].children[3].textContent.includes('10.0초 앞섬') && elements.get('slide-timing-rows').children[0].children[4].textContent === '발화로 추정',
    '직전 슬라이드 시간 차이와 경계 추정을 함께 표시');
  check(elements.get('keypoints').children[0].className === 'current-sentence' && elements.get('next').disabled && !elements.get('previous').disabled,
    '현재 문장 강조와 논리 슬라이드 탐색 버튼 상태');
  responseState = trackingState;
  await elements.get('keypoints').children[0].children[1].children.at(-1).listeners.click();
  check(requests.at(-1).url === '/api/resync' && JSON.parse(requests.at(-1).options.body).index === 1,
    '여기서 이어가기가 정확한 문장 위치를 서버에 전달');
  render({...trackingState, session:{...trackingState.session,status:'ended'}});
  check(elements.get('keypoints').children[0].children[1].children.at(-1).disabled,
    '종료된 발표에서는 위치 변경 비활성화');
  check(elements.get('script-slide-durations').disabled === false,
    '발표 종료 후 슬라이드 목표 시간을 다시 편집 가능');
  const liveState = {...trackingState, audio_input_mode:'manual', audio_status:'manual', session:{...trackingState.session,
    session_id:'live-feedback-test',status:'running',elapsed_sec:24,judgment_status:'ready',
    script_progress:{...trackingState.session.script_progress,
      tracking:{index:0,phase:'complete',slide_index:0,audio_end_sec:20,misses:0,reason:'위치 확인'},
      slide_timing:{elapsed_sec:24,target_duration_sec:30,planned_elapsed_sec:30,measured_elapsed_sec:20,
        ratio:1.5,reliable:true,pace:'fast',schedule_delta_sec:-40,records:[]}}}};
  const live = (sessionChanges = {}, trackingChanges = {}, timingChanges = {}, dataChanges = {}) => ({...liveState,...dataChanges,
    session:{...liveState.session,...sessionChanges,script_progress:{...liveState.session.script_progress,
      tracking:{...liveState.session.script_progress.tracking,...trackingChanges},
      slide_timing:{...liveState.session.script_progress.slide_timing,...timingChanges}}}});
  render(live());
  check(elements.get('feedback-state').textContent === '계획보다 빨라요' && elements.get('feedback-ratio').textContent === '150%' &&
    elements.get('feedback-delta').textContent.includes('10초 앞서'), '상단 피드백은 전체 누적 차이 대신 현재 슬라이드의 속도와 시간 차이를 표시');
  check(elements.get('feedback-remaining').textContent === '00:06' && elements.get('feedback-basis').textContent.includes('4.0초 전'),
    '계속 흐르는 슬라이드 시계와 발화 근거 시각을 구분');
  render(live({elapsed_sec:28,judgment_status:'evaluating'}, {}, {elapsed_sec:28}));
  check(elements.get('feedback-ratio').textContent === '150%' && elements.get('feedback-update').textContent.includes('직전 속도') &&
    elements.get('feedback-remaining').textContent === '00:02', '다음 발화 처리 중에도 직전 속도를 유지하고 남은 시간은 갱신');
  render(live({elapsed_sec:36}, {}, {elapsed_sec:36}));
  check(elements.get('feedback-ratio').textContent === '—' && elements.get('feedback-marker').hidden &&
    elements.get('feedback-delta').textContent.includes('최근 발화'), '15초 넘게 오래된 근거를 현재 속도로 안내하지 않음');
  render(live({elapsed_sec:33}, {audio_end_sec:30}, {elapsed_sec:33,measured_elapsed_sec:30,planned_elapsed_sec:18,ratio:.6,pace:'slow'}));
  check(elements.get('feedback-state').textContent === '계획보다 느려요' && elements.get('feedback-delta').textContent.includes('12초 뒤처') &&
    elements.get('feedback-clock-label').textContent.includes('초과') && elements.get('feedback-remaining').textContent === '00:03',
    '느림의 시간 차이와 슬라이드 목표 시간 초과를 구분');
  render(live({}, {}, {ratio:1,planned_elapsed_sec:20,pace:'on_plan'}));
  check(elements.get('feedback-state').textContent === '계획에 맞게 진행 중' && elements.get('feedback-marker').style.left === '50%',
    '계획 속도는 게이지 중앙에 표시');
  render(live({}, {misses:1}, {reliable:false,ratio:null}));
  check(elements.get('feedback-ratio').textContent === '—' && !elements.get('feedback-advice').textContent.includes('속도를 높여'),
    '위치를 놓치면 과거 속도에 따른 행동 안내를 지움');
  render(live({}, {phase:'manual',audio_end_sec:-1}, {reliable:false,ratio:null}));
  check(elements.get('feedback-marker').hidden && elements.get('feedback-state').textContent.includes('위치를 확인'),
    '수동 위치 변경 후 이전 속도를 재사용하지 않음');
  render(live({}, {}, {}, {audio_input_mode:'mic',audio_status:'error'}));
  check(elements.get('feedback-ratio').textContent === '—' && elements.get('feedback-state').textContent.includes('음성 입력'),
    '마이크 오류 중 남아 있는 모델 수치를 속도 안내로 사용하지 않음');
  render(live({}, {}, {ratio:3,planned_elapsed_sec:60}));
  check(elements.get('feedback-ratio').textContent === '300%' && elements.get('feedback-marker').style.left === '98%',
    '큰 속도 차이도 숫자는 유지하며 게이지 밖으로 넘치지 않음');
  render(live({status:'ended'}, {}, {reliable:false,ratio:null,elapsed_sec:50,
    records:[{slide_index:0,end_source:'session_end',target_duration_sec:30,actual_duration_sec:50,delta_sec:20,reliable:true}]}));
  check(elements.get('feedback-update').textContent.includes('발표 종료') && elements.get('feedback-ratio').textContent === '60%' &&
    elements.get('feedback-basis').textContent.includes('체류 시간'), '종료 후에는 마지막 슬라이드의 실제 종료 시간 기준으로 표시');
  render(live());
  vm.runInContext('renderLiveFeedback(current, true)', context);
  check(elements.get('feedback-ratio').textContent === '—' && elements.get('feedback-remaining').textContent === '—' &&
    elements.get('feedback-state').textContent.includes('서버 연결'), '연결 실패 시 고정된 숫자를 실시간 수치처럼 표시하지 않음');
  render(live());
  check(elements.get('feedback-ratio').textContent === '150%', '연결 복구 후 새 상태로 피드백 복원');
  render({...base,deck:trackingDeck});
  check(elements.get('feedback-state').textContent === '첫 문장을 기다려요' && elements.get('feedback-marker').hidden,
    '새 발표 준비 상태에 이전 세션의 속도가 남지 않음');
  console.log(`UI DOM 검증 ${passed}개 통과`);
})().catch(error => {console.error(error); process.exitCode = 1;});
