import { api, el } from './common.js';

const tbody = document.getElementById('cams');
const form = document.getElementById('add');
const msg = document.getElementById('add-msg');

function status(c) {
  if (!c.enabled) return el('span', { class: 'badge text-bg-secondary' }, 'isključena');
  if (c.online === true) return el('span', { class: 'badge text-bg-success' }, 'dostupna');
  if (c.online === false) return el('span', { class: 'badge text-bg-danger' }, 'nedostupna');
  return el('span', { class: 'badge text-bg-warning' }, 'provera…');
}

function row(c, i, cams) {
  const name = el('input', { class: 'form-control form-control-sm', value: c.name, maxlength: 128, 'aria-label': 'Naziv' });
  const loc = el('input', { class: 'form-control form-control-sm', value: c.location || '', maxlength: 128, 'aria-label': 'Lokacija' });
  const save = el('button', { class: 'btn btn-sm btn-outline-primary d-none' }, 'Sačuvaj');
  const err = el('div', { class: 'small text-danger' });
  const dirty = () => save.classList.toggle('d-none', name.value === c.name && loc.value === (c.location || ''));
  name.addEventListener('input', dirty);
  loc.addEventListener('input', dirty);

  const patch = async body => {
    err.textContent = '';
    try { await api(`/api/cameras/${c.slug}`, { method: 'PATCH', body: JSON.stringify(body) }); await load(); }
    catch (e) { err.textContent = e.message; }
  };
  save.onclick = () => patch({ name: name.value, location: loc.value });

  // swap sort_order with the neighbour
  const move = async d => {
    const other = cams[i + d];
    if (!other) return;
    await api(`/api/cameras/${other.slug}`, { method: 'PATCH', body: JSON.stringify({ sort_order: c.sort_order }) });
    await patch({ sort_order: other.sort_order === c.sort_order ? c.sort_order + d : other.sort_order });
  };

  const enabled = el('input', { class: 'form-check-input', type: 'checkbox', role: 'switch', 'aria-label': 'Prikazana' });
  enabled.checked = c.enabled;
  enabled.addEventListener('change', () => patch({ enabled: enabled.checked }));

  const actions = el('td', { class: 'text-nowrap' }, save);
  const del = el('button', { class: 'btn btn-sm btn-outline-danger ms-1' }, 'Obriši');
  del.onclick = async () => {
    if (del.dataset.armed) {
      try { await api(`/api/cameras/${c.slug}`, { method: 'DELETE' }); await load(); await loadXiaomiIfShown(); }
      catch (e) { err.textContent = e.message; }
    } else {
      del.dataset.armed = '1'; del.textContent = 'Potvrdi brisanje';
      setTimeout(() => { delete del.dataset.armed; del.textContent = 'Obriši'; }, 4000);
    }
  };
  actions.append(del, err);

  const source = c.kind === 'xiaomi'
    ? el('span', {}, `Xiaomi · ${c.model || ''}`, el('br'), el('span', { class: 'small text-body-secondary' }, c.ip || ''))
    : el('span', { class: 'small font-monospace text-break' }, c.source || '');
  if (c.kind === 'xiaomi') {
    // pan/tilt arrows on or off for this camera
    const id = `ptz-${c.slug}`;
    const ptz = el('input', { class: 'form-check-input', type: 'checkbox', role: 'switch', id });
    ptz.checked = !!c.ptz;
    ptz.addEventListener('change', () => patch({ ptz: ptz.checked }));
    source.append(el('div', { class: 'form-check form-switch small mb-0' }, ptz,
      el('label', { class: 'form-check-label', for: id }, 'okretanje (PTZ)')));
  }

  return el('tr', {},
    el('td', { class: 'text-nowrap' },
      el('button', { class: 'btn btn-sm btn-link px-1', title: 'Gore', onclick: () => move(-1) }, '▲'),
      el('button', { class: 'btn btn-sm btn-link px-1', title: 'Dole', onclick: () => move(1) }, '▼')),
    el('td', {}, name), el('td', {}, loc), el('td', {}, source), el('td', {}, status(c)),
    el('td', {}, el('div', { class: 'form-check form-switch' }, enabled)), actions);
}

