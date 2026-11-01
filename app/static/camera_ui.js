// Camera widgets shared by the dashboard grid and the single-camera page.
import { api, el } from './common.js';

const STALL_MS = 5000;
const PTZ_STEP_MS = 400;
const REASONS = { manual: 'ručno', continuous: 'stalno', schedule: 'po rasporedu', motion: 'pokret' };

export function renderControls(box, cam, replay) {
  box.replaceChildren(el('div', { class: 'text-body-secondary small' }, 'Učitavanje…'));
  const status = el('div', { class: 'small text-danger mt-1' });
  const restartKeys = new Set(cam.controls.filter(c => c.restarts_stream).map(c => c.key));

  const send = async (key, value, input) => {
    input.disabled = true; status.textContent = '';
    try {
      await api(`/api/cameras/${cam.slug}/state`, { method: 'POST', body: JSON.stringify({ key, value }) });
      // reconnect so the stream picks up the change; other viewers see it when they reconnect
      // the server reopens the camera source first (~1 s), then the player reconnects
      if (restartKeys.has(key)) setTimeout(replay, 2500);
    }
    catch (e) { status.textContent = e.message; await load(); }
    finally { input.disabled = !cam.can_control; }
  };

  const load = async () => {
    let state;
    try { state = await api(`/api/cameras/${cam.slug}/state`); }
    catch (e) { box.replaceChildren(el('div', { class: 'small text-danger' }, e.message)); return; }

    const rows = cam.controls.filter(c => c.key in state).map(c => {
      const id = `${cam.slug}-${c.key}`;
      if (c.kind === 'bool') {
        const input = el('input', { class: 'form-check-input', type: 'checkbox', role: 'switch', id });
        input.checked = !!state[c.key];
        input.disabled = !cam.can_control;
        input.addEventListener('change', () => send(c.key, input.checked, input));
        return el('div', { class: 'form-check form-switch' }, input, el('label', { class: 'form-check-label', for: id }, c.label));
      }
      const select = el('select', { class: 'form-select form-select-sm w-auto', id });
      for (const [v, label] of Object.entries(c.options)) {
        const o = el('option', { value: v }, label); o.selected = state[c.key] === v; select.append(o);
      }
      select.disabled = !cam.can_control;
      select.addEventListener('change', () => send(c.key, select.value, select));
      return el('div', { class: 'd-flex align-items-center gap-2 mb-1' }, el('label', { for: id, class: 'small' }, c.label), select);
    });

    const actions = cam.actions.map(a => el('button', {
      class: 'btn btn-sm btn-outline-warning me-2 mt-1',
      onclick: async ev => {
        ev.target.disabled = true; status.textContent = '';
        try { await api(`/api/cameras/${cam.slug}/actions/${a.key}`, { method: 'POST' }); }
        catch (e) { status.textContent = e.message; }
        finally { ev.target.disabled = false; }
      },
    }, a.label));

    box.replaceChildren(el('div', { class: 'row row-cols-1 row-cols-sm-2' }, ...rows.map(r => el('div', { class: 'col' }, r))),
      ...(cam.role_admin ? actions : []), status);
  };
  load();
}

// per-viewer quality choice; default HD. Storage can be unavailable (private mode), so never rely on it.
const qualityKey = slug => `quality:${slug}`;
function loadQuality(cam) {
  try { return cam.has_sd && localStorage.getItem(qualityKey(cam.slug)) === 'sd' ? 'sd' : 'hd'; } catch { return 'hd'; }
}
function saveQuality(cam, q) {
  try { localStorage.setItem(qualityKey(cam.slug), q); } catch {}
}

// Arrow pad over the video. Returns {start(dir), stop()} so a page can also drive it from the keyboard.
function addPtz(view, cam) {
  const msg = el('span', { class: 'ptz-msg badge text-bg-danger d-none' });
  const pad = el('div', { class: 'ptz' });
  const arrows = { up: '▲', left: '◀', right: '▶', down: '▼' };
  const buttons = {};
  let timer = null, active = null;

  const step = async dir => {
    try { await api(`/api/cameras/${cam.slug}/ptz/${dir}?quality=${cam.quality}`, { method: 'POST' }); msg.classList.add('d-none'); }
    catch (e) { msg.textContent = e.message; msg.classList.remove('d-none'); stop(); }
  };
  const start = dir => {
    if (active === dir) return;
    stop();
    active = dir;
    buttons[dir].classList.add('on');
    step(dir);
    timer = setInterval(() => step(dir), PTZ_STEP_MS);
  };
  const stop = () => {
    clearInterval(timer); timer = null;
    if (active) buttons[active].classList.remove('on');
    active = null;
  };

  for (const [dir, label] of Object.entries(arrows)) {
    const b = buttons[dir] = el('button', { class: dir, title: 'Okreni', 'aria-label': dir }, label);
    b.addEventListener('pointerdown', ev => { ev.preventDefault(); start(dir); });
    for (const ev of ['pointerup', 'pointerleave', 'pointercancel']) b.addEventListener(ev, stop);
    pad.append(b);
  }
  view.append(pad, msg);
  return { start, stop };
}

