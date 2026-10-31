import { api, el } from './common.js';
import { mountCamera } from './camera_ui.js';

const root = document.getElementById('cam-root');
const slug = root.dataset.slug;
const REASONS = { manual: 'ručno', continuous: 'stalno', schedule: 'raspored', motion: 'pokret', recovered: 'oporavljeno' };

await customElements.whenDefined('video-stream');
const cam = (await api('/api/cameras')).find(c => c.slug === slug);
if (!cam) {
  root.replaceChildren(el('div', { class: 'alert alert-warning' }, 'Kamera ne postoji ili je isključena. ', el('a', { href: '/' }, 'Nazad na sve kamere')));
  throw new Error('no camera');
}
cam.role_admin = 'did' in cam;
document.title = cam.name;
const { ptz } = mountCamera(root, cam, { controlsOpen: true });

// snapshot of the stream currently watched (HD or SD)
const snap = root.querySelector('.btn-snap');
if (!cam.can_control) snap.remove();
else snap.addEventListener('click', () => {
  snap.href = `/api/cameras/${slug}/snapshot?quality=${cam.quality}&download=1`;
});

// keyboard: arrows pan/tilt while held, F toggles fullscreen
const KEYS = { ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down' };
const typing = () => ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName);
if (ptz) root.querySelector('.kbd-help').classList.remove('d-none');
document.addEventListener('keydown', ev => {
  if (typing() || ev.ctrlKey || ev.metaKey || ev.altKey) return;
  if (ptz && KEYS[ev.key]) { ev.preventDefault(); ptz.start(KEYS[ev.key]); }
  else if (ev.key === 'f' || ev.key === 'F') root.querySelector('.btn-fs').click();
});
document.addEventListener('keyup', ev => { if (ptz && KEYS[ev.key]) ptz.stop(); });
window.addEventListener('blur', () => ptz?.stop());

// latest recordings of this camera
const recent = root.querySelector('.recent');
root.querySelector('.all-recs').href = `/recordings?camera=${encodeURIComponent(slug)}`;
async function loadRecent() {
  try {
    const { items } = await api(`/api/recordings?camera=${encodeURIComponent(slug)}&limit=5`);
    if (!items.length) { recent.textContent = 'Još nema snimaka.'; return; }
    recent.replaceChildren(el('ul', { class: 'list-unstyled mb-0' }, ...items.map(r => el('li', {},
      el('a', { href: `/recordings?camera=${encodeURIComponent(slug)}&play=${r.id}` },
        new Date(r.started_at).toLocaleString('sr-Latn-RS', { dateStyle: 'short', timeStyle: 'short' })),
      ` · ${Math.round(r.duration_s)} s · ${REASONS[r.reason] || r.reason}`))));
  } catch (e) { recent.textContent = e.message; }
}
loadRecent();

setInterval(async () => {
  try {
    const c = (await api('/api/cameras')).find(x => x.slug === slug);
    if (!c) return;
    cam.online = c.online; cam.rec = c.rec; cam.renderRec();
  } catch {}
}, 10000);
setInterval(loadRecent, 60000);
