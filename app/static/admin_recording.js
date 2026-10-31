import { api, el } from './common.js';

const form = document.getElementById('storage');
const msg = document.getElementById('storage-msg');
const statusBox = document.getElementById('storage-status');
const tbody = document.getElementById('cams');

const MODES = { off: 'Isključeno', continuous: 'Stalno (24/7)', schedule: 'Po rasporedu', motion: 'Na pokret (uskoro)' };
const DAYS = ['Pon', 'Uto', 'Sre', 'Čet', 'Pet', 'Sub', 'Ned'];
const REASONS = { manual: 'ručno', continuous: 'stalno', schedule: 'po rasporedu', motion: 'pokret' };

const gb = b => (b / 1024 ** 3).toFixed(2) + ' GB';
const say = (text, ok) => { msg.textContent = text; msg.className = 'small ' + (ok ? 'text-success' : 'text-danger'); };

function renderStatus(st) {
  if (st.status_ok === null || st.status_ok === undefined) {
    statusBox.innerHTML = '';
    statusBox.append(el('span', { class: 'badge text-bg-secondary' }, 'još nema provere'));
  } else {
    statusBox.replaceChildren(
      el('span', { class: `badge ${st.status_ok ? 'text-bg-success' : 'text-bg-danger'}` }, st.status_ok ? 'skladište radi' : 'problem sa skladištem'),
      ' ',
      el('span', { class: 'text-body-secondary' },
        `${st.status_error ? st.status_error + ' · ' : ''}čeka slanje: ${gb(st.pending_bytes || 0)}`));
  }
}

async function loadStorage() {
  const st = await api('/api/recording/storage');
  for (const k of ['host', 'share', 'folder', 'username', 'default_retention_days', 'buffer_max_gb']) {
    if (st[k] !== null && st[k] !== undefined) form.elements[k].value = st[k];
  }
  form.elements.password.placeholder = st.has_password ? '•••••• (ostavi prazno da ostane ista)' : '';
  renderStatus(st);
}

function storageBody() {
  const f = new FormData(form);
  return {
    host: f.get('host'), share: f.get('share'), folder: f.get('folder') || '', username: f.get('username'),
    password: f.get('password') || null,
    default_retention_days: Number(f.get('default_retention_days')), buffer_max_gb: Number(f.get('buffer_max_gb')),
  };
}

document.getElementById('btn-test').onclick = async ev => {
  ev.target.disabled = true; say('Proveravam (upis i brisanje probnog fajla)…', true);
  try { await api('/api/recording/storage/test', { method: 'POST', body: JSON.stringify(storageBody()) }); say('Veza radi, upis je moguć.', true); }
  catch (e) { say(e.message, false); }
  finally { ev.target.disabled = false; }
};

form.addEventListener('submit', async ev => {
  ev.preventDefault();
  try {
    const st = await api('/api/recording/storage', { method: 'PUT', body: JSON.stringify(storageBody()) });
    form.elements.password.value = '';
    form.elements.password.placeholder = st.has_password ? '•••••• (ostavi prazno da ostane ista)' : '';
    say('Sačuvano.', true);
    renderStatus(st);
  } catch (e) { say(e.message, false); }
});

function camRow(c) {
  const mode = el('select', { class: 'form-select form-select-sm', 'aria-label': 'Način snimanja' });
  for (const [v, label] of Object.entries(MODES)) {
    const o = el('option', { value: v }, label);
    o.selected = c.mode === v;
    if (v === 'motion') o.disabled = true;
    mode.append(o);
  }
  const start = el('input', { type: 'time', class: 'form-control form-control-sm', value: (c.schedule_start || '22:00').slice(0, 5), 'aria-label': 'Od' });
  const end = el('input', { type: 'time', class: 'form-control form-control-sm', value: (c.schedule_end || '06:00').slice(0, 5), 'aria-label': 'Do' });
  const days = el('div', { class: 'd-flex flex-wrap gap-1 mt-1' });
  DAYS.forEach((d, i) => {
    const id = `${c.slug}-d${i}`;
    const cb = el('input', { type: 'checkbox', class: 'btn-check', id, value: String(i), autocomplete: 'off' });
    cb.checked = (c.schedule_days ?? '0123456').includes(String(i));
    days.append(cb, el('label', { class: 'btn btn-outline-secondary btn-sm py-0 px-1', for: id }, d));
  });
  const sched = el('div', {}, el('div', { class: 'd-flex gap-1 align-items-center' }, start, '–', end), days);
  const ret = el('input', { type: 'number', min: 1, max: 3650, class: 'form-control form-control-sm', style: 'width: 6rem',
    placeholder: 'podraz.', value: c.retention_days ?? '', 'aria-label': 'Dana čuvanja' });
  const showSched = () => sched.classList.toggle('opacity-25', mode.value !== 'schedule');
  mode.addEventListener('change', showSched);
  showSched();

  let state;
  if (!c.recorder_alive) state = el('span', { class: 'badge text-bg-warning' }, 'snimač ne radi');
  else if (c.active) state = el('span', { class: 'badge text-bg-danger' }, `snima (${REASONS[c.reason] || c.reason})`);
  else state = el('span', { class: 'badge text-bg-secondary' }, 'ne snima');
  const err = el('div', { class: 'small text-danger' }, c.error || '');

  const save = el('button', { class: 'btn btn-sm btn-primary' }, 'Sačuvaj');
  save.onclick = async () => {
    err.textContent = '';
    const body = {
      mode: mode.value, schedule_start: start.value || null, schedule_end: end.value || null,
      schedule_days: [...days.querySelectorAll('input:checked')].map(x => x.value).join(''),
      retention_days: ret.value ? Number(ret.value) : null,
    };
    try { await api(`/api/recording/cameras/${c.slug}`, { method: 'PUT', body: JSON.stringify(body) }); save.textContent = 'Sačuvano ✓'; setTimeout(() => { save.textContent = 'Sačuvaj'; }, 2000); }
    catch (e) { err.textContent = e.message; }
  };

  return el('tr', {}, el('td', {}, c.name), el('td', {}, mode), el('td', {}, sched), el('td', {}, ret),
    el('td', {}, state, err), el('td', {}, save));
}

async function loadCams() {
  const cams = await api('/api/recording/cameras');
  tbody.replaceChildren(...cams.map(camRow));
}

await Promise.all([loadStorage(), loadCams()]);
// status only; rows are not rebuilt so edits in progress survive
setInterval(async () => { try { renderStatus(await api('/api/recording/storage')); } catch {} }, 15000);
