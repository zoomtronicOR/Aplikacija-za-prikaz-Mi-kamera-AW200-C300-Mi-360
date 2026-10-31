import { api } from './common.js';
import { mountCamera } from './camera_ui.js';

function addCamera(grid, cam) {
  const card = document.getElementById('cam-card').content.firstElementChild.cloneNode(true);
  card.querySelector('.name-link').href = `/camera/${encodeURIComponent(cam.slug)}`;
  grid.append(card);
  mountCamera(card, cam);
}

const grid = document.getElementById('grid');
await customElements.whenDefined('video-stream');
const cams = await api('/api/cameras');
const byslug = {};
for (const cam of cams) { cam.role_admin = 'did' in cam; byslug[cam.slug] = cam; addCamera(grid, cam); }

// refresh reachability and recording state without re-creating players
setInterval(async () => {
  try {
    for (const c of await api('/api/cameras')) {
      const cam = byslug[c.slug];
      if (!cam) continue;
      cam.online = c.online;
      cam.rec = c.rec;
      cam.renderRec();
    }
  } catch {}
}, 10000);
