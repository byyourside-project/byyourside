'use strict';

const $ = id => document.getElementById(id);
const TAB_NAMES = ['feedback', 'transcript', 'comparison'];
const VIDEO_EXTENSIONS = new Set(['.mp4', '.mov', '.webm', '.mkv']);
const AUDIO_EXTENSIONS = new Set(['.wav', '.m4a', '.mp3', '.flac', '.ogg', '.aac']);
let selectedFile = null;
let report = null;
let busy = false;

function time(value) {
  const seconds = Math.max(0, Number(value) || 0);
  return String(Math.floor(seconds / 60)).padStart(2, '0') + ':' +
    String(Math.floor(seconds % 60)).padStart(2, '0');
}

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function status(message, error = false) {
  $('status').textContent = message;
  $('status').className = 'status' + (error ? ' error' : '');
}

function setBusy(value) {
  busy = value;
  ['analyze', 'demo', 'save', 'recompare', 'audio-file', 'script-file', 'script', 'target', 'review-title']
    .forEach(id => $(id).disabled = value);
  document.querySelector('.results').setAttribute('aria-busy', String(value));
}

async function request(url, options = {}) {
  const response = await fetch(url, options);
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error('서버 응답을 읽지 못했어요. 실행 창이 열려 있는지 확인해 주세요.');
  }
  if (!response.ok) throw new Error(data.error || '요청을 처리하지 못했습니다.');
  return data;
}

const post = (url, data) => request(url, {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify(data),
});

function target() {
  const value = $('target').value.trim();
  if (!value) return null;
  const number = Number(value);
  if (!Number.isFinite(number) || number < 1 || number > 7200) {
    throw new Error('목표 시간은 1~7,200초로 입력해 주세요.');
  }
  return number;
}

function extension(file) {
  const dot = file.name.lastIndexOf('.');
  return dot < 0 ? '' : file.name.slice(dot).toLowerCase();
}

function validateMedia(file) {
  const suffix = extension(file);
  if (!VIDEO_EXTENSIONS.has(suffix) && !AUDIO_EXTENSIONS.has(suffix)) {
    throw new Error('MP4, MOV, WEBM, MKV 영상이나 WAV, M4A, MP3, FLAC, OGG, AAC 음성을 선택해 주세요.');
  }
  const maximum = VIDEO_EXTENSIONS.has(suffix) ? 500 : 80;
  if (file.size > maximum * 1024 * 1024) {
    throw new Error(maximum + 'MB 이하의 파일을 선택해 주세요.');
  }
  if (!file.size) throw new Error('비어 있는 파일입니다. 다른 파일을 선택해 주세요.');
}

function selectFile(file) {
  if (!file || busy) return;
  try {
    validateMedia(file);
    selectedFile = file;
    $('file-label').textContent = file.name;
    status('대본을 확인한 뒤 발표 분석하기를 눌러 주세요.');
  } catch (error) {
    selectedFile = null;
    $('audio-file').value = '';
    $('file-label').textContent = '연습 영상을 놓아주세요';
    status(error.message, true);
  }
}

$('audio-file').addEventListener('change', event => selectFile(event.target.files[0]));
['dragenter', 'dragover'].forEach(name => $('dropzone').addEventListener(name, event => {
  event.preventDefault();
  if (!busy) $('dropzone').classList.add('drag');
}));
['dragleave', 'drop'].forEach(name => $('dropzone').addEventListener(name, event => {
  event.preventDefault();
  $('dropzone').classList.remove('drag');
}));
$('dropzone').addEventListener('drop', event => selectFile(event.dataTransfer.files[0]));

