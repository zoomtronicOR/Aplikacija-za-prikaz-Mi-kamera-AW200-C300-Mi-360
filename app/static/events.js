import { api, el } from './common.js';

const role = document.querySelector('main').dataset.role;
const daySel = document.getElementById('f-day');
const camSel = document.getElementById('f-cam');
const kindSel = document.getElementById('f-kind');
const list = document.getElementById('list');
const more = document.getElementById('more');
const count = document.getElementById('count');
const timeline = document.getElementById('timeline');
const PAGE = 50;
let offset = 0;

const pad = n => String(n).padStart(2, '0');
const today = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
const hms = s => new Date(s).toLocaleTimeString('sr-Latn-RS');
const dur = s => (s < 60 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`);

function title(ev) {
  switch (ev.kind) {
    case 'motion_start': {
      if (!ev.detail) return 'Pokret';
      const [what, score] = [ev.detail.slice(0, ev.detail.lastIndexOf(' ')), ev.detail.slice(ev.detail.lastIndexOf(' ') + 1)];
      return `${what[0].toUpperCase()}${what.slice(1)} (${score})`;
    }
    case 'camera_offline': return 'Kamera nedostupna';
    case 'camera_online': return 'Kamera ponovo radi';
    case 'storage_error': return `Problem sa snimanjem${ev.detail ? ': ' + ev.detail : ''}`;
    case 'storage_ok': return 'Snimanje ponovo radi';
    default: return ev.kind;
  }
}

function card(ev) {
  const img = ev.has_thumb
    ? el('img', { src: `/api/events/${ev.id}/thumb`, loading: 'lazy', alt: `Slika: ${title(ev)}` })
    : el('div', { class: 'ev-noimg d-flex align-items-center justify-content-center small text-body-secondary' },
      ev.kind === 'motion_start' ? 'bez slike' : ev.kind.startsWith('camera') ? '📷' : '💾');
  const meta = [];
  if (ev.kind === 'motion_start') {
    meta.push(ev.end ? `trajalo ${dur((new Date(ev.end) - new Date(ev.ts)) / 1000)}` : 'u toku ili bez kraja');
  }
  const actions = el('div', { class: 'mt-1' });
  if (ev.recording) {
    actions.append(el('a', { class: 'btn btn-sm btn-outline-primary',
      href: `/recordings?camera=${encodeURIComponent(ev.camera)}&play=${ev.recording.id}&t=${ev.recording.offset_s}` }, '▶ Pusti snimak'));
  } else if (ev.kind === 'motion_start') {
    // clips are joined and uploaded within a few minutes
    const fresh = Date.now() - new Date(ev.ts) < 10 * 60 * 1000;
    actions.append(el('span', { class: 'small text-body-secondary' }, fresh ? 'snimak se još obrađuje' : 'snimak nije sačuvan'));
  }
  return el('article', { class: 'card card-body py-2 ev-card', id: `ev-${ev.id}` }, img,
    el('div', {},
      el('div', {}, el('strong', {}, title(ev)), ' ', el('span', { class: 'text-body-secondary small' }, ev.camera_name || '')),
      el('div', { class: 'small text-body-secondary' }, [hms(ev.ts), ...meta].join(' · ')),
      actions));
}

async function loadList(reset, highlight) {
  if (reset) { offset = 0; list.replaceChildren(); }
  const q = new URLSearchParams({ day: daySel.value, kind: kindSel.value, offset, limit: PAGE });
  if (camSel.value) q.set('camera', camSel.value);
  const data = await api(`/api/events?${q}`);
  list.append(...data.items.map(card));
  offset += data.items.length;
  count.textContent = `${data.total} događaja`;
  more.classList.toggle('d-none', offset >= data.total);
  if (!data.total) list.append(el('div', { class: 'small text-body-secondary' }, 'Nema događaja za izabrani filter.'));
  if (highlight) {
    const target = document.getElementById(`ev-${highlight}`);
    if (target) { target.classList.add('highlight'); target.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
  }
}

async function loadTimeline() {
  const data = await api(`/api/events/timeline?day=${daySel.value}`);
  const start = new Date(data.start).getTime(), span = new Date(data.end).getTime() - start;
  const now = Date.now();
  const pos = (a, b) => {
    const s = Math.max(0, new Date(a).getTime() - start);
    const e = Math.min(span, (b ? new Date(b).getTime() : Math.min(now, start + span)) - start);
    return `left: ${(s / span) * 100}%; width: ${Math.max(0.15, ((e - s) / span) * 100)}%`;
  };
  timeline.replaceChildren(...data.cameras.map(c => {
    const bar = el('div', { class: 'tl-bar' });
    for (const d of c.down) bar.append(el('div', { class: 'tl-mark tl-down', style: pos(d.start, d.end), title: `nedostupna od ${hms(d.start)}` }));
    for (const m of c.motion) {
      const mark = el('div', { class: 'tl-mark tl-motion', style: pos(m.start, m.end),
        title: `${m.detail || 'pokret'} u ${hms(m.start)}` });
      mark.onclick = () => { camSel.value = c.slug; kindSel.value = 'motion'; loadList(true, m.id); };
      bar.append(mark);
    }
    return el('div', { class: 'tl-row' }, el('span', { class: 'tl-name', title: c.name }, c.name), bar);
  }));
}

async function reload() { await Promise.all([loadTimeline(), loadList(true)]); }

for (const c of await api('/api/cameras')) camSel.append(el('option', { value: c.slug }, c.name));
daySel.value = today();
daySel.addEventListener('change', reload);
camSel.addEventListener('change', () => loadList(true));
kindSel.addEventListener('change', () => loadList(true));
more.onclick = () => loadList(false);
await reload();
setInterval(loadTimeline, 60000);

// admin: archive settings (thumbnail switch, retention)
if (role === 'admin') {
  const box = document.getElementById('settings');
  box.classList.remove('d-none');
  const thumbs = document.getElementById('s-thumbs');
  const days = document.getElementById('s-days');
  const msg = document.getElementById('s-msg');
  const s = await api('/api/events/settings');
  thumbs.checked = s.event_thumbs; days.value = s.events_keep_days;
  document.getElementById('settings-form').addEventListener('submit', async ev => {
    ev.preventDefault();
    try {
      await api('/api/events/settings', { method: 'PUT', body: JSON.stringify({ event_thumbs: thumbs.checked, events_keep_days: Number(days.value) }) });
      msg.className = 'small text-success'; msg.textContent = 'Sačuvano.';
    } catch (e) { msg.className = 'small text-danger'; msg.textContent = e.message; }
  });
}
