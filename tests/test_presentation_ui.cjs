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
  console.log(`UI DOM 검증 ${passed}개 통과`);
})().catch(error => {console.error(error); process.exitCode = 1;});