$('script-file').addEventListener('change', async event => {
  const file = event.target.files[0];
  if (!file || busy) return;
  try {
    if (!['.txt', '.md', '.pdf', '.docx'].includes(extension(file))) {
      throw new Error('TXT, MD, PDF, DOCX 대본 파일을 선택해 주세요.');
    }
    if (file.size > 10 * 1024 * 1024) throw new Error('대본 파일은 10MB까지 불러올 수 있어요.');
    if ($('script').value.trim() &&
        !confirm('입력한 대본을 선택한 파일의 내용으로 바꿀까요?')) return;
    setBusy(true);
    status('대본을 불러오고 있어요.');
    const data = await request('/api/script', {
      method: 'POST',
      headers: {'Content-Type': 'application/octet-stream', 'X-Script-Extension': extension(file)},
      body: file,
    });
    if (typeof data.text !== 'string' || !data.text.trim()) {
      throw new Error('대본에서 글자를 찾지 못했어요. 글자를 직접 붙여넣어 주세요.');
    }
    if (data.text.length > 12000) {
      throw new Error('대본이 12,000자를 넘어요. 발표할 부분만 복사해 입력해 주세요.');
    }
    $('script').value = data.text;
    const warnings = Array.isArray(data.warnings) ? data.warnings.join(' ') : '';
    status('대본을 불러왔어요. 내용과 문장 구분이 맞는지 확인해 주세요.' + (warnings ? ' ' + warnings : ''));
  } catch (error) {
    status(error.message, true);
  } finally {
    setBusy(false);
    $('script-file').value = '';
  }
});

$('analyze').addEventListener('click', async () => {
  if (!selectedFile) return status('먼저 연습 영상이나 녹음 파일을 선택해 주세요.', true);
  try {
    validateMedia(selectedFile);
    const options = {script: $('script').value, target_seconds: target()};
    setBusy(true);
    status('발표 파일을 준비하고 있어요. 큰 영상은 조금 더 걸릴 수 있어요.');
    const upload = await request('/api/upload', {
      method: 'POST',
      headers: {'Content-Type': 'application/octet-stream', 'X-Audio-Extension': extension(selectedFile)},
      body: selectedFile,
    });
    await post('/api/analyze', {...options, id: upload.id});
    const messages = {
      preparing: '발표 파일을 준비하고 있어요.',
      extracting: '영상과 음성을 준비하고 있어요.',
      analyzing_audio: '발표 내용을 듣고, 대본과 말의 속도를 확인하고 있어요.',
      analyzing_video: '카메라 방향과 시선을 확인하고 있어요.',
    };
    for (let count = 0; count < 1260; count++) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      const job = await request('/api/jobs/' + upload.id);
      if (job.status === 'error') throw new Error(job.error || '분석을 완료하지 못했어요.');
      if (job.status === 'done') {
        render(job.report);
        showTab('feedback');
        loadHistory();
        status('분석이 끝났어요. 확인할 구간을 누르고 발표를 다시 살펴보세요.');
        return;
      }
      status(messages[job.stage] || messages[job.status] || job.message || '발표를 분석하고 있어요. 잠시 기다려 주세요.');
    }
    throw new Error('아직 분석 결과를 받지 못했어요. 잠시 후 지난 연습 목록을 확인해 주세요.');
  } catch (error) {
    status(error.message, true);
  } finally {
    setBusy(false);
  }
});

$('demo').addEventListener('click', async () => {
  try {
    setBusy(true);
    status('첫 녹음을 불러오고 있어요.');
    const data = await post('/api/demo', {});
    $('script').value = data.script || '';
    $('target').value = '';
    render(data);
    showTab('feedback');
    loadHistory();
    status('실제 첫 녹음의 자동 전사 결과입니다. 다시 듣고 수정할 수 있어요.');
  } catch (error) {
    status(error.message, true);
  } finally {
    setBusy(false);
  }
});

function activePlayer() {
  return $('video-player').hidden ? $('player') : $('video-player');
}

function stopPlayers() {
  ['player', 'video-player'].forEach(id => {
    const player = $(id);
    player.pause();
    player.removeAttribute('src');
    player.load();
  });
}

function seek(start) {
  const player = activePlayer();
  const position = Math.max(0, Number(start) || 0);
  const play = () => {
    player.currentTime = position;
    player.play().catch(() => status('재생 버튼을 눌러 발표를 확인해 주세요.'));
  };
  if (player.readyState >= 1) play();
  else player.addEventListener('loadedmetadata', play, {once: true});
}

function seekButton(start, end, label) {
  const title = label || ('▷ ' + time(start) + (Number.isFinite(end) ? ' — ' + time(end) : '부터 재생'));
  const button = el('button', title, 'seek');
  button.type = 'button';
  button.onclick = () => seek(start);
  return button;
}

