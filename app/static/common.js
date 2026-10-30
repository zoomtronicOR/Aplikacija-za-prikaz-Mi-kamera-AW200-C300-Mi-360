export async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  if (r.status === 401) { location.href = '/login'; throw new Error('Niste prijavljeni'); }
  if (r.status === 204) return {};
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = data.detail;
    // FastAPI validation errors come as a list
    throw new Error(Array.isArray(d) ? d.map(x => x.msg).join('; ') : d || `Greška ${r.status}`);
  }
  return data;
}

export function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith('on')) e.addEventListener(k.slice(2), v); else if (v !== undefined) e.setAttribute(k, v);
  }
  e.append(...children);
  return e;
}