/**
 * Wires a camera into `root`, which holds elements with these classes (any may be missing):
 * .name .loc .view .dot .rec-badge .btn-rec .btn-quality .btn-fs .btn-controls .controls
 * With opts.controlsOpen the controls are rendered at once instead of behind the ⚙ button.
 */
export function mountCamera(root, cam, opts = {}) {
  const q = sel => root.querySelector(sel);
  if (q('.name')) q('.name').textContent = cam.name;
  if (q('.loc')) q('.loc').textContent = cam.location || '';

  const view = q('.view');
  let player;
  const play = () => {
    // a fresh element per quality switch: simplest way to drop the old connection cleanly
    player?.remove();
    player = document.createElement('video-stream');
    if (cam.talk) {
      // two-way audio: the browser's microphone goes to the camera's speaker over WebRTC only
      player.mode = 'webrtc';
      player.media = 'video,audio,microphone';
    } else {
      // mjpeg last: lets MJPEG cameras (ESP32-CAM) play without server-side transcoding
      player.mode = 'webrtc,mse,hls,mjpeg';
    }
    player.src = new URL(`/api/ws?src=${encodeURIComponent(cam.slug)}&quality=${cam.quality}`, location.href);
    view.prepend(player);
  };
  cam.quality = loadQuality(cam);
  play();
  const ptz = cam.ptz && cam.can_control ? addPtz(view, cam) : null;

  const qbtn = q('.btn-quality');
  if (qbtn) {
    if (!cam.has_sd) qbtn.remove();
    const showQuality = () => { qbtn.textContent = cam.quality.toUpperCase(); };
    showQuality();
    qbtn.onclick = () => {
      cam.quality = cam.quality === 'hd' ? 'sd' : 'hd';
      saveQuality(cam, cam.quality);
      showQuality();
      play();
    };
  }

  if (q('.btn-fs')) q('.btn-fs').onclick = () => (document.fullscreenElement ? document.exitFullscreen() : view.requestFullscreen());

  // recording: red badge while the recorder writes this camera, REC button toggles manual recording
  const badge = q('.rec-badge');
  const recBtn = q('.btn-rec');
  cam.renderRec = () => {
    const r = cam.rec || {};
    badge?.classList.toggle('d-none', !r.active);
    if (badge) badge.title = r.active ? `Snima se (${REASONS[r.reason] || r.reason})` : '';
    if (!recBtn) return;
    recBtn.textContent = r.manual ? '■ Stop' : 'REC';
    recBtn.classList.toggle('btn-danger', !!r.manual);
    recBtn.classList.toggle('btn-outline-danger', !r.manual);
    recBtn.title = r.manual ? `Zaustavi ručno snimanje (pokrenuo: ${r.manual_by})` : 'Pokreni ručno snimanje';
  };
  if (recBtn && !cam.can_control) recBtn.remove();
  else if (recBtn) {
    recBtn.onclick = async () => {
      recBtn.disabled = true;
      try {
        cam.rec = await api(`/api/recording/cameras/${cam.slug}/manual`, { method: 'POST', body: JSON.stringify({ active: !cam.rec?.manual }) });
        cam.renderRec();
      } catch (e) { recBtn.title = e.message; }
      finally { recBtn.disabled = false; }
    };
  }
  cam.renderRec();

  const controls = q('.controls');
  const btn = q('.btn-controls');
  if (controls && opts.controlsOpen) {
    if (cam.controls.length) renderControls(controls, cam, play);
    else controls.replaceChildren(el('div', { class: 'small text-body-secondary' }, 'Ova kamera nema kontrole.'));
  } else if (btn) {
    if (!cam.controls.length) btn.remove();
    btn.onclick = () => {
      const show = controls.classList.toggle('d-none') === false;
      if (show) renderControls(controls, cam, play);
    };
  }

  // status dot: red when the camera is unreachable, green while video time advances, orange otherwise
  const dot = q('.dot');
  let lastT = -1, lastMove = 0;
  setInterval(() => {
    const v = player.video, now = Date.now();
    if (v && v.currentTime !== lastT) { lastT = v.currentTime; lastMove = now; }
    const playing = lastMove && now - lastMove < STALL_MS;
    if (!dot) return;
    dot.className = 'dot ' + (cam.online === false ? 'err' : playing ? 'ok' : 'wait');
    dot.title = cam.online === false ? 'Nedostupna' : playing ? 'Uživo' : 'Povezivanje…';
  }, 1000);

  return { play, ptz, view };
}
