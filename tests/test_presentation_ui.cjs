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
const requests = [];
const context = vm.createContext({
  document, console, AbortController, Blob, URL,
  setTimeout() {return 1;}, clearTimeout() {},
  fetch: async (url, options) => {
    requests.push({url, options});
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
  console.log(`UI DOM 검증 ${passed}개 통과`);
})().catch(error => {console.error(error); process.exitCode = 1;});
