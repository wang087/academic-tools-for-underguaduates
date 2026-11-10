/* Pure page-side category enumeration. Called with cfg, action and identity. */
(() => {
  const nodes = () => Array.from(document.querySelectorAll(cfg.click_selector));
  function identities() {
    const seen = new Map();
    const out = nodes().map(e => {
      const text = safety.norm(e.getAttribute('title') || e.textContent);
      const data_key = e.getAttribute('data-key') || '';
      const href = e.getAttribute('href') || '';
      const base = JSON.stringify([data_key, href, text]);
      const occurrence = seen.get(base) || 0; seen.set(base, occurrence + 1);
      return {text, data_key, href, occurrence, key: base + ':' + occurrence};
    });
    const counts = new Map();
    out.forEach(i => counts.set(i.text, (counts.get(i.text) || 0) + 1));
    out.forEach(i => i.repeated_name = counts.get(i.text) > 1);
    return out;
  }
  function locate(id) {
    const list = identities(), all = nodes();
    const index = list.findIndex(i => i.key === id.key);
    return index < 0 ? null : all[index];
  }
  function snapshot() {
    const items = Array.from(document.querySelectorAll(cfg.card_selector)).map(li => {
      const a = li.querySelector('a[href]');
      const nm = li.querySelector('.teacher-name,.name,h3,h4,.tit') || a;
      return {name: safety.norm(nm && nm.textContent).replace(/\s+/g, ''), href: a && /^https?:/i.test(a.href) ? a.href : '', title: a && a.getAttribute('title') || ''};
    });
    const state = cfg.completion_selector ? document.querySelector(cfg.completion_selector) : null;
    return {items, signature: JSON.stringify(items), state: state ? {key: state.getAttribute('data-category-key') || '', state: state.getAttribute('data-state') || '', token: state.getAttribute('data-load-token') || ''} : null, challenge: safety.challenge()};
  }
  const challenge = safety.challenge();
  if (challenge) return {challenge, blocked: true, reason: challenge, manual_required: true};
  if (action === 'snapshot') return snapshot();
  if (action === 'list') return {categories: identities()};
  const e = locate(identity);
  const reason = safety.check(e);
  if (reason) return {attempted: true, clicked: false, blocked: true, reason};
  if (action === 'click') {
    const before = snapshot();
    try { e.click(); return {attempted: true, clicked: true, blocked: false, before}; }
    catch (err) { return {attempted: true, clicked: false, blocked: true, reason: 'click_error'}; }
  }
  return {attempted: false, clicked: false, reason: 'unknown_action'};
})()
