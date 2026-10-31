import { api, el } from './common.js';

const form = document.getElementById('storage');
const msg = document.getElementById('storage-msg');
const statusBox = document.getElementById('storage-status');
const tbody = document.getElementById('cams');

const MODES = { off: 'Isključeno', continuous: 'Stalno (24/7)', schedule: 'Po rasporedu', motion: 'Na pokret' };
const statusCells = {};  // slug -> function(camera status) that refreshes the status cell
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

const CELLS = 144;  // 9 rows x 16 columns, like the detector's blocks

function zoneEditor(c) {
  const mask = Array.from({ length: CELLS }, (_, i) => c.motion_mask?.[i] === '1');
  const img = el('img', { alt: `Slika kamere ${c.name}` });
  const grid = el('div', { class: 'zone-grid' });
  const cells = mask.map(() => el('div'));
  const paintCell = (i, v) => { mask[i] = v; cells[i].classList.toggle('masked', v); };
  let painting = null;  // value being painted while the pointer is down
  cells.forEach((cell, i) => {
    cell.classList.toggle('masked', mask[i]);
    cell.addEventListener('pointerdown', ev => { ev.preventDefault(); painting = !mask[i]; paintCell(i, painting); });
    cell.addEventListener('pointerenter', () => { if (painting !== null) paintCell(i, painting); });
    grid.append(cell);
  });
  window.addEventListener('pointerup', () => { painting = null; });

  const msg = el('span', { class: 'small text-danger ms-2' });
  const refresh = () => {
    msg.textContent = '';
    img.src = `/api/cameras/${c.slug}/snapshot?quality=sd&t=${Date.now()}`;
  };
  img.addEventListener('error', () => { msg.textContent = 'Slika nije stigla, pokušaj ponovo.'; });

  const box = el('div', { class: 'py-2' },
    el('div', { class: 'small mb-1' }, 'Klikni ili prevuci preko polja koja detektor treba da ignoriše (drveće, ulica, nebo).'),
    el('div', { class: 'zone-wrap mb-1' }, img, grid),
    el('div', { class: 'small zone-legend mb-1' },
      el('span', { style: 'background: rgba(20,20,20,.7); border: 1px solid #777' }), 'ignorisano',
      el('span', { style: 'background: rgba(255,193,7,.6)' }), 'automatski ignorisano (nemirno)',
      el('span', { style: 'box-shadow: inset 0 0 0 2px #e33' }), 'pomera se sada'),
    el('button', { type: 'button', class: 'btn btn-sm btn-outline-secondary', onclick: refresh }, 'Osveži sliku'), ' ',
    el('button', { type: 'button', class: 'btn btn-sm btn-outline-secondary',
      onclick: () => mask.forEach((_, i) => paintCell(i, false)) }, 'Obriši zone'),
    msg);

  return {
    box,
    show: () => { if (!img.src) refresh(); },
    value: () => (mask.some(Boolean) ? mask.map(v => (v ? '1' : '0')).join('') : null),
    live: s => cells.forEach((cell, i) => {
      cell.classList.toggle('live', s.motion_live?.[i] === '1');
      cell.classList.toggle('suppressed', s.motion_suppressed?.[i] === '1' && !mask[i]);
    }),
  };
}

