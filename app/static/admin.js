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
  if (c.kind === 'generic') {
    const del = el('button', { class: 'btn btn-sm btn-outline-danger ms-1' }, 'Obriši');
    del.onclick = async () => {
      if (del.dataset.armed) {
        try { await api(`/api/cameras/${c.slug}`, { method: 'DELETE' }); await load(); } catch (e) { err.textContent = e.message; }
      } else {
        del.dataset.armed = '1'; del.textContent = 'Potvrdi brisanje';
        setTimeout(() => { delete del.dataset.armed; del.textContent = 'Obriši'; }, 4000);
      }
    };
    actions.append(del);
  }
  actions.append(err);

  const source = c.kind === 'xiaomi'
    ? el('span', {}, `Xiaomi · ${c.model || ''}`, el('br'), el('span', { class: 'small text-body-secondary' }, c.ip || ''))
    : el('span', { class: 'small font-monospace text-break' }, c.source || '');

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

// no auto-refresh: it would wipe names being edited in the table
await load();