async function load() {
  const cams = await api('/api/cameras?all=true');
  tbody.replaceChildren(...cams.map((c, i) => row(c, i, cams)));
}

function formBody() {
  const f = new FormData(form);
  return {
    name: f.get('name'), location: f.get('location') || null, source: f.get('source'),
    sd_source: f.get('sd_source') || null, mode: f.get('mode'),
  };
}

function say(text, ok) { msg.textContent = text; msg.className = 'small ' + (ok ? 'text-success' : 'text-danger'); }

document.getElementById('btn-probe').onclick = async ev => {
  ev.target.disabled = true; say('Proveravam…', true);
  try { const r = await api('/api/cameras/probe', { method: 'POST', body: JSON.stringify(formBody()) }); say(`Slika stiže (${r.resolution}).`, true); }
  catch (e) { say(e.message, false); }
  finally { ev.target.disabled = false; }
};

form.addEventListener('submit', async ev => {
  ev.preventDefault();
  const btn = form.querySelector('button.btn-primary');
  btn.disabled = true; say('Proveravam vezu i dodajem…', true);
  try {
    const cam = await api('/api/cameras', { method: 'POST', body: JSON.stringify(formBody()) });
    say(`Dodata „${cam.name}”${cam.resolution ? ` (${cam.resolution})` : ''}.`, true);
    form.reset();
    await load();
  } catch (e) { say(e.message, false); }
  finally { btn.disabled = false; }
});

// --- Xiaomi account and cameras ----------------------------------------------------------------
const xAccount = document.getElementById('x-account');
const xRegion = document.getElementById('x-region');
const xMsg = document.getElementById('x-msg');
const xTable = document.getElementById('x-table');
const xBody = document.getElementById('x-devices');
const xLogin = document.getElementById('x-login');
const xSay = (text, ok) => { xMsg.textContent = text; xMsg.className = 'small ' + (ok ? 'text-success' : 'text-danger'); };

async function loadAccounts() {
  const { accounts, regions } = await api('/api/xiaomi/accounts');
  xAccount.replaceChildren(...(accounts.length ? accounts.map(a => el('option', { value: a }, a))
    : [el('option', { value: '' }, '(nema prijave)')]));
  if (!xRegion.options.length) {
    for (const [v, label] of Object.entries(regions)) xRegion.append(el('option', { value: v }, label));
  }
  if (!accounts.length) xLogin.classList.remove('d-none');
}

function deviceRow(d) {
  const name = el('input', { class: 'form-control form-control-sm', value: d.name, maxlength: 128, 'aria-label': 'Naziv' });
  const ptzId = `xptz-${d.did}`;
  const ptz = el('input', { class: 'form-check-input', type: 'checkbox', role: 'switch', id: ptzId });
  ptz.checked = ['xiaomi.camera.c01a01', 'chuangmi.camera.ipc019'].includes(d.model);
  const result = el('div', { class: 'small' });
  const action = el('td', { class: 'text-nowrap' });
  if (d.added_as) {
    action.append(el('span', { class: 'badge text-bg-secondary' }, `dodata: ${d.added_as}`));
  } else {
    const add = el('button', { class: 'btn btn-sm btn-primary' }, 'Dodaj');
    add.disabled = !d.reachable;
    add.title = d.reachable ? '' : 'Kamera nije dostupna iz mreže servera';
    add.onclick = async () => {
      add.disabled = true; add.textContent = 'Dodajem…'; result.className = 'small'; result.textContent = '';
      try {
        const cam = await api('/api/xiaomi/cameras', { method: 'POST', body: JSON.stringify({
          account: xAccount.value, region: xRegion.value, did: d.did, name: name.value, ptz: ptz.checked }) });
        const r = cam.report;
        result.className = 'small text-success';
        result.textContent = `Dodata: HD ${r.hd}${r.sd ? `, SD ${r.sd}` : ''}, kontrola: ${r.controls}.`;
        add.remove();
        await load();
      } catch (e) {
        result.className = 'small text-danger'; result.textContent = e.message;
        add.disabled = false; add.textContent = 'Dodaj';
      }
    };
    action.append(add);
  }
  action.append(result);
  return el('tr', {},
    el('td', {}, d.added_as ? d.name : name),
    el('td', { class: 'small' }, d.model, ' ', el('span', { class: `badge ${d.known_model ? 'text-bg-success' : 'text-bg-info'}` },
      d.known_model ? 'provereno' : 'novi model')),
    el('td', { class: 'small' }, d.ip),
    el('td', {}, el('span', { class: `badge ${d.reachable ? 'text-bg-success' : 'text-bg-secondary'}` }, d.reachable ? 'da' : 'ne')),
    el('td', {}, d.added_as ? '' : el('div', { class: 'form-check form-switch mb-0' }, ptz,
      el('label', { class: 'form-check-label small', for: ptzId }, 'ima motor'))),
    action);
}

