import { api, el } from './common.js';

const role = document.querySelector('main').dataset.role;
const camSel = document.getElementById('f-cam');
const daySel = document.getElementById('f-day');
const rows = document.getElementById('rows');
const more = document.getElementById('more');
const count = document.getElementById('count');
const box = document.getElementById('player-box');
const player = document.getElementById('player');
const playerMsg = document.getElementById('player-msg');

const REASONS = { manual: 'ručno', continuous: 'stalno', schedule: 'raspored', motion: 'pokret', recovered: 'oporavljeno' };
const PAGE = 100;
let offset = 0;

const fmtTime = s => new Date(s).toLocaleString('sr-Latn-RS', { dateStyle: 'short', timeStyle: 'medium' });
const fmtDur = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
const fmtSize = b => b > 1024 ** 3 ? (b / 1024 ** 3).toFixed(2) + ' GB' : (b / 1024 ** 2).toFixed(1) + ' MB';

function play(r) {
  box.classList.remove('d-none');
  playerMsg.classList.add('d-none');
  document.getElementById('player-title').textContent = `${r.camera_name} · ${fmtTime(r.started_at)}`;
  player.src = `/api/recordings/${r.id}/file`;
  player.play().catch(() => {});
  box.scrollIntoView({ behavior: 'smooth', block: 'start' });
}
player.addEventListener('error', () => playerMsg.classList.remove('d-none'));
document.getElementById('player-close').onclick = () => {
  player.pause(); player.removeAttribute('src'); player.load(); box.classList.add('d-none');
};

function row(r) {
  const actions = el('td', { class: 'text-nowrap text-end' },
    el('button', { class: 'btn btn-sm btn-outline-primary', onclick: () => play(r) }, '▶ Pusti'));
  if (role === 'admin' || role === 'user') {
    actions.append(' ', el('a', { class: 'btn btn-sm btn-outline-secondary', href: `/api/recordings/${r.id}/file?download=1` }, '⭳ Preuzmi'));
  }
  if (role === 'admin') {
    const del = el('button', { class: 'btn btn-sm btn-outline-danger ms-1' }, 'Obriši');
    del.onclick = async () => {
      if (!del.dataset.armed) {
        del.dataset.armed = '1'; del.textContent = 'Potvrdi';
        setTimeout(() => { delete del.dataset.armed; del.textContent = 'Obriši'; }, 4000);
        return;
      }
      try { await api(`/api/recordings/${r.id}`, { method: 'DELETE' }); tr.remove(); }
      catch (e) { del.textContent = e.message; }
    };
    actions.append(del);
  }
  const tr = el('tr', {}, el('td', {}, fmtTime(r.started_at)), el('td', {}, r.camera_name), el('td', {}, fmtDur(r.duration_s)),
    el('td', {}, REASONS[r.reason] || r.reason), el('td', {}, fmtSize(r.size_bytes)), actions);
  return tr;
}

async function load(reset) {
  if (reset) { offset = 0; rows.replaceChildren(); }
  const q = new URLSearchParams({ offset, limit: PAGE });
  if (camSel.value) q.set('camera', camSel.value);
  if (daySel.value) q.set('day', daySel.value);
  const data = await api(`/api/recordings?${q}`);
  rows.append(...data.items.map(row));
  offset += data.items.length;
  count.textContent = `${data.total} snimaka`;
  more.classList.toggle('d-none', offset >= data.total);
  if (!data.total) rows.append(el('tr', {}, el('td', { colspan: 6, class: 'text-body-secondary small' }, 'Nema snimaka za izabrani filter.')));
}

for (const c of await api('/api/cameras')) camSel.append(el('option', { value: c.slug }, c.name));
camSel.addEventListener('change', () => load(true));
daySel.addEventListener('change', () => load(true));
more.onclick = () => load(false);
await load(true);