function camRow(c) {
  const mode = el('select', { class: 'form-select form-select-sm', 'aria-label': 'Način snimanja' });
  for (const [v, label] of Object.entries(MODES)) {
    const o = el('option', { value: v }, label);
    o.selected = c.mode === v;
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
  // motion sensitivity, shown under the mode select when "Na pokret" is chosen
  const sens = el('input', { type: 'range', class: 'form-range', min: 1, max: 100, value: c.motion_sensitivity ?? 50,
    'aria-label': 'Osetljivost na pokret' });
  const sensVal = el('span', { class: 'small text-body-secondary' });
  const showSens = () => { sensVal.textContent = `osetljivost ${sens.value}`; };
  sens.addEventListener('input', showSens);
  showSens();
  // every optional feature has its own switch (all on by default)
  const toggle = (key, label, title) => {
    const id = `${c.slug}-${key}`;
    const input = el('input', { type: 'checkbox', class: 'form-check-input', role: 'switch', id });
    input.checked = c[key] ?? true;
    return { key, input, box: el('div', { class: 'form-check form-switch small', title }, input,
      el('label', { class: 'form-check-label', for: id }, label)) };
  };
  const audio = toggle('record_audio', 'Snimaj zvuk', 'Isključeno: snimci su bez zvuka');
  const motionToggles = [
    toggle('motion_auto_ignore', 'Ignoriši delove koji se stalno pomeraju (drveće na vetru)',
      'Delovi slike koji se pomeraju veći deo poslednjih minuta privremeno se ne računaju'),
    toggle('motion_zones_enabled', 'Koristi ručne zone', 'Isključeno: iscrtane zone ostaju sačuvane, ali se ne primenjuju'),
    toggle('motion_noise_adapt', 'Prilagodi prag šumu (noć, kiša)', 'Isključeno: stalni prag promene piksela'),
    toggle('motion_require_group', 'Traži povezanu grupu (ne raštrkane tačke)',
      'Isključeno: broje se svi blokovi koji se pomeraju, i kad nisu jedan pored drugog'),
    toggle('motion_pre_roll', 'Snimi i 10 s pre pokreta', 'Isključeno: snimak počinje od dela u kome je pokret uočen'),
  ];
  const zones = zoneEditor(c);
  const zoneRow = el('tr', { class: 'd-none' }, el('td', { colspan: 6 }, zones.box));
  const zoneBtn = el('button', { type: 'button', class: 'btn btn-sm btn-outline-secondary mt-1' }, 'Zone ▾');
  zoneBtn.onclick = () => {
    const open = zoneRow.classList.toggle('d-none') === false;
    zoneBtn.textContent = open ? 'Zone ▴' : 'Zone ▾';
    if (open) zones.show();
  };
  // AI verification: a switch for the feature and one per object group
  const aiOn = toggle('ai_enabled', 'AI potvrda: snimaj samo kad je u kadru…',
    'Pokret se računa tek kad model prepozna izabrani objekat tamo gde se nešto pomerilo');
  aiOn.input.checked = c.ai_enabled ?? false;
  const aiGroups = [
    toggle('ai_person', 'osoba'),
    toggle('ai_vehicle', 'vozilo (auto, motor, bicikl…)'),
    toggle('ai_animal', 'životinja (pas, mačka, ptica…)'),
  ];
  aiGroups[1].input.checked = c.ai_vehicle ?? false;
  aiGroups[2].input.checked = c.ai_animal ?? false;
  const aiScore = el('input', { type: 'range', class: 'form-range', min: 25, max: 95, step: 5,
    value: Math.round((c.ai_min_score ?? 0.5) * 100), 'aria-label': 'Minimalna sigurnost' });
  const aiScoreVal = el('span', { class: 'small text-body-secondary' });
  const showScore = () => { aiScoreVal.textContent = `minimalna sigurnost ${aiScore.value}%`; };
  aiScore.addEventListener('input', showScore);
  showScore();
  const aiSub = el('div', { class: 'ms-3' }, ...aiGroups.map(t => t.box), aiScoreVal, aiScore);
  const showAi = () => aiSub.classList.toggle('opacity-25', !aiOn.input.checked);
  aiOn.input.addEventListener('change', showAi);
  showAi();

  const motionBox = el('div', { class: 'mt-1', style: 'min-width: 14rem' }, sensVal, sens,
    ...motionToggles.map(t => t.box), el('div', { class: 'border-top mt-1 pt-1' }, aiOn.box, aiSub), zoneBtn);
  const modeCell = el('div', {}, mode, el('div', { class: 'mt-1' }, audio.box), motionBox);

  const showSched = () => {
    sched.classList.toggle('opacity-25', mode.value !== 'schedule');
    motionBox.classList.toggle('d-none', mode.value !== 'motion');
  };
  mode.addEventListener('change', showSched);
  showSched();

  const stateCell = el('td', {});
  statusCells[c.slug] = s => {
    let state;
    if (!s.recorder_alive) state = el('span', { class: 'badge text-bg-warning' }, 'snimač ne radi');
    else if (s.active) state = el('span', { class: 'badge text-bg-danger' }, `snima (${REASONS[s.reason] || s.reason})`);
    else state = el('span', { class: 'badge text-bg-secondary' }, 'ne snima');
    const parts = [state];
    if (s.mode === 'motion') {
      // largest moving group relative to what triggers recording: a full bar means motion
      const pct = s.motion_level == null ? 0 : Math.min(100, s.motion_level * 100);
      parts.push(el('div', { class: 'progress mt-1', style: 'height: 6px', title: 'Pokret u odnosu na prag za snimanje' },
        el('div', { class: `progress-bar ${pct >= 100 ? 'bg-danger' : 'bg-info'}`, style: `width: ${Math.max(1, pct)}%` })));
      zones.live(s);
      parts.push(el('div', { class: 'small text-body-secondary' },
        s.last_motion_at ? `poslednji pokret ${new Date(s.last_motion_at).toLocaleTimeString('sr-Latn-RS')}` : 'još nema pokreta'));
      if (s.ai_enabled && s.ai_last) {
        parts.push(el('div', { class: 'small text-body-secondary' },
          `AI: ${s.ai_last} (${new Date(s.ai_last_at).toLocaleTimeString('sr-Latn-RS')})`));
      }
    }
    if (s.error) parts.push(el('div', { class: 'small text-danger' }, s.error));
    stateCell.replaceChildren(...parts);
  };
  statusCells[c.slug](c);
  const err = el('div', { class: 'small text-danger' });

  const save = el('button', { class: 'btn btn-sm btn-primary' }, 'Sačuvaj');
  save.onclick = async () => {
    err.textContent = '';
    const body = {
      mode: mode.value, schedule_start: start.value || null, schedule_end: end.value || null,
      schedule_days: [...days.querySelectorAll('input:checked')].map(x => x.value).join(''),
      retention_days: ret.value ? Number(ret.value) : null,
      motion_sensitivity: Number(sens.value),
      motion_mask: zones.value(),
      ...Object.fromEntries([audio, ...motionToggles, aiOn, ...aiGroups].map(t => [t.key, t.input.checked])),
      ai_min_score: Number(aiScore.value) / 100,
    };
    try { await api(`/api/recording/cameras/${c.slug}`, { method: 'PUT', body: JSON.stringify(body) }); save.textContent = 'Sačuvano ✓'; setTimeout(() => { save.textContent = 'Sačuvaj'; }, 2000); }
    catch (e) { err.textContent = e.message; }
  };

  const row = el('tr', {}, el('td', {}, c.name), el('td', {}, modeCell), el('td', {}, sched), el('td', {}, ret),
    stateCell, el('td', {}, save, err));
  return [row, zoneRow];
}

async function loadCams() {
  const cams = await api('/api/recording/cameras');
  tbody.replaceChildren(...cams.flatMap(camRow));
}

await Promise.all([loadStorage(), loadCams()]);
// status only; rows are not rebuilt so edits in progress survive
setInterval(async () => { try { renderStatus(await api('/api/recording/storage')); } catch {} }, 15000);
setInterval(async () => {
  try { for (const s of await api('/api/recording/cameras')) statusCells[s.slug]?.(s); } catch {}
}, 3000);
