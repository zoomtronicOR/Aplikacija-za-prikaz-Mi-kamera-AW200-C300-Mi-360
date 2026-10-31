import { api, el } from './common.js';

const tbody = document.getElementById('users');
const fmt = s => (s ? new Date(s).toLocaleString('sr-Latn-RS', { dateStyle: 'short', timeStyle: 'short' }) : '—');
let roles = {};
let me;

function genPassword() {
  // 16 characters from an unambiguous alphabet, from the browser's CSPRNG
  const abc = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  const r = crypto.getRandomValues(new Uint32Array(16));
  return Array.from(r, x => abc[x % abc.length]).join('');
}

function confirmButton(label, armedLabel, cls, action) {
  const b = el('button', { class: `btn btn-sm ${cls} ms-1` }, label);
  b.onclick = async () => {
    if (!b.dataset.armed) {
      b.dataset.armed = '1'; b.textContent = armedLabel;
      setTimeout(() => { delete b.dataset.armed; b.textContent = label; }, 4000);
      return;
    }
    await action();
  };
  return b;
}

function row(u) {
  const err = el('div', { class: 'small text-danger' });
  const self = u.id === me;
  const patch = async body => {
    err.textContent = '';
    try { await api(`/api/users/${u.id}`, { method: 'PATCH', body: JSON.stringify(body) }); }
    catch (e) { err.textContent = e.message; }
    await load();
  };

  const role = el('select', { class: 'form-select form-select-sm w-auto', 'aria-label': 'Uloga' });
  for (const [v, label] of Object.entries(roles)) { const o = el('option', { value: v }, label); o.selected = u.role === v; role.append(o); }
  role.disabled = self;
  role.title = self ? 'Svoju ulogu ne možeš da menjaš' : '';
  role.addEventListener('change', () => patch({ role: role.value }));

  const activeId = `act-${u.id}`;
  const active = el('input', { class: 'form-check-input', type: 'checkbox', role: 'switch', id: activeId });
  active.checked = u.is_active;
  active.disabled = self;
  active.addEventListener('change', () => patch({ is_active: active.checked }));

  // new password inline
  const pwBox = el('div', { class: 'input-group input-group-sm mt-1 d-none', style: 'max-width: 360px' });
  const pw = el('input', { class: 'form-control', type: 'text', minlength: 12, placeholder: 'nova lozinka', autocomplete: 'new-password' });
  const pwSave = el('button', { class: 'btn btn-outline-primary' }, 'Postavi');
  pwSave.onclick = async () => {
    err.textContent = '';
    try {
      await api(`/api/users/${u.id}/password`, { method: 'POST', body: JSON.stringify({ password: pw.value, must_change_password: !self }) });
      pwBox.classList.add('d-none'); err.className = 'small text-success';
      err.textContent = self ? 'Lozinka promenjena.' : 'Lozinka postavljena; korisnik je odjavljen i postaviće svoju pri prijavi.';
    } catch (e) { err.className = 'small text-danger'; err.textContent = e.message; }
  };
  pwBox.append(pw, el('button', { class: 'btn btn-outline-secondary', title: 'Nasumična', onclick: () => { pw.value = genPassword(); } }, '🎲'), pwSave);
  const pwBtn = el('button', { class: 'btn btn-sm btn-outline-secondary' }, 'Nova lozinka');
  pwBtn.onclick = () => pwBox.classList.toggle('d-none');

  const actions = el('td', { class: 'text-nowrap' }, pwBtn);
  if (!self) {
    actions.append(confirmButton('Obriši', 'Potvrdi brisanje', 'btn-outline-danger', async () => {
      try { await api(`/api/users/${u.id}`, { method: 'DELETE' }); await load(); } catch (e) { err.textContent = e.message; }
    }));
  }
  actions.append(pwBox, err);

  const name = el('td', {}, el('strong', {}, u.username), self ? el('span', { class: 'badge text-bg-secondary ms-1' }, 'ti') : '',
    u.must_change_password ? el('div', { class: 'small text-warning' }, 'mora da postavi svoju lozinku') : '');
  return el('tr', {}, name, el('td', {}, role),
    el('td', {}, el('div', { class: 'form-check form-switch mb-0' }, active)), el('td', { class: 'small' }, fmt(u.last_login_at)), actions);
}

async function load() {
  const data = await api('/api/users');
  roles = data.roles; me = data.me;
  tbody.replaceChildren(...data.users.map(row));
  const userSel = document.getElementById('l-user');
  const keep = userSel.value;
  userSel.replaceChildren(el('option', { value: '' }, 'Svi'), ...data.users.map(u => el('option', { value: u.username }, u.username)),
    el('option', { value: 'mqtt' }, 'mqtt'), el('option', { value: 'cli' }, 'cli'));
  userSel.value = keep;
}

// --- add user ---
const addRole = document.getElementById('a-role');
const addMsg = document.getElementById('a-msg');
document.getElementById('a-gen').onclick = () => { document.getElementById('a-pw').value = genPassword(); };
document.getElementById('add').addEventListener('submit', async ev => {
  ev.preventDefault();
  const body = { username: document.getElementById('a-name').value.trim(), role: addRole.value,
    password: document.getElementById('a-pw').value, must_change_password: document.getElementById('a-must').checked };
  try {
    await api('/api/users', { method: 'POST', body: JSON.stringify(body) });
    addMsg.className = 'small text-success';
    addMsg.textContent = `Korisnik „${body.username}” je dodat. Prenesi mu lozinku: ${body.password}`;
    ev.target.reset();
    await load();
  } catch (e) { addMsg.className = 'small text-danger'; addMsg.textContent = e.message; }
});

// --- audit log ---
const ACTIONS = { login: 'prijava', logout: 'odjava', user: 'korisnici', own: 'sopstvena lozinka', camera: 'kamere',
  recording: 'snimanje', notify: 'obaveštenja', xiaomi: 'Xiaomi', mqtt: 'komande iz HA', cameras: 'uvoz kamera', events: 'arhiva' };
const logBody = document.getElementById('log');
const logUser = document.getElementById('l-user');
const logAction = document.getElementById('l-action');
const logMore = document.getElementById('l-more');
let logOffset = 0;

async function loadLog(reset) {
  if (reset) { logOffset = 0; logBody.replaceChildren(); }
  const q = new URLSearchParams({ offset: logOffset, limit: 100 });
  if (logUser.value) q.set('username', logUser.value);
  if (logAction.value) q.set('action', logAction.value);
  const data = await api(`/api/audit?${q}`);
  if (logAction.options.length === 1) {
    for (const a of data.actions) logAction.append(el('option', { value: a }, ACTIONS[a] || a));
  }
  logBody.append(...data.items.map(a => el('tr', { class: a.action.endsWith('failed') || a.action.endsWith('blocked') ? 'table-warning' : '' },
    el('td', { class: 'text-nowrap' }, fmt(a.ts)), el('td', {}, a.username || ''), el('td', {}, a.action),
    el('td', {}, a.target || ''), el('td', { class: 'text-break', style: 'max-width: 28rem' }, a.detail || ''), el('td', {}, a.ip || ''))));
  logOffset += data.items.length;
  document.getElementById('l-count').textContent = `${data.total} zapisa`;
  logMore.classList.toggle('d-none', logOffset >= data.total);
}
logUser.addEventListener('change', () => loadLog(true));
logAction.addEventListener('change', () => loadLog(true));
logMore.onclick = () => loadLog(false);

await load();
for (const [v, label] of Object.entries(roles)) addRole.append(el('option', { value: v }, label));
addRole.value = 'user';
await loadLog(true);
