import { api, el } from './common.js';

const NAMES = { database: 'Baza', go2rtc: 'go2rtc (video)', recorder: 'Snimač', storage: 'Skladište snimaka',
  https: 'HTTPS (Caddy)', mqtt: 'Home Assistant (MQTT)' };
const gb = b => `${(b / 1024 ** 3).toFixed(1)} GB`;
const fmt = s => new Date(s).toLocaleString('sr-Latn-RS', { dateStyle: 'short', timeStyle: 'short' });
const dur = s => {
  if (s == null) return '—';
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
};
let alarm = { disk_warn_pct: 80, disk_crit_pct: 90 };

function gauge(title, pct, sub, warnAt = alarm.disk_warn_pct, critAt = alarm.disk_crit_pct) {
  const cls = pct == null ? 'bg-secondary' : pct >= critAt ? 'bg-danger' : pct >= warnAt ? 'bg-warning' : 'bg-success';
  return el('div', { class: 'col-sm-6 col-lg-3' }, el('section', { class: 'card card-body h-100' },
    el('div', { class: 'small text-body-secondary' }, title),
    el('div', { class: 'fs-4' }, pct == null ? '—' : `${Math.round(pct)} %`),
    el('div', { class: 'progress mb-1', style: 'height: 6px' }, el('div', { class: `progress-bar ${cls}`, style: `width: ${pct ?? 0}%` })),
    el('div', { class: 'small text-body-secondary' }, sub)));
}

function render(d) {
  alarm = d.alarm;
  const m = d.metrics;
  const noStats = 'nema podataka (servis xiaomi-cam-hub-stats ne radi)';
  document.getElementById('metrics').replaceChildren(
    gauge('Procesor', m.cpu_pct, m.stats_ok ? `${m.cpus} jezgra · opterećenje ${m.load.map(x => x.toFixed(2)).join(' / ')}` : noStats, 85, 95),
    gauge('RAM', m.memory?.pct, m.memory ? `${gb(m.memory.used)} od ${gb(m.memory.total)}` : noStats, 85, 95),
    gauge('Disk servera', m.server_disk.pct, `slobodno ${gb(m.server_disk.free)} od ${gb(m.server_disk.total)}`),
    gauge('Disk sa snimcima', m.share_disk?.pct, m.share_disk ? `slobodno ${gb(m.share_disk.free)} od ${gb(m.share_disk.total)}` : 'nema podataka (skladište nije dostupno)'),
  );
  document.getElementById('services').replaceChildren(...Object.entries(d.services).map(([k, s]) => el('tr', {},
    el('td', { class: 'text-nowrap' }, el('span', { class: `dot ${s.ok === true ? 'ok' : s.ok === false ? 'err' : 'wait'} d-inline-block me-2` }), NAMES[k] || k),
    el('td', { class: 'small text-body-secondary' }, s.detail || ''))));
  const up = d.cameras.filter(c => c.online).length;
  document.getElementById('cameras').replaceChildren(
    el('div', { class: 'mb-1' }, `${up} od ${d.cameras.length} dostupno · server (CT) radi ${dur(m.uptime_s)}`),
    ...d.cameras.map(c => {
      const w = c.watchdog || {};
      const noVideo = c.online && w.video_ok === false;
      const notes = [];
      if (c.online === false && c.last_seen_at) notes.push(`poslednji put viđena ${fmt(c.last_seen_at)}`);
      if (noVideo) notes.push('na mreži, ali ne daje sliku');
      if (w.repaired_at) notes.push(`samopopravka ${fmt(w.repaired_at)}${w.last_action ? ` (${w.last_action})` : ''}`);
      return el('div', {}, el('span', { class: `dot ${noVideo ? 'wait' : c.online ? 'ok' : c.online === false ? 'err' : 'wait'} d-inline-block me-2` }),
        c.name, notes.length ? el('span', { class: noVideo ? 'text-warning' : 'text-body-secondary' }, ` · ${notes.join(' · ')}`) : '');
    }));
  document.getElementById('updated').textContent = `osveženo ${new Date().toLocaleTimeString('sr-Latn-RS')}`;
}

async function load() { render(await api('/api/system')); }

const form = document.getElementById('alarm');
const msg = document.getElementById('a-msg');
form.addEventListener('submit', async ev => {
  ev.preventDefault();
  const body = { on_disk: document.getElementById('a-on').checked,
    disk_warn_pct: Number(document.getElementById('a-warn').value), disk_crit_pct: Number(document.getElementById('a-crit').value) };
  try { await api('/api/system/alarm', { method: 'PUT', body: JSON.stringify(body) }); msg.className = 'small text-success'; msg.textContent = 'Sačuvano.'; await load(); }
  catch (e) { msg.className = 'small text-danger'; msg.textContent = e.message; }
});

const wdMsg = document.getElementById('w-msg');
document.getElementById('wd').addEventListener('submit', async ev => {
  ev.preventDefault();
  const body = { enabled: document.getElementById('w-on').checked, repair: document.getElementById('w-repair').checked,
    notify: document.getElementById('w-notify').checked };
  try { await api('/api/system/watchdog', { method: 'PUT', body: JSON.stringify(body) }); wdMsg.className = 'small text-success'; wdMsg.textContent = 'Sačuvano.'; }
  catch (e) { wdMsg.className = 'small text-danger'; wdMsg.textContent = e.message; }
});

const first = await api('/api/system');
document.getElementById('w-on').checked = first.watchdog.enabled;
document.getElementById('w-repair').checked = first.watchdog.repair;
document.getElementById('w-notify').checked = first.watchdog.notify;
document.getElementById('a-on').checked = first.alarm.on_disk;
document.getElementById('a-warn').value = first.alarm.disk_warn_pct;
document.getElementById('a-crit').value = first.alarm.disk_crit_pct;
render(first);
setInterval(() => load().catch(() => {}), 10000);