async function loadXiaomi() {
  if (!xAccount.value) { xSay('Prvo se prijavi na Xiaomi nalog.', false); return; }
  xSay('Učitavam…', true);
  try {
    const list = await api(`/api/xiaomi/devices?account=${encodeURIComponent(xAccount.value)}&region=${xRegion.value}`);
    // cameras in this network first
    list.sort((a, b) => (b.reachable - a.reachable) || a.name.localeCompare(b.name));
    xBody.replaceChildren(...list.map(deviceRow));
    xTable.classList.remove('d-none');
    xSay(`${list.length} kamera na nalogu, ${list.filter(d => d.reachable).length} u ovoj mreži.`, true);
  } catch (e) { xSay(e.message, false); }
}
async function loadXiaomiIfShown() { if (!xTable.classList.contains('d-none')) await loadXiaomi(); }

document.getElementById('x-load').onclick = loadXiaomi;
document.getElementById('x-login-toggle').onclick = () => xLogin.classList.toggle('d-none');

xLogin.addEventListener('submit', async ev => {
  ev.preventDefault();
  // only the visible step's fields: go2rtc restarts the login whenever it gets a username
  const step = ['captcha', 'verify'].find(s => !xLogin.querySelector(`.x-step-${s}`).classList.contains('d-none')) || 'cred';
  const fields = { cred: ['username', 'password'], captcha: ['captcha'], verify: ['verify'] }[step];
  const body = Object.fromEntries(fields.map(k => [k, xLogin.elements[k].value.trim()]).filter(([, v]) => v));
  xSay('Prijavljujem…', true);
  try {
    const r = await api('/api/xiaomi/login', { method: 'POST', body: JSON.stringify(body) });
    const show = step => ['cred', 'captcha', 'verify'].forEach(s =>
      xLogin.querySelector(`.x-step-${s}`).classList.toggle('d-none', s !== step));
    if (r.status === 'captcha') {
      document.getElementById('x-captcha-img').src = 'data:image/jpeg;base64,' + r.image;
      xLogin.elements.captcha.value = '';
      show('captcha'); xSay('Prepiši znakove sa slike.', true);
    } else if (r.status === 'verify') {
      document.getElementById('x-verify-target').textContent = `Xiaomi je poslao kod na ${r.target}.`;
      show('verify'); xSay('Unesi kod iz poruke.', true);
    } else {
      xLogin.reset(); show('cred'); xLogin.classList.add('d-none');
      xSay('Prijava uspela.', true);
      await loadAccounts();
    }
  } catch (e) { xSay(e.message, false); }
});

// no auto-refresh: it would wipe names being edited in the table
await Promise.all([load(), loadAccounts()]);
