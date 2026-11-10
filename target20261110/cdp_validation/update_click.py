from pathlib import Path
p = Path(__file__).resolve().parents[2] / 'scripts' / 'cdp.py'
s = p.read_text(encoding='utf-8')
a = s.index('# ── Cookie / 同意书弹窗')
b = s.index('\n\nclass CDP:', a)
s = s[:a] + '''# Conservative cookie policy; kept as a string for new-document injection.
from pathlib import Path as _PolicyPath
CLICK_SAFETY_JS = (_PolicyPath(__file__).with_name("click_safety.js")).read_text(encoding="utf-8")
CONSENT_JS = "(() => { const safety = " + CLICK_SAFETY_JS + "; return " + (
    _PolicyPath(__file__).with_name("consent_logic.js")).read_text(encoding="utf-8") + "; })()"
''' + s[b:]
a = s.index('    def dismiss_consent(')
b = s.index('    def get_text(', a)
s = s[:a] + '''    def dismiss_consent_report(self, timeout: float = 8.0) -> dict:
        """Return observed DOM outcome; a dispatched click is not success.

        Counts are cumulative within this document. No layer is hidden or removed
        by this policy. verified requires the original banner to disappear.
        """
        deadline = time.monotonic() + max(0.0, timeout)
        report = {"status": "unchanged", "attempted": 0, "clicked": 0,
                  "verified": 0, "blocked": 0, "unchanged": True,
                  "manual_required": False, "items": []}
        while time.monotonic() < deadline:
            try:
                result = self.eval(CONSENT_JS, timeout=max(0.001, deadline - time.monotonic()))
                if isinstance(result, dict):
                    report = result
                if report.get("verified") or report.get("manual_required") or not report.get("clicked"):
                    break
            except CDPError as exc:
                report["error"] = str(exc)
                break
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        self._consent_report = report
        return report

    def get_consent_report(self, timeout: float = 8.0) -> dict:
        return self.dismiss_consent_report(timeout=timeout)

    def dismiss_consent(self, timeout: float = 8.0) -> bool:
        """Legacy bool: true only for an observed banner disappearance."""
        return bool(self.dismiss_consent_report(timeout).get("verified", 0))

''' + s[b:]
p.write_text(s, encoding='utf-8')
