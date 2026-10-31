import { api, el } from './common.js';

const RULES = [
  ['on_motion', 'Pokret na kameri'],
  ['with_snapshot', 'Pošalji i sliku iz kamere uz poruku o pokretu'],
  ['on_camera_offline', 'Kamera postala nedostupna'],
  ['on_camera_online', 'Kamera ponovo radi'],
  ['on_storage', 'Problem sa snimanjem (npr. računar sa folderom ugašen) i povratak'],
];

// channel forms: [field, label, type, placeholder]; the secret field is separate
const CHANNELS = {
  telegram: {
    title: 'Telegram',
    help: 'U Telegramu otvori @BotFather → /newbot i kopiraj token. Zatim pošalji svom botu /start. ' +
      'Chat ID saznaš od @userinfobot (tvoj ID) ili, za grupu, dodaj bota u grupu.',
    fields: [['chat_id', 'Chat ID', 'text', '123456789']],
    secret: ['Token bota', '123456:ABC…'],
  },
  ntfy: {
    title: 'ntfy (push na telefon)',
    help: 'Instaliraj ntfy aplikaciju i pretplati se na temu. Na javnom ntfy.sh tema je jedina zaštita, ' +
      'pa izaberi dugačko, teško pogodivo ime. Token je potreban samo za zaštićen server.',
    fields: [['server', 'Server', 'text', 'https://ntfy.sh'], ['topic', 'Tema (topic)', 'text', 'kamere-…']],
    secret: ['Pristupni token (nije obavezno)', 'tk_…'],
  },
  email: {
    title: 'Email',
    help: 'Za Gmail: server smtp.gmail.com, port 587, starttls. Lozinka je „lozinka za aplikacije” sa Google naloga, ne obična lozinka.',
    fields: [['host', 'SMTP server', 'text', 'smtp.gmail.com'], ['port', 'Port', 'number', '587'],
      ['security', 'Bezbednost', 'select', ['starttls', 'ssl', 'none']], ['username', 'Korisnik', 'text', ''],
      ['sender', 'Pošiljalac (From)', 'text', 'kamere@…'], ['recipients', 'Primaoci (zarezom)', 'text', 'ja@…']],
    secret: ['Lozinka', ''],
  },
  mqtt: {
    title: 'Home Assistant (MQTT)',
    help: 'Broker je obično Mosquitto dodatak u Home Assistant-u. Uz uključeno otkrivanje, svaka kamera se u HA pojavi ' +
      'kao uređaj sa senzorima „Pokret” i „Dostupnost”. Teme: <prefiks>/<kamera>/status, /motion, /event.',
    fields: [['host', 'Broker (IP)', 'text', '192.168.1.5'], ['port', 'Port', 'number', '1883'],
      ['username', 'Korisnik', 'text', ''], ['prefix', 'Prefiks tema', 'text', 'xiaomi_cam'],
      ['discovery', 'Automatsko otkrivanje u Home Assistant-u', 'switch', true],
      ['discovery_prefix', 'Prefiks otkrivanja', 'text', 'homeassistant']],
    secret: ['Lozinka', ''],
  },
};

const fmt = s => (s ? new Date(s).toLocaleString('sr-Latn-RS', { dateStyle: 'short', timeStyle: 'short' }) : '');
let data;

function switchEl(id, label, checked) {
  const input = el('input', { type: 'checkbox', class: 'form-check-input', role: 'switch', id });
  input.checked = !!checked;
  return { input, box: el('div', { class: 'form-check form-switch' }, input, el('label', { class: 'form-check-label', for: id }, label)) };
}

function renderRules() {
  const s = data.settings;
  const sw = {};
  document.getElementById('rule-switches').replaceChildren(...RULES.map(([k, label]) => {
    sw[k] = switchEl(`r-${k}`, label, s[k]);
    return sw[k].box;
  }));

  sw.cooldown_enabled = switchEl('r-cooldown', 'Najviše jedna poruka o pokretu iste kamere na', s.cooldown_enabled);
  const minutes = el('input', { type: 'number', min: 1, max: 1440, value: s.cooldown_minutes, class: 'form-control form-control-sm', style: 'width: 5rem', 'aria-label': 'Minuta' });
  document.getElementById('cooldown-row').replaceChildren(sw.cooldown_enabled.box, minutes, el('span', { class: 'small' }, 'min'));

  sw.quiet_enabled = switchEl('r-quiet', 'Tihi sati', s.quiet_enabled);
  const qs = el('input', { type: 'time', value: (s.quiet_start || '23:00').slice(0, 5), class: 'form-control form-control-sm', style: 'width: 7rem', 'aria-label': 'Od' });
  const qe = el('input', { type: 'time', value: (s.quiet_end || '07:00').slice(0, 5), class: 'form-control form-control-sm', style: 'width: 7rem', 'aria-label': 'Do' });
  document.getElementById('quiet-row').replaceChildren(sw.quiet_enabled.box, qs, '–', qe);

  document.getElementById('cam-switches').replaceChildren(...data.cameras.map(c => {
    const t = switchEl(`cam-${c.slug}`, c.name, c.notify_motion);
    t.input.addEventListener('change', () =>
      api(`/api/notify/cameras/${c.slug}`, { method: 'PUT', body: JSON.stringify({ notify_motion: t.input.checked }) }).catch(() => {}));
    return t.box;
  }));

  const form = document.getElementById('rules');
  const msg = document.getElementById('rules-msg');
  form.onsubmit = async ev => {
    ev.preventDefault();
    const body = Object.fromEntries(Object.entries(sw).map(([k, v]) => [k, v.input.checked]));
    Object.assign(body, { cooldown_minutes: Number(minutes.value), quiet_start: qs.value || null, quiet_end: qe.value || null });
    try { await api('/api/notify/settings', { method: 'PUT', body: JSON.stringify(body) }); msg.className = 'small text-success'; msg.textContent = 'Sačuvano.'; }
    catch (e) { msg.className = 'small text-danger'; msg.textContent = e.message; }
  };
}

