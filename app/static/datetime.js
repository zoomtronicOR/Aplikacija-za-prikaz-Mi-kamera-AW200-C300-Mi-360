// Serbian date and time format for every <input type="date|time">, including inputs created later.
// Native pickers follow the browser's language (e.g. mm/dd/yyyy, AM/PM). The input keeps its standard
// value (yyyy-mm-dd, HH:MM), so page code reads and sets .value as before.
(() => {
  if (!window.flatpickr) return;
  const valueProp = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
  const opts = {
    date: { dateFormat: 'Y-m-d', altInput: true, altFormat: 'd.m.Y.' },
    time: { enableTime: true, noCalendar: true, time_24hr: true, dateFormat: 'H:i', altInput: true, altFormat: 'H:i' },
  };

  const enhance = (input) => {
    const kind = input.getAttribute('type');
    if (!opts[kind] || input._flatpickr) return;
    const fp = flatpickr(input, { ...opts[kind], locale: 'sr', disableMobile: true, allowInput: false });
    fp.altInput.className = input.className;
    if (input.style.cssText) fp.altInput.style.cssText = input.style.cssText;
    // a text field fills its flex row, unlike the native time input; keep it as narrow as that one
    if (kind === 'time' && !input.style.width) fp.altInput.style.width = '4.5rem';
    const label = input.getAttribute('aria-label');
    if (label) fp.altInput.setAttribute('aria-label', label);
    // page code sets .value directly; keep the visible field in step
    let syncing = false;
    Object.defineProperty(input, 'value', {
      configurable: true,
      get() { return valueProp.get.call(this); },
      set(v) {
        valueProp.set.call(this, v);
        if (!syncing) { syncing = true; try { fp.setDate(v || null, false); } finally { syncing = false; } }
      },
    });
  };

  const scan = (root) => {
    if (root.matches?.('input[type=date], input[type=time]')) enhance(root);
    root.querySelectorAll?.('input[type=date], input[type=time]').forEach(enhance);
  };
  const start = () => {
    scan(document.body);
    new MutationObserver((muts) => muts.forEach((m) => m.addedNodes.forEach(scan)))
      .observe(document.body, { childList: true, subtree: true });
  };
  if (document.body) start(); else document.addEventListener('DOMContentLoaded', start);
})();
