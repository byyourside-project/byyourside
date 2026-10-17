(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const ui = {
    video: $('preview'), result: $('result'), empty: $('empty-state'), countdown: $('countdown'),
    camera: $('camera-indicator'), stageHint: $('stage-hint'), stageStatus: $('stage-status'),
    progress: $('shot-progress'), capture: $('capture'), command: $('command'), voiceStatus: $('voice-status'),
    listen: $('listen'), media: $('media-area'), fourLive: $('four-live'), fourStrip: $('four-strip'),
  };
  const state = { layout: 'single', frame: 'plain', timer: 10, shootingView: 'strip', zoom: 1, panX: 0, panY: 0, busy: false, stream: null, frames: [], currentShot: 0 };
  const labels = {
    layout: { single: '한 장', four_cut: '네 컷' },
    frame: { plain: '기본', starlight: '별빛', party: '파티', buddy: '별곰이' },
    timer: { 3: '3초', 10: '10초', 15: '15초', 20: '20초' },
    shootingView: { strip: '네 칸에서', full: '한 컷씩 크게' },
  };
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function announce(message, stage = message) {
    ui.voiceStatus.textContent = message;
    ui.stageStatus.textContent = stage;
  }

  function renderFourSlots() {
    ui.fourStrip.className = `photo-strip frame-${state.frame}`;
    ui.fourStrip.replaceChildren();
    for (let i = 0; i < 4; i += 1) {
      const slot = document.createElement('div');
      slot.className = 'four-slot';
      slot.setAttribute('aria-label', `${i + 1}번째 사진 ${state.frames[i] ? '촬영 완료' : i === state.currentShot ? '미리보기' : '대기 중'}`);
      if (state.frames[i]) {
        const photo = document.createElement('img');
        photo.src = state.frames[i];
        photo.alt = '';
        slot.append(photo);
      } else if (i === state.currentShot) {
        slot.classList.add('active');
        const live = document.createElement('canvas');
        live.width = 900;
        live.height = 600;
        slot.append(live);
      } else {
        const label = document.createElement('span');
        label.textContent = `${String(i + 1).padStart(2, '0')} / 04`;
        slot.append(label);
      }
      ui.fourStrip.append(slot);
    }
  }

  function updateSettings() {
    $('layout-value').textContent = labels.layout[state.layout];
    $('frame-value').textContent = labels.frame[state.frame];
    $('timer-value').textContent = labels.timer[state.timer];
    $('shooting-view-value').textContent = labels.shootingView[state.shootingView];
    $('zoom-value').textContent = `${state.zoom.toFixed(2)}배`;
    const shift = (state.zoom - 1) * 50;
    ui.video.style.transform = `translate(${-state.panX * shift}%, ${-state.panY * shift}%) scaleX(-1) scale(${state.zoom})`;
    $('shooting-view-group').hidden = state.layout !== 'four_cut';
    $('setting-count').textContent = state.layout === 'four_cut' ? '01 — 04' : '01 — 03';
    $('preview-decor').className = `preview-decor frame-${state.frame}`;
    ui.media.classList.toggle('four-mode', state.layout === 'four_cut');
    const shootingFull = state.layout === 'four_cut' && state.busy && state.shootingView === 'full';
    ui.media.classList.toggle('shooting-full', shootingFull);
    ui.fourLive.hidden = state.layout !== 'four_cut' || shootingFull || !state.stream || !ui.result.hidden;
    renderFourSlots();
    document.querySelectorAll('[data-setting]').forEach((button) => {
      const active = String(state[button.dataset.setting]) === button.dataset.value;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
      button.disabled = state.busy;
    });
    document.querySelectorAll('[data-view-action]').forEach((button) => {
      const action = button.dataset.viewAction;
      const blocked = action === 'zoomIn' ? state.zoom >= 2
        : action === 'zoomOut' ? state.zoom <= 1
        : action === 'reset' ? state.zoom === 1 && state.panX === 0 && state.panY === 0
        : state.zoom === 1 || (action === 'left' && state.panX <= -1)
          || (action === 'right' && state.panX >= 1)
          || (action === 'up' && state.panY <= -1)
          || (action === 'down' && state.panY >= 1);
      button.disabled = state.busy || blocked;
    });
    ui.progress.textContent = state.layout === 'four_cut' ? `${String(Math.min(state.currentShot + 1, 4)).padStart(2, '0')} / 04` : '01 / 01';
  }

  function adjustView(action) {
    if (state.busy) return;
    if (action === 'zoomIn') state.zoom = Math.min(2, state.zoom + .25);
    else if (action === 'zoomOut') state.zoom = Math.max(1, state.zoom - .25);
    else if (action === 'reset') { state.zoom = 1; state.panX = 0; state.panY = 0; }
    else if (state.zoom === 1) return announce('화면을 먼저 확대해 주세요.');
    else if (action === 'left') state.panX = Math.max(-1, state.panX - .25);
    else if (action === 'right') state.panX = Math.min(1, state.panX + .25);
    else if (action === 'up') state.panY = Math.max(-1, state.panY - .25);
    else if (action === 'down') state.panY = Math.min(1, state.panY + .25);
    else return;
    if (state.zoom === 1) { state.panX = 0; state.panY = 0; }
    state.frames = [];
    state.currentShot = 0;
    ui.result.hidden = true;
    updateSettings();
    const message = action === 'reset' ? '화면 구도를 초기화했어요.'
      : action.startsWith('zoom') ? `화면 배율을 ${state.zoom.toFixed(2)}배로 설정했어요.`
      : `보이는 위치를 ${ { left: '왼쪽', right: '오른쪽', up: '위', down: '아래' }[action] }으로 옮겼어요.`;
    announce(message, message);
  }

  function setSetting(setting, value) {
    if (state.busy) return;
    if (setting === 'shootingView' && state.layout !== 'four_cut') {
      announce('네 컷 배치를 선택한 뒤 촬영 방법을 정해 주세요.');
      return;
    }
    const next = setting === 'timer' ? Number(value) : value;
    if (!Object.prototype.hasOwnProperty.call(labels[setting] || {}, String(next))) return;
    if (state[setting] !== next && (setting === 'layout' || setting === 'frame' || setting === 'shootingView')) {
      state.frames = [];
      state.currentShot = 0;
      ui.result.hidden = true;
    }
    state[setting] = next;
    updateSettings();
    const suffix = { layout: ' 배치로', frame: ' 프레임으로', timer: ' 타이머로', shootingView: ' 촬영으로' }[setting];
    announce(`${labels[setting][next]}${suffix} 설정했어요.`, `${labels[setting][next]} 설정 완료`);
  }

  async function connectCamera() {
    if (!navigator.mediaDevices?.getUserMedia) {
      ui.empty.querySelector('strong').textContent = '이 브라우저에서는 카메라를 사용할 수 없어요';
      announce('카메라를 지원하는 브라우저에서 로컬 주소로 열어 주세요.');
      return;
    }
    try {
      const next = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 960 } }, audio: false });
      state.stream?.getTracks().forEach((track) => track.stop());
      state.stream = next;
      ui.video.srcObject = next;
      await ui.video.play();
      ui.empty.hidden = true;
      ui.camera.classList.add('ready');
      ui.camera.lastElementChild.textContent = '카메라 연결됨';
      updateSettings();
      ui.stageHint.textContent = '정면을 바라봐 주세요';
      announce('카메라가 준비됐어요. 설정을 고르고 사진을 찍어 보세요.', '촬영 준비 완료');
    } catch (error) {
      ui.empty.hidden = false;
      ui.empty.querySelector('strong').textContent = '카메라를 연결할 수 없어요';
      ui.empty.querySelector('span').textContent = error.name === 'NotAllowedError' ? '브라우저에서 카메라 사용을 허용해 주세요.' : '카메라 연결을 확인한 뒤 다시 시도해 주세요.';
      ui.camera.lastElementChild.textContent = '카메라 연결 필요';
      announce('카메라 연결을 확인해 주세요.', '카메라 연결 필요');
    }
  }

  function drawVideoFrame(canvas) {
    const video = ui.video;
    if (!video.videoWidth || !video.videoHeight) return;
    const ctx = canvas.getContext('2d');
    const sourceRatio = video.videoWidth / video.videoHeight;
    const targetRatio = canvas.width / canvas.height;
    const baseWidth = sourceRatio > targetRatio ? video.videoHeight * targetRatio : video.videoWidth;
    const baseHeight = sourceRatio > targetRatio ? video.videoHeight : video.videoWidth / targetRatio;
    const sourceWidth = baseWidth / state.zoom;
    const sourceHeight = baseHeight / state.zoom;
    const sourceX = (video.videoWidth - sourceWidth) / 2 - state.panX * (baseWidth - sourceWidth) / 2;
    const sourceY = (video.videoHeight - sourceHeight) / 2 + state.panY * (baseHeight - sourceHeight) / 2;
    ctx.save();
    ctx.translate(canvas.width, 0);
    ctx.scale(-1, 1);
    ctx.drawImage(video, sourceX, sourceY, sourceWidth, sourceHeight, 0, 0, canvas.width, canvas.height);
    ctx.restore();
  }

  function snap() {
    const canvas = document.createElement('canvas');
    canvas.width = 900;
    canvas.height = 600;
    drawVideoFrame(canvas);
    return canvas.toDataURL('image/jpeg', .91);
  }

  function drawLiveSlot() {
    if (!ui.fourLive.hidden && state.stream) {
      const canvas = ui.fourStrip.querySelector('.four-slot.active canvas');
      if (canvas) drawVideoFrame(canvas);
    }
    requestAnimationFrame(drawLiveSlot);
  }

  async function count(seconds, label) {
    const countInSlot = state.layout === 'four_cut' && state.shootingView === 'strip';
    ui.countdown.classList.toggle('four-count', countInSlot);
    if (countInSlot) {
      const slot = ui.fourStrip.querySelector('.four-slot.active');
      const area = ui.media.getBoundingClientRect();
      const target = slot?.getBoundingClientRect();
      if (target) {
        ui.countdown.style.left = `${target.left - area.left}px`;
        ui.countdown.style.top = `${target.top - area.top}px`;
        ui.countdown.style.width = `${target.width}px`;
        ui.countdown.style.height = `${target.height}px`;
      }
    } else {
      ui.countdown.removeAttribute('style');
    }
    for (let remaining = seconds; remaining > 0; remaining -= 1) {
      ui.countdown.hidden = false;
      ui.countdown.textContent = String(remaining);
      ui.stageStatus.textContent = `${label} · ${remaining}초`;
      await wait(1000);
    }
    ui.countdown.hidden = true;
    ui.countdown.removeAttribute('style');
  }

  async function capture() {
    if (state.busy) return;
    if (!state.stream || ui.video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
      announce('카메라가 연결된 뒤 촬영할 수 있어요.', '카메라 연결 필요');
      return;
    }
    state.busy = true;
    ui.capture.disabled = true;
    ui.result.hidden = true;
    ui.result.replaceChildren();
    state.frames = [];
    state.currentShot = 0;
    updateSettings();
    const total = state.layout === 'four_cut' ? 4 : 1;
    const frames = [];
    try {
      for (let i = 0; i < total; i += 1) {
        ui.stageHint.textContent = total === 4 ? `${i + 1}번째 컷 촬영` : '한 장 촬영';
        await count(state.timer, total === 4 ? `${i + 1}번째 컷까지` : '촬영까지');
        frames.push(snap());
        state.frames = frames;
        state.currentShot = i + 1;
        if (total === 4) renderFourSlots();
        ui.progress.textContent = `${String(i + 1).padStart(2, '0')} / ${String(total).padStart(2, '0')}`;
        ui.stageStatus.textContent = `${i + 1}번째 사진 촬영 완료`;
        await wait(200);
      }
      ui.result.classList.toggle('four', total === 4);
      const strip = document.createElement('div');
      strip.className = `photo-strip frame-${state.frame}`;
      frames.forEach((src, i) => {
        const img = document.createElement('img');
        img.src = src;
        img.alt = `${i + 1}번째 촬영 사진`;
        strip.append(img);
      });
      ui.result.append(strip);
      ui.result.hidden = false;
      ui.stageHint.textContent = '촬영 결과';
      announce('촬영이 끝났어요. 다시 촬영하려면 촬영 버튼을 눌러 주세요.', '촬영 완료');
    } catch (error) {
      console.error('사진 촬영 실패', error);
      announce('촬영 중 문제가 생겼어요. 카메라를 확인해 주세요.', '촬영 실패');
    } finally {
      ui.countdown.hidden = true;
      state.busy = false;
      ui.capture.disabled = false;
      updateSettings();
    }
  }

  function interpret(text) {
    const phrase = text.trim().replace(/[.!?]/g, '').replace(/\s+/g, ' ');
    if (!phrase) return { error: '명령을 입력해 주세요.' };
    const viewActions = [];
    if (/(화면|카메라|구도|배율|줌).*(확대|줌인)|^(확대|줌인)/.test(phrase)) viewActions.push('zoomIn');
    if (/(화면|카메라|구도|배율|줌).*(축소|줌아웃)|^(축소|줌아웃)/.test(phrase)) viewActions.push('zoomOut');
    if (/(구도|배율|화면).*(초기화|원래대로)/.test(phrase)) viewActions.push('reset');
    if (/(옮겨|이동|움직|맞춰)/.test(phrase)) {
      if (/왼쪽/.test(phrase)) viewActions.push('left');
      if (/오른쪽/.test(phrase)) viewActions.push('right');
      if (/(위쪽|위로)/.test(phrase)) viewActions.push('up');
      if (/(아래쪽|아래로)/.test(phrase)) viewActions.push('down');
    }
    if (viewActions.length) {
      if (viewActions.length !== 1 || /(프레임|테두리|배치|타이머|촬영|찍어|셔터|네 컷|한 컷)/.test(phrase)) return { error: '한 번에 한 가지씩 말해 주세요.' };
      return { viewAction: viewActions[0] };
    }
    if (/(촬영 방법|촬영 방식|네 칸에서|네 칸 안|한 컷씩|풀화면|큰 화면)/.test(phrase)) {
      if (/(배치|타이머|프레임|테두리)/.test(phrase)) return { error: '한 번에 한 가지씩 말해 주세요.' };
      if (/(한 컷씩|풀화면|큰 화면)/.test(phrase)) return { setting: 'shootingView', value: 'full' };
      if (/(네 칸에서|네 칸 안)/.test(phrase)) return { setting: 'shootingView', value: 'strip' };
    }
    const shooting = /(찍어|찍을|찍고|찍게|촬영해|촬영 시작|셔터.*눌러)/.test(phrase);
    const layout = /(배치|모드|한 장|한 컷|네 컷|네 칸|네 장)/.test(phrase);
    const frame = /(프레임|테두리|장식|별빛|파티|별곰이|곰돌이|기본)/.test(phrase);
    const timer = /(타이머|대기 시간|카운트다운)/.test(phrase);
    if (/(다시 찍|재촬영|저장|보여|결과)/.test(phrase)) return { error: '이 명령은 이번 시연에서 지원하지 않아요.' };
    if ((shooting && (layout || frame || timer)) || [layout, frame, timer].filter(Boolean).length > 1) return { error: '한 번에 한 가지씩 말해 주세요. 설정 후 “사진 찍어줘”라고 말하면 돼요.' };
    if (shooting) return { action: 'capture' };
    if (layout) {
      if (/(네 컷|네 칸|네 장)/.test(phrase)) return { setting: 'layout', value: 'four_cut' };
      if (/(한 장|한 컷)/.test(phrase)) return { setting: 'layout', value: 'single' };
    }
    if (frame) {
      if (/(별곰이|곰돌이)/.test(phrase)) return { setting: 'frame', value: 'buddy' };
      if (/(별빛|별 장식|별 테두리)/.test(phrase)) return { setting: 'frame', value: 'starlight' };
      if (/(파티|축하|색종이)/.test(phrase)) return { setting: 'frame', value: 'party' };
      if (/(기본|장식.*없|테두리.*없)/.test(phrase)) return { setting: 'frame', value: 'plain' };
    }
    if (timer) {
      if (/(삼 초|세 초|3\s*초)/.test(phrase)) return { setting: 'timer', value: '3' };
      if (/(십오 초|열다섯 초|15초)/.test(phrase)) return { setting: 'timer', value: '15' };
      if (/(이십 초|스무 초|20초)/.test(phrase)) return { setting: 'timer', value: '20' };
      if (/(십 초|열 초|10초)/.test(phrase)) return { setting: 'timer', value: '10' };
    }
    return { error: '명령을 이해하지 못했어요. 화면 아래 추천 명령을 참고해 주세요.' };
  }

  function runCommand(phrase) {
    if (state.busy) return announce('촬영이 끝난 뒤 다시 말해 주세요.');
    const command = interpret(phrase);
    if (command.error) return announce(command.error);
    if (command.action === 'capture') return capture();
    if (command.viewAction) return adjustView(command.viewAction);
    setSetting(command.setting, command.value);
  }

  document.querySelectorAll('[data-setting]').forEach((button) => button.addEventListener('click', () => setSetting(button.dataset.setting, button.dataset.value)));
  document.querySelectorAll('[data-view-action]').forEach((button) => button.addEventListener('click', () => adjustView(button.dataset.viewAction)));
  ui.capture.addEventListener('click', capture);
  $('retry-camera').addEventListener('click', connectCamera);
  $('fullscreen').addEventListener('click', async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch { announce('이 브라우저에서는 전체 화면 전환을 사용할 수 없어요.'); }
  });
  document.addEventListener('fullscreenchange', () => {
    $('fullscreen').textContent = document.fullscreenElement ? '전체 화면 닫기' : '전체 화면';
  });
  $('command-form').addEventListener('submit', (event) => {
    event.preventDefault();
    runCommand(ui.command.value);
    ui.command.value = '';
  });

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (SpeechRecognition) {
    const recognition = new SpeechRecognition();
    recognition.lang = 'ko-KR';
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognition.onresult = (event) => {
      const phrase = event.results[0][0].transcript;
      ui.command.value = phrase;
      runCommand(phrase);
    };
    recognition.onerror = (event) => announce(event.error === 'not-allowed' ? '마이크 사용을 허용해 주세요.' : '음성을 인식하지 못했어요. 다시 말하거나 문장을 입력해 주세요.');
    recognition.onend = () => ui.listen.classList.remove('listening');
    ui.listen.addEventListener('click', () => {
      if (state.busy) return;
      try { recognition.start(); ui.listen.classList.add('listening'); announce('듣고 있어요. 명령을 말씀해 주세요.'); }
      catch { announce('잠시 후 다시 말해 주세요.'); }
    });
  } else {
    ui.listen.disabled = true;
    ui.listen.title = '이 브라우저는 음성 인식을 지원하지 않습니다.';
    ui.voiceStatus.textContent = '이 브라우저에서는 문장 입력과 화면 버튼으로 시연할 수 있어요.';
  }

  function tickClock() { $('clock').textContent = new Intl.DateTimeFormat('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date()); }
  tickClock();
  setInterval(tickClock, 30_000);
  updateSettings();
  drawLiveSlot();
  connectCamera();
  window.addEventListener('pagehide', () => state.stream?.getTracks().forEach((track) => track.stop()));
})();