function renderChannel(kind) {
  const def = CHANNELS[kind];
  const ch = data.channels[kind];
  const inputs = {};
  const enabled = switchEl(`ch-${kind}`, 'Uključeno', ch.enabled);

  const fields = def.fields.map(([k, label, type, ph]) => {
    const id = `ch-${kind}-${k}`;
    if (type === 'switch') {
      const t = switchEl(id, label, ch.config[k] ?? ph);
      inputs[k] = t.input;
      return el('div', { class: 'col-12' }, t.box);
    }
    let input;
    if (type === 'select') {
      input = el('select', { class: 'form-select form-select-sm', id });
      for (const o of ph) { const opt = el('option', { value: o }, o); opt.selected = (ch.config[k] || ph[0]) === o; input.append(opt); }
    } else {
      input = el('input', { class: 'form-control form-control-sm', id, type, placeholder: ph, value: ch.config[k] ?? '' });
    }
    inputs[k] = input;
    return el('div', { class: 'col-sm-6' }, el('label', { class: 'form-label small mb-0', for: id }, label), input);
  });
  const secretId = `ch-${kind}-secret`;
  const secret = el('input', { class: 'form-control form-control-sm', id: secretId, type: 'password', autocomplete: 'new-password',
    placeholder: ch.has_secret ? '•••••• (ostavi prazno da ostane isto)' : def.secret[1] });

  const status = el('div', { class: 'small' });
  const showStatus = c => {
    const parts = [];
    if (kind === 'mqtt' && c.enabled) parts.push(c.connected ? '✓ povezano sa brokerom' : `✗ nije povezano${c.error ? ': ' + c.error : ''}`);
    if (c.last_ok_at) parts.push(`poslednje uspešno slanje: ${fmt(c.last_ok_at)}`);
    if (c.last_error) parts.push(`poslednja greška: ${c.last_error}`);
    status.textContent = parts.join(' · ');
    status.className = 'small ' + (c.last_error || (kind === 'mqtt' && c.enabled && !c.connected) ? 'text-warning' : 'text-body-secondary');
  };
  showStatus(ch);

  const msg = el('span', { class: 'small' });
  const body = () => ({
    enabled: enabled.input.checked,
    config: Object.fromEntries(Object.entries(inputs).map(([k, i]) => [k, i.type === 'checkbox' ? i.checked : i.value.trim()])),
    secret: secret.value || null,
  });
  const say = (t, ok) => { msg.textContent = t; msg.className = 'small ' + (ok ? 'text-success' : 'text-danger'); };
  const test = el('button', { type: 'button', class: 'btn btn-sm btn-outline-secondary' }, kind === 'mqtt' ? 'Proveri vezu' : 'Pošalji probu');
  test.onclick = async () => {
    test.disabled = true; say('Šaljem…', true);
    try { await api(`/api/notify/channels/${kind}/test`, { method: 'POST', body: JSON.stringify(body()) }); say(kind === 'mqtt' ? 'Veza radi.' : 'Poslato — proveri telefon/poštu.', true); }
    catch (e) { say(e.message, false); }
    finally { test.disabled = false; }
  };
  const save = el('button', { type: 'button', class: 'btn btn-sm btn-primary' }, 'Sačuvaj');
  save.onclick = async () => {
    try {
      await api(`/api/notify/channels/${kind}`, { method: 'PUT', body: JSON.stringify(body()) });
      secret.value = ''; say('Sačuvano.', true);
    } catch (e) { say(e.message, false); }
  };

  return el('section', { class: 'card card-body gap-2', id: `card-${kind}` },
    el('div', { class: 'd-flex justify-content-between align-items-center' }, el('h3', { class: 'h6 mb-0' }, def.title), enabled.box),
    el('div', { class: 'small text-body-secondary' }, def.help),
    el('div', { class: 'row g-2' }, ...fields,
      el('div', { class: 'col-sm-6' }, el('label', { class: 'form-label small mb-0', for: secretId }, def.secret[0]), secret)),
    el('div', { class: 'd-flex gap-2 align-items-center' }, test, save, msg),
    status);
}

async function load() {
  data = await api('/api/notify');
  renderRules();
  document.getElementById('channels').replaceChildren(...Object.keys(CHANNELS).map(renderChannel));
}

await load();