function renderVerdicts(data) {
  const pace = data.pace || {
    label: '구간별 수치 확인',
    summary: '기존 기록입니다. 말한 구간의 수치와 전사를 함께 살펴보세요.',
    status: 'reference_only',
  };
  const gaze = data.gaze || {
    label: '시선 분석 없음',
    summary: data.media?.kind === 'video' ? '이 기록에는 시선 분석 결과가 없어요.' : '음성 파일에는 시선 정보가 없어요. 영상으로 연습하면 함께 확인할 수 있어요.',
    status: 'unavailable',
  };
  const cards = [
    ['말의 속도', pace, (pace.status === 'needs_review')],
    ['카메라 방향·시선 추정', gaze, gaze.status === 'complete' && (gaze.events || []).length > 0],
  ];
  $('verdicts').replaceChildren(...cards.map(([title, item, needsReview]) => {
    const observed = item.status === 'complete' && !needsReview;
    const card = el('article', undefined, 'verdict' + (needsReview ? ' attention' : observed ? ' observed' : ''));
    card.append(el('span', title, 'verdict-category'), el('h2', item.label || '판단 보류'), el('p', item.summary || '분석할 정보를 충분히 얻지 못했어요.'));
    return card;
  }));

  const reference = data.pace?.reference;
  const referenceText = reference
    ? [reference.label, reference.details].filter(value => typeof value === 'string').join(' · ')
    : '한글 글자 수와 감지한 발화 시간을 기준으로 추정합니다. 실제 발음한 음절 수와 다를 수 있어요.';
  $('pace-description').textContent = referenceText;
  const plannedTime = reference?.target;
  $('pace-target').hidden = !plannedTime;
  $('pace-target').textContent = plannedTime
    ? [plannedTime.label, plannedTime.detail].filter(value => typeof value === 'string').join(' · ')
    : '';
  $('pace-next-step').textContent = data.pace?.next_step || '';
  renderIntervals($('pace-intervals'), data.pace?.intervals || [],
    data.pace?.status === 'needs_review' ? '판단 이유는 위 설명을 확인해 주세요.' : '따로 표시할 속도 구간이 없어요. 분석 기준과 전사를 함께 확인해 주세요.');

  $('gaze-description').textContent = gaze.summary || '카메라를 기준으로 얼굴 방향과 시선을 추정합니다.';
  const measurements = [];
  if (Number.isFinite(gaze.coverage_ratio)) {
    measurements.push(['판단할 수 있었던 비율', Math.round(gaze.coverage_ratio * 100) + '%', '전체 분석 구간 기준']);
  }
  if (Number.isFinite(gaze.camera_facing_ratio)) {
    measurements.push(['카메라 쪽으로 추정', Math.round(gaze.camera_facing_ratio * 100) + '%', '판단할 수 있었던 구간 중']);
  }
  if (Number.isFinite(gaze.unknown_seconds)) {
    measurements.push(['판단하기 어려운 시간', gaze.unknown_seconds.toFixed(1) + '초', '시선 이탈로 계산하지 않아요']);
  }
  if (Number.isFinite(gaze.longest_away_seconds)) {
    measurements.push(['가장 긴 시선 이탈 추정', gaze.longest_away_seconds.toFixed(1) + '초', '영상으로 다시 확인해 주세요']);
  }
  $('gaze-measurements').replaceChildren(...measurements.map(([label, value, detail]) => {
    const item = el('div', undefined, 'measurement');
    item.append(el('span', label), el('strong', value), el('small', detail));
    return item;
  }));

  const gazeEvents = Array.isArray(gaze.events) ? gaze.events : [];
  const unknowns = (gaze.intervals || []).filter(item => item.status === 'unknown' && item.end - item.start >= 2);
  const away = gazeEvents.length ? gazeEvents : (gaze.intervals || []).filter(item => item.status === 'away');
  const intervals = [...away, ...unknowns].sort((a, b) => a.start - b.start);
  renderIntervals($('gaze-intervals'), intervals,
    gaze.status === 'complete' ? '따로 표시할 시선 구간이 없어요. 영상에서 직접 확인할 수도 있어요.' : '시선 구간을 판단할 정보가 충분하지 않아요.');
  $('gaze-note').textContent = (gaze.limitations || []).filter(value => typeof value === 'string').join(' ') ||
    '정면 카메라 촬영을 기준으로 한 추정입니다. 실제 청중과의 눈맞춤이나 발표의 전달력을 확정하지 않습니다.';
}

