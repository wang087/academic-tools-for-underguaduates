/* Evaluated with a lexical `safety` supplied by cdp.py. */
(() => {
  const KEY = '__cdpConsentPolicyV2';
  if (window[KEY]) return window[KEY].run();
  const BOXES = '#onetrust-banner-sdk,#onetrust-pc-sdk,#CybotCookiebotDialog,#qc-cmp2-container,#qc-cmp2-ui,.osano-cm-window,#didomi-host,#didomi-popup,#didomi-notice,.didomi-notice-popup,#truste-consent-content,#consent_blackbar,#usercentrics-root,#usercentrics-cmp-ui,#uc-center-container,[id^="sp_message_container"],#cookiescript_injected,#cookie-law-info-bar,#cookiebanner,.cookie-banner,#cookie-banner,.cookie-notice,#cookie-notice,#cookiebar,.cookiebar,#cookie-bar,.cookie-bar,.cc-window,.cc-banner,#gdpr-consent-tool-wrapper,.cky-consent-container,.cky-preference-center,.cmplz-cookiebanner,#cmplz-cookiebanner-container,#iubenda-cs-banner,.iubenda-cs-container,#moove_gdpr_cookie_info_bar,#hs-eu-cookie-confirmation,#BorlabsCookieBox,.eu-cookie-compliance-banner,#cookie-consent,.cookie-consent,.cookie-consent-banner,#cookieConsent,[role="dialog"][aria-label*="cookie" i]';
  const ACCEPT = /^(accept(?: all| cookies| all cookies| necessary cookies| selected cookies)?|allow (?:all|cookies|all cookies)|agree(?: to all)?|i agree|同意(?:全部|所有)?|接受(?:全部|所有)?|允许(?:全部|所有)?|同意并继续|接受并继续)$/i;
  const AMBIGUOUS = /^(ok(?:ay)?|确认|确定|知道了|got it|i understand)$/i;
  const evidence = /cookies?|cookie policy|饼干|Cookie|隐私.*(?:同意|政策)|(?:同意|接受).*(?:隐私|数据)/i;
  const records = [], attempted = new WeakSet();
  function roots(root) {
    const out = [root];
    if (root.shadowRoot) out.push(root.shadowRoot);
    for (let i = 0; i < out.length && out.length < 24; i++) {
      for (const e of out[i].querySelectorAll('*')) if (e.shadowRoot && out.length < 24) out.push(e.shadowRoot);
    }
    return out;
  }
  // A zero-size shadow host can contain a rendered fixed-position banner.
  // Inspect only its own descendants/shadow trees, never root siblings.
  function rendered(box) {
    if (!box || !box.isConnected) return false;
    if (!safety.hidden(box)) return true;
    for (const root of roots(box)) for (const child of root.querySelectorAll('*')) {
      if (!safety.hidden(child)) return true;
    }
    return false;
  }
  function report(block) {
    for (const r of records) if (r.clicked && !r.verified && !rendered(r.box)) {
      r.verified = true; r.evidence = 'container_disappeared';
    }
    const items = records.map(r => ({container: r.container, label: r.label, attempted: true, clicked: r.clicked, verified: r.verified, blocked: !!r.reason, reason: r.reason || '', evidence: r.evidence || '', status: r.verified ? 'verified' : r.reason ? 'blocked' : r.clicked ? 'clicked' : 'attempted'}));
    const counts = {attempted: items.length, clicked: items.filter(r=>r.clicked).length, verified: items.filter(r=>r.verified).length, blocked: items.filter(r=>r.blocked).length};
    return {policy_version: 2, ...counts, unchanged: counts.verified === 0, status: block ? 'blocked' : counts.verified ? 'verified' : counts.clicked ? 'clicked' : counts.blocked ? 'blocked' : 'unchanged', reason: block || '', manual_required: !!block, items};
  }
  function run() {
    const block = safety.challenge();
    if (block) return report(block);
    const seen = new Set();
    for (const root of roots(document)) for (const box of root.querySelectorAll(BOXES)) {
      if (seen.size >= 60) break;
      if (seen.has(box)) continue;
      seen.add(box);
      if (box.tagName === 'IFRAME' || !rendered(box)) continue;
      const container = box.id || box.className || box.tagName;
      // Search descendants only; never search the box's surrounding shadow root.
      for (const inner of roots(box)) for (const b of inner.querySelectorAll('button,a[role=button],input[type=button],input[type=submit],[role=button]')) {
        const label = safety.norm(b.innerText || b.value || b.getAttribute('aria-label') || b.textContent).replace(/[\s»→›]+$/, '');
        if (!ACCEPT.test(label) || AMBIGUOUS.test(label)) continue;
        if (attempted.has(box)) break;
        const reason = safety.check(b);
        const r = {box, container: String(container), label, clicked: false, verified: false, reason};
        records.push(r); attempted.add(box);
        if (!reason) {
          try { b.click(); r.clicked = true; }
          catch (e) { r.reason = 'click_error'; }
        }
        break;
      }
    }
    return report('');
  }
  Object.defineProperty(window, KEY, {value: {run}, configurable: true});
  const first = run();
  let n = 0;
  const timer = setInterval(() => { run(); if (++n >= 25) clearInterval(timer); }, 1200);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run, {once: true});
  return first;
})()
