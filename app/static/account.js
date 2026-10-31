import { api } from './common.js';

const form = document.getElementById('pw');
const msg = document.getElementById('msg');
const say = (t, ok) => { msg.textContent = t; msg.className = 'small ' + (ok ? 'text-success' : 'text-danger'); };

form.addEventListener('submit', async ev => {
  ev.preventDefault();
  const cur = document.getElementById('cur').value;
  const nw = document.getElementById('new').value;
  if (nw !== document.getElementById('new2').value) { say('Nove lozinke se ne poklapaju.', false); return; }
  try {
    await api('/api/me/password', { method: 'POST', body: JSON.stringify({ current: cur, new: nw }) });
    form.reset();
    say('Lozinka je promenjena.', true);
    setTimeout(() => { location.href = '/'; }, 1200);
  } catch (e) { say(e.message, false); }
});