function renderIntervals(container, items, emptyMessage) {
  const valid = items.filter(item => Number.isFinite(item.start) && Number.isFinite(item.end) && item.end > item.start);
  if (!valid.length) {
    container.replaceChildren(el('p', emptyMessage, 'feedback-empty'));
    return;
  }
  const visible = valid.slice(0, 80);
  container.replaceChildren(...visible.map(item => {
    const row = el('article', undefined, 'feedback-row');
    const heading = el('div', undefined, 'feedback-heading');
    heading.append(seekButton(item.start, item.end), el('strong', item.label || (item.status === 'unknown' ? '판단하기 어려운 구간' : '다시 확인할 구간')));
    row.append(heading);
    const description = item.detail || item.summary || item.reason;
    if (description) row.append(el('p', description));
    return row;
  }));
  if (valid.length > visible.length) {
    container.append(el('p', '먼저 ' + visible.length + '개 구간을 표시했어요. 전체 구간은 결과 저장에서 확인할 수 있어요.', 'feedback-empty'));
  }
}

function render(data) {
  report = data;
  $('empty').hidden = true;
  $('review').hidden = false;
  $('review-title').value = data.title || '발표 연습';
  $('download').href = data.download_url;
  const isVideo = data.media?.kind === 'video';
  const mediaUrl = data.media?.url || data.audio_url;
  const desiredPlayer = isVideo ? $('video-player') : $('player');
  if (desiredPlayer.getAttribute('src') !== mediaUrl || desiredPlayer.hidden) {
    stopPlayers();
    $('video-player').hidden = !isVideo;
    $('player').hidden = isVideo;
    if (mediaUrl) desiredPlayer.src = mediaUrl;
    $('play-time').textContent = '00:00';
  }
  $('player-label').textContent = isVideo ? '발표 다시 보기' : '발표 다시 듣기';

  renderVerdicts(data);
  const schedule = data.schedule || {};
  let scheduleValue = '미설정';
  let scheduleDetail = '목표 시간을 입력해 주세요';
  if (schedule.status === 'no_speech') {
    scheduleValue = '판단 보류';
    scheduleDetail = '말한 구간을 찾지 못했어요';
  } else if (Number.isFinite(schedule.difference_seconds)) {
    scheduleValue = Math.abs(schedule.difference_seconds).toFixed(1) + '초';
    scheduleDetail = schedule.difference_seconds > 0 ? '전체 목표 시간 초과' : '전체 목표 시간 이내 여유';
  }
  const summary = data.summary || {};
  const stats = [
    [isVideo ? '영상 길이' : '녹음 길이', time(data.duration), '처음부터 끝까지'],
    ['말한 구간', (summary.segment_count || 0) + '개', '자동 감지한 발화 구간'],
    ['추정 말하기 속도', Number.isFinite(summary.rate) ? Math.round(summary.rate) : '—', '한글 자/분 · 쉼 포함'],
    ['목표 시간 비교', scheduleValue, scheduleDetail],
  ];
  $('summary').replaceChildren(...stats.map(([label, value, detail]) => {
    const box = el('div', undefined, 'stat');
    box.append(el('span', label, 'stat-label'), el('strong', value), el('small', detail));
    return box;
  }));

  const segments = data.segments || [];
  $('timeline').replaceChildren(...segments.map(segment => {
    const button = el('button');
    button.type = 'button';
    button.style.left = (100 * segment.start / data.duration) + '%';
    button.style.width = (100 * (segment.end - segment.start) / data.duration) + '%';
    button.title = time(segment.start) + ' · ' + segment.text;
    button.setAttribute('aria-label', time(segment.start) + '부터 재생');
    button.dataset.segment = segment.id;
    button.onclick = () => seek(segment.start);
    return button;
  }));
  $('timeline').hidden = !segments.length;
  $('warnings').replaceChildren(...(data.warnings || []).map(message => el('p', message)));
  $('segments').replaceChildren();
  if (!segments.length) {
    $('segments').append(el('p', '말한 구간을 찾지 못했어요. 소리가 들어 있는 파일인지 재생해 확인해 주세요.', 'comparison-note'));
  }
  segments.forEach(segment => {
    if (Number.isFinite(segment.pause_before) && segment.pause_before >= 0.35) {
      $('segments').append(el('div', '약 ' + segment.pause_before.toFixed(1) + '초 간격', 'gap'));
    }
    const row = el('article', undefined, 'segment');
    row.dataset.segment = segment.id;
    const header = el('div', undefined, 'segment-header');
    const rate = Number.isFinite(segment.rate) ? Math.round(segment.rate) + ' 한글 자/분' : '속도 판단 보류';
    header.append(seekButton(segment.start, segment.end), el('span', rate, 'rate'));
    const text = el('textarea');
    text.value = segment.text;
    text.rows = 2;
    text.maxLength = 2000;
    text.dataset.segment = segment.id;
    text.setAttribute('aria-label', segment.id + '번 구간 전사 수정');
    row.append(header, text);
    (segment.notes || []).forEach(note => row.append(el('p', note, 'segment-note')));
    $('segments').append(row);
  });

  $('comparisons').replaceChildren();
  if (!(data.comparison || []).length) {
    $('comparisons').append(el('p', '대본을 입력하고 아래 버튼을 눌러 주세요.', 'comparison-note'));
  }
  (data.comparison || []).forEach(row => {
    const card = el('article', undefined, 'comparison-row');
    const labels = {matched: '비슷한 표현 확인', review: '표현 확인 필요', unconfirmed: '연결 구간 확인 필요'};
    const state = Object.hasOwn(labels, row.status) ? row.status : 'unconfirmed';
    card.append(el('span', labels[state], 'badge ' + state), el('p', row.text));
    if (Number.isFinite(row.start)) card.append(seekButton(row.start));
    if (state === 'unconfirmed') {
      card.append(el('small', '바꿔 말하기나 인식 오류일 수 있습니다. 실제로 빠뜨렸는지 직접 확인하세요.'));
    }
    (row.internal_pauses || []).forEach(pause => card.append(el('small',
      '문장에 연결된 구간 안에서 약 ' + pause.duration.toFixed(1) + '초 간격 감지 · 의도한 쉼인지 확인해 주세요.')));
    $('comparisons').append(card);
  });

  const methods = Object.values(data.method || {}).filter(value => typeof value === 'string');
  methods.push(...(data.gaze?.methods || []).filter(value => typeof value === 'string'));
  $('methods').replaceChildren(...methods.map(message => el('p', message)));
  markCurrent();
}

