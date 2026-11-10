/* Pure page-side policy. No Input.*, scrolling, DOM removal or fingerprint changes. */
(() => {
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim();
  const composedParent = e => e.parentElement || (e.getRootNode && e.getRootNode().host);
  function hidden(e) {
    if (!e || !e.isConnected) return true;
    for (let p = e; p; p = composedParent(p)) {
      const s = getComputedStyle(p);
      if (p.hidden || s.display === 'none' || /hidden|collapse/.test(s.visibility) || Number(s.opacity) === 0) return true;
    }
    const r = e.getBoundingClientRect();
    return r.width <= 0 || r.height <= 0;
  }
  function challenge() {
    const pattern = /captcha|recaptcha|hcaptcha|turnstile|cf-challenge|challenge-platform|verify.{0,20}(human|identity)|人机验证|安全验证|验证码|验证身份/i;
    if (/just a moment|verify you are human|checking your browser|安全验证|人机验证/i.test(document.title || '')) return 'challenge_title';
    const roots = [document];
    for (let i = 0; i < roots.length && roots.length < 24; i++) {
      for (const e of roots[i].querySelectorAll('*')) if (e.shadowRoot && roots.length < 24) roots.push(e.shadowRoot);
      for (const e of roots[i].querySelectorAll('iframe, [id], [class], [role=dialog]')) {
        if (pattern.test([e.id, e.className, e.getAttribute('src'), e.getAttribute('title'), e.getAttribute('aria-label')].join(' ')) && !hidden(e)) return 'challenge_element';
      }
    }
    if (/verify you are human|complete the security check|请完成.{0,8}验证|请验证您是/i.test((document.body && document.body.innerText) || '')) return 'challenge_text';
    return '';
  }
  function hit(e, x, y) {
    let top = document.elementFromPoint(x, y);
    for (let n = 0; top && top.shadowRoot && n < 10; n++) {
      const inner = top.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === top) break;
      top = inner;
    }
    for (let p = top; p; p = composedParent(p)) if (p === e) return true;
    return false;
  }
  function check(e) {
    if (!e || !e.isConnected) return 'missing';
    const danger = /captcha|challenge|verify|verification|验证|校验|download|下载|delete|删除|支付|付款|pay(?:ment)?|purchase|checkout|submit|提交|logout|sign.?out/i;
    if (danger.test([e.textContent, e.getAttribute('aria-label'), e.id, e.className, e.getAttribute('href'), e.getAttribute('onclick')].join(' '))) return 'dangerous';
    for (let p = e; p; p = composedParent(p)) {
      if (p.inert || p.hasAttribute('inert')) return 'inert';
      if (p.disabled || p.getAttribute('aria-disabled') === 'true') return 'disabled';
    }
    if (hidden(e)) return 'hidden';
    if (getComputedStyle(e).pointerEvents === 'none') return 'pointer_events_none';
    if (e.matches('input[type=submit], input[type=image], button[type=submit]') || (e.tagName === 'BUTTON' && e.form && !e.hasAttribute('type'))) return 'submit';
    if (e.hasAttribute('download') || e.target && e.target !== '_self') return 'dangerous_link';
    const raw = (e.getAttribute('href') || '').trim();
    if (raw && !/^javascript:\s*(?:void\s*\(\s*0\s*\)|;)\s*;?$/i.test(raw)) {
      try { const u = new URL(raw, location.href), here = new URL(location.href);
        if (!/^https?:$/.test(u.protocol) || u.origin !== here.origin || u.pathname !== here.pathname || u.search !== here.search) return 'dangerous_link';
      } catch (_) { return 'dangerous_link'; }
    }
    const r = e.getBoundingClientRect();
    const left = Math.max(0, r.left), right = Math.min(innerWidth, r.right), top = Math.max(0, r.top), bottom = Math.min(innerHeight, r.bottom);
    if (right <= left || bottom <= top) return 'offscreen';
    if (!hit(e, (left + right) / 2, (top + bottom) / 2)) return 'covered';
    return '';
  }
  return {norm, hidden, check, challenge};
})()
