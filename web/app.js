'use strict';
const $ = id => document.getElementById(id);
let selectedFile = null, report = null, busy = false;
const time = value => `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(Math.floor(value % 60)).padStart(2, '0')}`;
function el(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; }
function status(message, error = false) { $('status').textContent = message; $('status').className = `status${error ? ' error' : ''}`; }
function setBusy(value) { busy = value; ['analyze','demo','save','recompare'].forEach(id => $(id).disabled = value); }
async function request(url, options = {}) { const response = await fetch(url, options); const data = await response.json(); if (!response.ok) throw new Error(data.error || '요청을 처리하지 못했습니다.'); return data; }
const post = (url, data) => request(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
function target() { const value = $('target').value.trim(); if (!value) return null; const number = Number(value); if (!Number.isFinite(number) || number < 1 || number > 7200) throw new Error('목표 시간은 1~7,200초로 입력해 주세요.'); return number; }
function selectFile(file) { if (!file || busy) return; selectedFile = file; $('file-label').textContent = file.name; status('녹음 분석하기를 누르면 시작합니다.'); }
$('audio-file').addEventListener('change', e => selectFile(e.target.files[0]));
['dragenter','dragover'].forEach(name => $('dropzone').addEventListener(name, e => {e.preventDefault(); $('dropzone').classList.add('drag');}));
['dragleave','drop'].forEach(name => $('dropzone').addEventListener(name, e => {e.preventDefault(); $('dropzone').classList.remove('drag');}));
$('dropzone').addEventListener('drop', e => selectFile(e.dataTransfer.files[0]));
$('analyze').addEventListener('click', async () => {
  if (!selectedFile) return status('먼저 녹음 파일을 선택해 주세요.', true);
  if (selectedFile.size > 80 * 1024 * 1024) return status('80MB 이하의 파일을 선택해 주세요.', true);
  try {
    const options = {script:$('script').value, target_seconds:target()};
    setBusy(true); status('녹음 파일을 준비하고 있어요.');
    const suffix = '.' + selectedFile.name.split('.').pop().toLowerCase();
    const upload = await request('/api/upload', {method:'POST', headers:{'Content-Type':'application/octet-stream','X-Audio-Extension':suffix}, body:selectedFile});
    await post('/api/analyze', {...options, id:upload.id});
    for (let count = 0; count < 350; count++) {
      await new Promise(resolve => setTimeout(resolve, 900));
      const job = await request(`/api/jobs/${upload.id}`);
      if (job.status === 'error') throw new Error(job.error);
      if (job.status === 'done') {render(job.report); status('분석이 끝났어요. 구간을 누르고 다시 들어보세요.'); return;}
      status(job.message || '녹음을 분석하고 있어요.');
    }
    throw new Error('분석 상태를 확인하지 못했습니다. 실행 창을 확인해 주세요.');
  } catch (error) {status(error.message, true);} finally {setBusy(false);}
});
$('demo').addEventListener('click', async () => {try {setBusy(true); status('첫 녹음을 불러오고 있어요.'); const data = await post('/api/demo', {}); $('script').value = data.script; $('target').value = ''; render(data); status('실제 첫 녹음의 자동 전사 결과입니다. 다시 듣고 수정할 수 있어요.');} catch(error) {status(error.message,true);} finally {setBusy(false);}});
function seek(start) {const player = $('player'); player.currentTime = start; player.play().catch(() => status('재생 버튼을 눌러 음성을 들어주세요.'));}
function render(data) {
  report = data; $('empty').hidden = true; $('review').hidden = false;
  $('review-title').textContent = data.title; $('download').href = data.download_url;
  if ($('player').getAttribute('src') !== data.audio_url) $('player').src = data.audio_url;
  const schedule = data.schedule;
  let scheduleValue = '미설정', scheduleDetail = '목표 시간을 입력해 주세요';
  if (schedule.status === 'no_speech') {scheduleValue = '판단 보류'; scheduleDetail = '말한 구간을 찾지 못했어요';}
  else if (schedule.difference_seconds !== null) {scheduleValue = `${Math.abs(schedule.difference_seconds).toFixed(1)}초`; scheduleDetail = schedule.difference_seconds > 0 ? '전체 목표 시간 초과' : '전체 목표 시간 이내 여유';}
  const stats = [['녹음 길이',time(data.duration),'처음부터 끝까지'],['말한 구간',`${data.summary.segment_count}개`,'자동 감지한 발화 구간'],['추정 말하기 속도',data.summary.rate === null ? '—' : Math.round(data.summary.rate),'한글 자/분 · 쉼 포함'],['목표 시간 비교',scheduleValue,scheduleDetail]];
  $('summary').replaceChildren(...stats.map(([label,value,detail]) => {const box=el('div',undefined,'stat'); box.append(el('span',label,'stat-label'),el('strong',value),el('small',detail)); return box;}));
  $('timeline').replaceChildren(...data.segments.map(s => {const b=el('button'); b.style.left=`${100*s.start/data.duration}%`; b.style.width=`${100*(s.end-s.start)/data.duration}%`; b.title=`${time(s.start)} · ${s.text}`; b.setAttribute('aria-label',`${time(s.start)}부터 듣기`); b.dataset.segment=s.id; b.onclick=()=>seek(s.start); return b;}));
  $('warnings').replaceChildren(...data.warnings.map(message=>el('p',message)));
  $('segments').replaceChildren();
  data.segments.forEach(s => {
    if(s.pause_before !== null && s.pause_before >= .35) $('segments').append(el('div',`약 ${s.pause_before.toFixed(1)}초 간격`,'gap'));
    const row=el('article',undefined,'segment'); row.dataset.segment=s.id;
    const header=el('div',undefined,'segment-header'); const b=el('button',`▷ ${time(s.start)} — ${time(s.end)}`,'seek'); b.onclick=()=>seek(s.start);
    header.append(b,el('span',`${Math.round(s.rate)} 한글 자/분`,'rate'));
    const text=el('textarea'); text.value=s.text; text.rows=2; text.dataset.segment=s.id; text.setAttribute('aria-label',`${s.id}번 구간 전사 수정`);
    row.append(header,text); s.notes.forEach(note=>row.append(el('p',note,'segment-note'))); $('segments').append(row);
  });
  $('comparisons').replaceChildren();
  if (!data.comparison.length) $('comparisons').append(el('p','왼쪽에 대본을 입력하고 아래 버튼을 눌러 주세요.','comparison-note'));
  data.comparison.forEach(row => {
    const card=el('article',undefined,'comparison-row');
    const labels={matched:'비슷한 표현 확인',review:'표현 확인 필요',unconfirmed:'연결 구간 확인 필요'};
    card.append(el('span',labels[row.status],`badge ${row.status}`),el('p',row.text));
    if(row.start !== null) {const b=el('button',`▷ ${time(row.start)}부터 듣기`,'seek'); b.onclick=()=>seek(row.start); card.append(b);}
    if(row.status === 'unconfirmed') card.append(el('small','바꿔 말하기나 인식 오류일 수 있습니다. 실제로 빠뜨렸는지 직접 확인하세요.'));
    row.internal_pauses.forEach(p=>card.append(el('small',`문장에 연결된 구간 안에서 약 ${p.duration.toFixed(1)}초 간격 감지 · 의도한 쉼인지 들어보세요.`)));
    $('comparisons').append(card);
  });
  $('methods').replaceChildren(...Object.values(data.method).map(message=>el('p',message)));
}
function collectEdits() {const edits={}; document.querySelectorAll('#segments textarea').forEach(node=>{const s=report.segments.find(s=>s.id===Number(node.dataset.segment)); if(s.edited || node.value!==s.asr_text) edits[node.dataset.segment]=node.value;}); return edits;}
async function refresh() {if(!report)return; try {const options={script:$('script').value,target_seconds:target(),edits:collectEdits()}; setBusy(true); const data=await post(`/api/reviews/${report.id}`,options); render(data); status('수정한 전사와 대본을 반영했어요. 시간 경계는 자동 감지값입니다.');} catch(error){status(error.message,true);} finally{setBusy(false);}}
$('save').onclick=refresh; $('recompare').onclick=refresh;
function showTab(name) {['transcript','comparison'].forEach(key=>{const selected=key===name; $(`tab-${key}`).classList.toggle('selected',selected); $(`tab-${key}`).setAttribute('aria-selected',String(selected)); $(`${key}-panel`).hidden=!selected;});}
$('tab-transcript').onclick=()=>showTab('transcript'); $('tab-comparison').onclick=()=>showTab('comparison');
['transcript','comparison'].forEach(key=>$(`tab-${key}`).addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();const next=key==='transcript'?'comparison':'transcript';showTab(next);$(`tab-${next}`).focus();}}));
$('player').addEventListener('timeupdate',()=>{const now=$('player').currentTime; $('play-time').textContent=time(now); if(!report)return; document.querySelectorAll('.segment,.timeline button').forEach(node=>{const s=report.segments.find(s=>s.id===Number(node.dataset.segment)); node.classList.toggle(node.tagName==='BUTTON'?'active':'playing',Boolean(s&&s.start<=now&&now<s.end));});});
request('/api/status').then(data=>{$('demo').hidden=!data.demo_available; if(!data.models_ready)status('새 파일 분석에는 음성 모델 준비가 필요합니다. 첫 녹음 예제는 바로 볼 수 있어요.');}).catch(error=>status(error.message,true));