function collectEdits() {
  const edits = {};
  document.querySelectorAll('#segments textarea').forEach(node => {
    const segment = report.segments.find(item => item.id === Number(node.dataset.segment));
    if (segment && (segment.edited || node.value !== segment.asr_text)) edits[node.dataset.segment] = node.value;
  });
  return edits;
}

async function refresh() {
  if (!report || busy) return;
  try {
    const options = {script: $('script').value, target_seconds: target(), edits: collectEdits()};
    setBusy(true);
    const data = await post('/api/reviews/' + report.id, options);
    render(data);
    loadHistory();
    status('수정한 전사와 대본을 반영했어요. 시간 경계와 시선 분석은 그대로 유지됩니다.');
  } catch (error) {
    status(error.message, true);
  } finally {
    setBusy(false);
  }
}
$('save').onclick = refresh;
$('recompare').onclick = refresh;

function showTab(name) {
  TAB_NAMES.forEach(key => {
    const selected = key === name;
    $('tab-' + key).classList.toggle('selected', selected);
    $('tab-' + key).setAttribute('aria-selected', String(selected));
    $('tab-' + key).tabIndex = selected ? 0 : -1;
    $(key + '-panel').hidden = !selected;
  });
}
TAB_NAMES.forEach((key, index) => {
  $('tab-' + key).onclick = () => showTab(key);
  $('tab-' + key).addEventListener('keydown', event => {
    let next;
    if (event.key === 'ArrowLeft') next = (index + TAB_NAMES.length - 1) % TAB_NAMES.length;
    else if (event.key === 'ArrowRight') next = (index + 1) % TAB_NAMES.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = TAB_NAMES.length - 1;
    else return;
    event.preventDefault();
    showTab(TAB_NAMES[next]);
    $('tab-' + TAB_NAMES[next]).focus();
  });
});

['player', 'video-player'].forEach(id => {
  $(id).addEventListener('timeupdate', () => {
    if ($(id) !== activePlayer()) return;
    const now = $(id).currentTime;
    $('play-time').textContent = time(now);
    if (!report) return;
    document.querySelectorAll('.segment, .timeline button').forEach(node => {
      const segment = report.segments.find(item => item.id === Number(node.dataset.segment));
      node.classList.toggle(node.tagName === 'BUTTON' ? 'active' : 'playing',
        Boolean(segment && segment.start <= now && now < segment.end));
    });
  });
  $(id).addEventListener('error', () => {
    if ($(id).getAttribute('src')) status('재생 파일을 열지 못했어요. 파일이 남아 있는지 확인하고 다시 열어 주세요.', true);
  });
});

const dateLabel = iso => {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return (date.getMonth() + 1) + '/' + date.getDate() + ' ' +
    String(date.getHours()).padStart(2, '0') + ':' + String(date.getMinutes()).padStart(2, '0');
};

function markCurrent() {
  document.querySelectorAll('.history-item').forEach(item =>
    item.classList.toggle('current', Boolean(report && item.dataset.id === report.id)));
}

async function loadHistory() {
  try {
    const {reviews} = await request('/api/reviews');
    $('history-count').textContent = reviews.length ? reviews.length + '개' : '';
    $('history-empty').hidden = reviews.length > 0;
    $('history-list').replaceChildren(...reviews.map(item => {
      const li = el('li', undefined, 'history-item');
      li.dataset.id = item.id;
      const open = el('button', undefined, 'history-open');
      open.type = 'button';
      const rate = Number.isFinite(item.avg_rate) ? Math.round(item.avg_rate) + ' 자/분' : '—';
      open.append(el('strong', item.title), el('span', dateLabel(item.created_at) + ' · ' + time(item.duration) + ' · ' + rate));
      if (item.review_state === 'user_edited_transcript') open.append(el('em', '전사 수정함'));
      open.onclick = () => openReview(item.id);
      const remove = el('button', '×', 'history-delete');
      remove.type = 'button';
      remove.title = '기록 삭제';
      remove.setAttribute('aria-label', item.title + ' 기록 삭제');
      remove.onclick = () => deleteReview(item);
      li.append(open, remove);
      return li;
    }));
    markCurrent();
  } catch (error) {
    status(error.message, true);
  }
}

async function openReview(id) {
  if (busy) return;
  try {
    setBusy(true);
    const job = await request('/api/jobs/' + id);
    if (job.status !== 'done') throw new Error(job.error || '아직 분석 중인 기록입니다.');
    $('script').value = job.report.script || '';
    $('target').value = job.report.schedule?.target_seconds ?? '';
    render(job.report);
    showTab('feedback');
    status('저장된 연습 기록을 불러왔어요.');
    $('review').scrollIntoView({behavior: 'smooth', block: 'start'});
  } catch (error) {
    status(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function deleteReview(item) {
  if (busy || !confirm("'" + item.title + "' 기록을 삭제할까요?\n영상·음성 파일과 분석 결과가 이 PC에서 지워지며 되돌릴 수 없습니다.")) return;
  try {
    setBusy(true);
    if (report && report.id === item.id) stopPlayers();
    await request('/api/reviews/' + item.id, {method: 'DELETE'});
    if (report && report.id === item.id) {
      report = null;
      $('review').hidden = true;
      $('empty').hidden = false;
    }
    status('기록을 삭제했어요.');
  } catch (error) {
    if (report && report.id === item.id) render(report);
    status(error.message, true);
  } finally {
    setBusy(false);
    loadHistory();
  }
}

$('review-title').addEventListener('keydown', event => {
  if (event.key === 'Enter') {
    event.preventDefault();
    event.target.blur();
  }
});
$('review-title').addEventListener('change', async () => {
  if (!report || busy) return;
  const title = $('review-title').value.trim();
  if (!title) {
    $('review-title').value = report.title;
    return status('제목을 입력해 주세요.', true);
  }
  if (title === report.title) return;
  try {
    setBusy(true);
    report = await post('/api/reviews/' + report.id, {title});
    $('review-title').value = report.title;
    status('제목을 바꿨어요.');
    loadHistory();
  } catch (error) {
    $('review-title').value = report.title;
    status(error.message, true);
  } finally {
    setBusy(false);
  }
});

loadHistory();
request('/api/status').then(data => {
  $('demo').hidden = !data.demo_available;
  if (!data.models_ready) {
    status('새 파일을 분석할 준비가 필요해요. 실행 안내에 따라 분석 도구를 준비해 주세요.' +
      (data.demo_available ? ' 첫 녹음 예제는 바로 볼 수 있어요.' : ''));
  }
}).catch(error => status(error.message, true));
