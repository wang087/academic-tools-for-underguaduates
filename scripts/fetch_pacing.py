# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 同站点节流 + 站点级熔断 + 熔断状态持久化（可复用模块）
#
# 为什么需要这个模块（对照报告 §1.5 的三条落差）
#   1. **节流**：既有实现有 `pace()`（download_all_fulltexts.py:650-659）与
#      `HostPacer`（fetch_oa_direct.py:119-137），但它们是**各自一份**的私有实现，
#      新接入的路线（出版商 API / OA 候选）无从复用。政策上「同站点必须拉开间隔」
#      是**反封禁红线**，不该只有某一处实现。
#   2. **熔断**：既有 `SiteGuard`（download_all_fulltexts.py:104-148）只在**进程内**，
#      跨轮不复用 —— 于是新一轮会重新撞同一堵墙。README.md:811 承诺的
#      「永久跳过」在代码里**没有对应实现**（对照报告 §7.2 第 9 条）。
#   3. **状态文件**：新增模块若自建一份 `.work_cdp/*.json`，就会与既有状态漂移
#      （docs/阶段R全文获取模块.md 六 的教训就是「工作目录判据必须复用，不能各算各的」）。
#      所以本模块**不新立文件**：它读写既有 `fulltext_state.json` 里的
#      `_sites` 保留键（键名以 `_` 开头，既有代码逐 DOI 遍历，不会碰它）。
#
# [STOP] 三条不变量（与既有实现一致，改动不得破坏）
#   ① 同站点**严格串行**：`GUARD.lock(site)` 必须包住「判定→节流→执行→记账」。
#   ② 失败计数**必须在站点锁内**更新 —— 否则后来者读到过期值，
#      实测 limit=2 时会放进去 3 个（download_all_fulltexts.py:671-677）。
#   ③ 节流间隔按 **host** 计，不是全局 —— 出版社 WAF 是按 host 计数的
#      （fetch_oa_direct.py:122-124 的实测教训）。
#
# 用法：
#   from fetch_pacing import PacerGuard
#   pg = PacerGuard()                       # 自动加载持久化熔断状态
#   site = pg.site_of_doi("10.1016/j.cell.2014.03.011")   # → "sciencedirect.com"
#   with pg.lock(site):
#       if pg.blocked(site):
#           ...  # 本轮跳过
#       pg.pace(site)                       # 同站点最小间隔
#       res = do_request(...)
#       pg.record(site, ok=res.ok, reason=res.reason)   # 锁内记账
#   pg.save()                               # 落盘熔断状态
#
#   uv run python scripts/fetch_pacing.py --self-test
#   uv run python scripts/fetch_pacing.py --status
#   uv run python scripts/fetch_pacing.py --clear <site>
#
# 退出码：0 = 通过；1 = 自检失败；2 = 用法错误；3 = 指定站点当前处于熔断
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import json
import os
import pathlib
import random
import re
import sys
import threading
import time

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _fix_streams() -> None:
    """UTF-8 兜底（幂等；原因见 refs_backfill.py 同名函数）。"""
    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        buf = getattr(s, "buffer", None)
        if buf is None:
            continue
        enc = (getattr(s, "encoding") or "").lower()
        if enc.replace("-", "") in ("utf8", "utf8mb4", "cp65001"):
            continue
        try:
            setattr(sys, name, io.TextIOWrapper(buf, encoding="utf-8", errors="replace"))
        except Exception:
            pass


_fix_streams()


# ══════════════════════════════════════════════════════════════
# 默认间隔表
#
# [STOP] 这里给出的数字全部来自**实测记录**，不是拍脑袋：
#   · 25-55s 是既有缺省（download_all_fulltexts.py:760）
#   · 120-240s 是 Elsevier/AACR 实测需要的档位
#     （docs/阶段R全文获取模块.md:210-224：10.1158 从熔断变成功）
#   · 15-35s 给结构化 API（Crossref/Unpaywall/EPMC）——
#     它们是 polite pool，间隔要求远低于出版社内容站
# ══════════════════════════════════════════════════════════════
DEFAULT_INTERVAL = (25.0, 55.0)

# 站点键 → 间隔表的别名
#
# [STOP] 为什么需要别名：既有 `detect.site_of_doi()` 用的是**出版社组**键
# （10.1016 → "elsevier"、10.1038 → "nature.com"、10.1002/10.1042/10.1111 → "wiley"），
# 而 `detect.site_of(url)` 用的是**落地域名**。两者指同一批站点但键名不同。
# 别名表把它们映射到同一档间隔，避免「按 DOI 推定站点时拿不到专用档位」。
INTERVAL_ALIAS: dict[str, str] = {
    "elsevier": "sciencedirect.com",
    "wiley": "onlinelibrary.wiley.com",
    "nature.com": "www.nature.com",
    "ieeexplore.ieee.org": "ieeexplore.ieee.org",
    "aacrjournals.org": "aacrjournals.org",
}

HOST_INTERVALS: dict[str, tuple[float, float]] = {
    # 结构化元数据 API（polite pool）
    "api.crossref.org": (5.0, 12.0),
    "api.openalex.org": (5.0, 12.0),
    "api.unpaywall.org": (5.0, 12.0),
    "www.ebi.ac.uk": (5.0, 12.0),
    "europepmc.org": (5.0, 12.0),
    "eutils.ncbi.nlm.nih.gov": (5.0, 12.0),
    "pub.orcid.org": (5.0, 12.0),
    "export.arxiv.org": (10.0, 20.0),
    # 出版商官方 API（授权接口，但仍要克制）
    "api.elsevier.com": (10.0, 25.0),
    "spdi.public.springernature.app": (10.0, 25.0),
    # 出版社内容站 —— 反封禁重灾区
    "sciencedirect.com": (120.0, 240.0),
    "cell.com": (120.0, 240.0),
    "linkinghub.elsevier.com": (120.0, 240.0),
    "aacrjournals.org": (120.0, 240.0),
    "jimmunol.org": (120.0, 240.0),
    "onlinelibrary.wiley.com": (25.0, 55.0),
    "academic.oup.com": (25.0, 55.0),
    "www.nature.com": (25.0, 55.0),
    "link.springer.com": (25.0, 55.0),
    "pubs.acs.org": (25.0, 55.0),
    "pubs.rsc.org": (25.0, 55.0),
    "journals.sagepub.com": (25.0, 55.0),
    "tandfonline.com": (25.0, 55.0),
    "ieeexplore.ieee.org": (60.0, 120.0),
    "jbc.org": (120.0, 240.0),
    "pnas.org": (25.0, 55.0),
    "science.org": (120.0, 240.0),
    "www.ncbi.nlm.nih.gov": (15.0, 30.0),
    "pmc.ncbi.nlm.nih.gov": (15.0, 30.0),
}

# DOI 前缀 → 站点：**只补 detect._DOI_PREFIX_SITE 未覆盖的**。
#   [STOP] 不重写 detect 已有的映射 —— 两份重复映射必然漂移。
#   冲突由 `--self-test` 的漂移断言拦住（见 self_test 第 2 步）。
DOI_PREFIX_SITE_SUPPLEMENT: dict[str, str] = {
    "10.1158": "aacrjournals.org",       # AACR 系（Cancer Discovery 等）
    "10.4049": "jimmunol.org",           # The Journal of Immunology
    "10.1109": "ieeexplore.ieee.org",    # IEEE Xplore
    "10.1136": "bmj.com",                # BMJ
    "10.1001": "jamanetwork.com",        # JAMA
    "10.1056": "nejm.org",               # NEJM
    "10.1016": "elsevier",               # 显式写出，便于漂移断言比较
    "10.1038": "nature.com",
    "10.1007": "link.springer.com",
    "10.1186": "link.springer.com",
    "10.1111": "wiley",
    "10.1002": "wiley",
    "10.1042": "wiley",
}

# 状态文件里的保留键（既有代码逐 DOI 遍历，不会碰以 _ 开头的键）
STATE_KEY = "_sites"
STATE_FILE = "fulltext_state.json"


# ══════════════════════════════════════════════════════════════
# 站点推断
# ══════════════════════════════════════════════════════════════
def host_of_url(url: str) -> str:
    try:
        import urllib.parse as _up  # noqa: PLC0415
        return (_up.urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def normalize_doi(doi: str) -> str:
    """去掉 URL / `doi:` 前缀与首尾空白。**不改写** DOI 后缀大小写。"""
    d = str(doi or "").strip()
    d = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", d, flags=re.I)
    d = re.sub(r"^doi:\s*", "", d, flags=re.I)
    return d.strip()


def doi_prefix(doi: str) -> str:
    """→ "10.1016" 这样的前缀。修复：「URL 前缀未先剥离」会得到 "https:"。"""
    d = normalize_doi(doi)
    return d.split("/", 1)[0].lower().split("(")[0].strip()


def site_of_doi(doi: str) -> str:
    """DOI → 站点标识。

    [STOP] 为什么按 **DOI 前缀** 而不是落地页 host：
    `https://doi.org/<doi>` 会把所有 DOI 显示成同一个 host（doi.org），
    按落地页 host 分组会让全部论文塌缩成一个站点，节流随之失效
    （detect.py:316-318 有专门说明）。

    优先级：
      1. `DOI_PREFIX_SITE_SUPPLEMENT`（本模块补的：AACR / J Immunol / IEEE / NEJM 等）
      2. 既有 `detect.site_of_doi()`（**复用，不重写**）
      3. 退化：DOI 前缀本身当站点键
    """
    d = normalize_doi(doi)
    if not d:
        return "unknown"
    p = doi_prefix(d)
    if p in DOI_PREFIX_SITE_SUPPLEMENT:
        return DOI_PREFIX_SITE_SUPPLEMENT[p]
    try:
        import detect  # noqa: PLC0415
        s = detect.site_of_doi(d)
        if s:
            return str(s)
    except Exception:  # noqa: BLE001
        pass
    return p or "unknown"


# ══════════════════════════════════════════════════════════════
# 节流 + 熔断
# ══════════════════════════════════════════════════════════════
class PacerGuard:
    """同站点节流 + 站点级熔断 + 熔断状态持久化。

    线程安全模型与既有 SiteGuard 一致：
      · `lock(site)` 返回**该站点专属**的锁；
      · 调用方必须把「阻塞判定 + pace + 请求 + 记账」整段放进 `with lock(site)`；
      · 这样同站点严格串行，且记账对后来者立即可见。
    """

    def __init__(self, limit: int = 2, *, intervals: dict | None = None,
                 default_interval: tuple[float, float] | None = None,
                 wd: pathlib.Path | None = None, log=None,
                 rng: random.Random | None = None,
                 clock=time.time, sleeper=time.sleep,
                 persist: bool = True):
        self.limit = max(1, int(limit))
        self.intervals = dict(HOST_INTERVALS)
        if intervals:
            self.intervals.update({str(k).lower(): tuple(v) for k, v in intervals.items()})
        self.default_interval = default_interval or DEFAULT_INTERVAL
        self.wd = pathlib.Path(wd) if wd else None
        self.log = log
        self.rng = rng or random.Random()
        self.clock = clock
        self.sleeper = sleeper
        self.persist = persist

        self._locks: dict[str, threading.Lock] = {}
        self._lk = threading.Lock()
        self._last: dict[str, float] = {}
        # 本轮内存计数
        self.hits: dict[str, int] = {}
        # 本轮最后受阻原因（落盘时带上，便于事后定位）
        self.reasons: dict[str, str] = {}
        # 跨轮持久计数（从 state 载入）
        self.persisted: dict[str, dict] = {}
        if persist:
            self._load()

    # ── 锁 ─────────────────────────────────────────────────
    def lock(self, site: str) -> threading.Lock:
        with self._lk:
            return self._locks.setdefault(str(site), threading.Lock())

    # ── 持久化 ─────────────────────────────────────────────
    def state_path(self) -> pathlib.Path | None:
        if self.wd is None:
            return None
        return pathlib.Path(self.wd) / ".work_cdp" / STATE_FILE

    def _load(self) -> None:
        p = self.state_path()
        if not p or not p.is_file():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return
        blk = data.get(STATE_KEY) if isinstance(data, dict) else None
        if isinstance(blk, dict):
            self.persisted = {str(k): v for k, v in blk.items() if isinstance(v, dict)}

    def save(self) -> pathlib.Path | None:
        """把熔断状态写回既有状态文件。

        [STOP] 必须**读-改-写**，不能整文件覆盖：
        `fulltext_state.json` 同时存着逐篇下载结果（既有脚本在读）。
        覆盖它等于把别的脚本的进度抹掉。
        """
        p = self.state_path()
        if not p or not self.persist:
            return None
        data: dict = {}
        if p.is_file():
            try:
                v = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(v, dict):
                    data = v
            except Exception:  # noqa: BLE001
                data = {}
        # 合并：已持久化的次数 + 本轮新增次数
        merged = dict(self.persisted)
        for site, n in self.hits.items():
            prev = merged.get(site) or {}
            merged[site] = {
                "count": int(prev.get("count") or 0) + int(n),
                "last_reason": self.reasons.get(site) or prev.get("last_reason", ""),
                "last_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        data[STATE_KEY] = merged
        p.parent.mkdir(parents=True, exist_ok=True)
        temp = p.with_name(f"{p.name}.part-{os.getpid()}")
        try:
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(temp, p)
        finally:
            temp.unlink(missing_ok=True)
        self.persisted = merged
        return p

    # ── 熔断 ───────────────────────────────────────────────
    def blocked(self, site: str) -> bool:
        """本轮是否已熔断。跨轮累计也计入（这正是「持久化」的意义）。"""
        s = str(site)
        return (self.hits.get(s, 0) + int((self.persisted.get(s) or {}).get("count") or 0)) >= self.limit

    def remaining(self, site: str) -> int:
        s = str(site)
        used = self.hits.get(s, 0) + int((self.persisted.get(s) or {}).get("count") or 0)
        return max(0, self.limit - used)

    def record(self, site: str, ok: bool, reason: str = "") -> int:
        """记账。**必须在站点锁内调用**（不变量 ②）。返回本轮累计受阻次数。"""
        s = str(site)
        with self._lk:
            if ok:
                return self.hits.get(s, 0)
            self.hits[s] = self.hits.get(s, 0) + 1
            self.reasons[s] = str(reason or "")[:120]
            n = self.hits[s]
        if self.log:
            total = n + int((self.persisted.get(s) or {}).get("count") or 0)
            try:
                self.log.warn(f"站点 {s} 受阻 {total}/{self.limit}（{reason}）"
                              + ("→ 本轮跳过该站点" if total >= self.limit else ""))
            except Exception:  # noqa: BLE001
                pass
        return n

    def hit(self, site: str, why: str = "") -> int:
        """`record(..., ok=False)` 的别名，便于与既有 SiteGuard 的调用点对接。"""
        return self.record(site, ok=False, reason=why)

    # ── 节流 ───────────────────────────────────────────────
    def interval_for(self, site: str) -> tuple[float, float]:
        """该站点的间隔区间。未知站点用默认（宁可慢，不可打封）。

        查询顺序：原名 → 别名（把 detect 的出版社组键映射到域名键）→ 默认。
        """
        s = str(site).lower()
        if s in self.intervals:
            return self.intervals[s]
        alias = INTERVAL_ALIAS.get(s)
        if alias and alias in self.intervals:
            return self.intervals[alias]
        return self.default_interval

    def pace(self, site: str) -> float:
        """同站点最小间隔（全局串行由调用方的站点锁保证）。返回实际等待秒数。"""
        s = str(site)
        lo, hi = self.interval_for(s)
        gap = self.rng.uniform(lo, hi)
        with self._lk:
            wait = self._last.get(s, 0.0) + gap - self.clock()
            if wait > 0:
                self.sleeper(wait)
            self._last[s] = self.clock()
        if wait > 0 and self.log:
            try:
                self.log.info(f"  [WAIT] 同站点节流 {wait:.0f}s（{s}）")
            except Exception:  # noqa: BLE001
                pass
        return max(0.0, wait)

    # ── 汇总 ───────────────────────────────────────────────
    def summary(self) -> str:
        parts = []
        for k in sorted(set(self.hits) | set(self.persisted)):
            n = self.hits.get(k, 0) + int((self.persisted.get(k) or {}).get("count") or 0)
            if n:
                parts.append(f"{k}x{n}")
        return "；".join(parts) if parts else "无站点触发熔断"

    def status(self) -> dict:
        sites = {}
        for k in sorted(set(self.hits) | set(self.persisted)):
            n = self.hits.get(k, 0) + int((self.persisted.get(k) or {}).get("count") or 0)
            sites[k] = {
                "count": n,
                "this_run": self.hits.get(k, 0),
                "persisted": int((self.persisted.get(k) or {}).get("count") or 0),
                "blocked": n >= self.limit,
                "interval": self.interval_for(k),
                "last_reason": (self.persisted.get(k) or {}).get("last_reason", ""),
            }
        return {"limit": self.limit, "state_file": str(self.state_path() or ""), "sites": sites}


def load_guard(wd: pathlib.Path | None = None, limit: int = 2, log=None) -> PacerGuard:
    """便捷构造：用 `logutil.workdir()` 当工作目录（判据**唯一**，不各算各的）。"""
    if wd is None:
        try:
            import logutil  # noqa: PLC0415
            wd = logutil.workdir()
        except Exception:  # noqa: BLE001
            wd = None
    return PacerGuard(limit=limit, wd=wd, log=log)


# ══════════════════════════════════════════════════════════════
# 自检（离线；注入假时钟与假 sleep，不真的等待）
# ══════════════════════════════════════════════════════════════
class _FakeClock:
    """可控时钟 + 假 sleep：把「等待」变成可断言的数字，自检无需真等。"""

    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(round(s, 3))
        self.t += s


def self_test() -> int:
    bad = 0
    import logutil  # noqa: PLC0415

    scratch = logutil.workdir() / ".work_cdp" / "selftest" / "pacing"
    state_file = scratch / ".work_cdp" / STATE_FILE
    try:
        # ── 1. DOI → 站点：按 DOI 前缀，不按落地页 host ──────
        cases = [
            ("10.1016/j.cell.2014.03.011", "elsevier"),
            ("https://doi.org/10.1038/s41577-022-00763-8", "nature.com"),
            ("10.1109/TMI.2020.1", "ieeexplore.ieee.org"),
            ("10.1158/2159-8290.cd-19-0761", "aacrjournals.org"),
            ("doi:10.4049/jimmunol.0802740", "jimmunol.org"),
        ]
        wrong = [(d, site_of_doi(d), w) for d, w in cases if site_of_doi(d) != w]
        if wrong:
            bad += 1
            print(f"  [X] 站点推断错误：{wrong}")
        else:
            print(f"站点推断：{len(cases)}/{len(cases)} 命中（URL/doi: 前缀已先剥离）")
        # 关键：两个不同 DOI 前缀不能并成同一站点
        if site_of_doi("10.1016/a") == site_of_doi("10.1158/b"):
            bad += 1
            print("  [X] 不同出版商被并成同一站点（会导致节流失效或全局串行）")
        else:
            print("站点推断：不同出版商分组不同，doi.org 未把全站塌缩成一个键")

        # ── 1b. ★ 与既有 detect 的映射不得漂移 ────────────────
        try:
            import detect  # noqa: PLC0415
        except Exception as e:  # noqa: BLE001
            print(f"漂移断言：跳过（detect 不可导入：{type(e).__name__}）")
        else:
            # 只对**双方都有映射**的前缀断言一致。
            #   detect 不在它的表里时会退化成「返回前缀本身」（如 "10.1158"），
            #   那不是冲突，只是它未覆盖 —— 本模块的补充正是为了补这些。
            conflicts, unlisted = [], []
            for pref, mine in DOI_PREFIX_SITE_SUPPLEMENT.items():
                theirs = detect.site_of_doi(f"{pref}/probe")
                if theirs == pref:
                    unlisted.append(pref)
                elif theirs and theirs != mine:
                    conflicts.append((pref, mine, theirs))
            if conflicts:
                bad += 1
                print(f"  [X] 与 detect._DOI_PREFIX_SITE 冲突（会分成两组、节流各算一套）：{conflicts}")
            else:
                print(f"漂移断言：补充前缀与 detect 的映射无冲突"
                      f"（其中 {len(unlisted)} 个是 detect 未覆盖、由本模块补充：{unlisted}）")
            # 别名必须都指向真实存在的档位，否则等于没生效
            dangling = [k for k, v in INTERVAL_ALIAS.items() if v not in HOST_INTERVALS]
            if dangling:
                bad += 1
                print(f"  [X] 别名指向不存在的档位：{dangling}")
            else:
                print(f"别名完整性：{len(INTERVAL_ALIAS)} 个别名都指向真实档位")
            # 出版社组键必须能拿到专用档位（不是掉进默认值）
            g_elsevier = PacerGuard(wd=None, persist=False)
            if g_elsevier.interval_for("elsevier") == DEFAULT_INTERVAL:
                bad += 1
                print("  [X] 'elsevier' 键掉进默认档位 —— 别名未生效")
            else:
                print(f"别名生效：elsevier → {g_elsevier.interval_for('elsevier')}（120-240 档）")

        # ── 2. 间隔表命中：出版社内容站必须远大于 API ─────────
        g = PacerGuard(wd=scratch, rng=random.Random(1))
        sd = g.interval_for("sciencedirect.com")
        api = g.interval_for("api.crossref.org")
        if not (sd[0] >= 120 and api[1] <= 20):
            bad += 1
            print(f"  [X] 间隔表不合理：sciencedirect={sd} crossref={api}")
        else:
            print(f"间隔表：sciencedirect.com={sd} 远大于 api.crossref.org={api}")
        unk = g.interval_for("never-heard-of.example")
        if unk != DEFAULT_INTERVAL:
            bad += 1
            print(f"  [X] 未知站点未用默认间隔：{unk}")
        else:
            print(f"间隔表：未知站点用默认 {DEFAULT_INTERVAL}（宁可慢，不可打封）")

        # ── 3. 节流真的生效：同站点第二次必须等 ──────────────
        clk = _FakeClock()
        g = PacerGuard(wd=None, persist=False, clock=clk.now, sleeper=clk.sleep,
                       rng=random.Random(0))
        g.pace("sciencedirect.com")            # 首次不等待
        first_slept = len(clk.slept)
        g.pace("sciencedirect.com")            # 第二次必须等待
        if first_slept != 0 or len(clk.slept) != 1:
            bad += 1
            print(f"  [X] 节流次数错误：first={first_slept} total={len(clk.slept)}")
        else:
            print(f"节流：首次不等待，第二次等待 {clk.slept[0]}s（在 120-240 区间内）")
        w = clk.slept[0]
        if not (115 <= w <= 245):
            bad += 1
            print(f"  [X] 等待时长 {w}s 不在 sciencedirect 的 120-240 区间")
        else:
            print("节流：等待时长落在该站点实测档位内")

        # 不同站点之间互不影响（节流按 host）
        g.pace("api.crossref.org")
        if len(clk.slept) != 1:
            bad += 1
            print("  [X] 换站点后仍然等待 —— 节流被错误地全局化")
        else:
            print("节流：换站点不等待（按 host 而非全局）")

        # ── 4. 熔断阈值与持久化 ─────────────────────────────
        g = PacerGuard(limit=2, wd=scratch, persist=True, rng=random.Random(2))
        if g.blocked("aacrjournals.org"):
            bad += 1
            print("  [X] 初始状态不应熔断")
        g.hit("aacrjournals.org", "challenge")
        if g.blocked("aacrjournals.org"):
            bad += 1
            print("  [X] 1/2 时不应熔断")
        g.hit("aacrjournals.org", "challenge")
        if not g.blocked("aacrjournals.org"):
            bad += 1
            print("  [X] 2/2 时应熔断")
        else:
            print("熔断：limit=2 时第 2 次受阻即熔断")
        p = g.save()
        if not p or not p.is_file():
            bad += 1
            print(f"  [X] 熔断状态未落盘：{p}")
        else:
            print(f"熔断：状态已写入 {pathlib.Path(p).name}（读-改-写，不覆盖逐篇结果）")

        # ── 5. ★ 核心：跨轮复用（既有 SiteGuard 做不到这点）──
        g2 = PacerGuard(limit=2, wd=scratch, persist=True)
        if not g2.blocked("aacrjournals.org"):
            bad += 1
            print("  [X] 新一轮未继承上一轮的熔断 —— 持久化没起作用")
        else:
            print("跨轮熔断：新一轮直接继承上一轮计数（不再撞同一堵墙）")
        if g2.remaining("aacrjournals.org") != 0:
            bad += 1
            print(f"  [X] remaining 计算错误：{g2.remaining('aacrjournals.org')}")
        else:
            print("跨轮熔断：remaining=0，调用方可知「该站本轮必跳过」")

        # ── 6. ★ 核心：写状态文件不得抹掉逐篇结果 ────────────
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({
            "10.1016/j.cell.2014.03.011": {"doi": "10.1016/j.cell.2014.03.011", "pdf_ok": True,
                                           "pdf_file": "2014_x.pdf"},
            STATE_KEY: {"old.example": {"count": 1, "last_reason": "challenge"}},
        }, ensure_ascii=False), encoding="utf-8")
        g3 = PacerGuard(limit=2, wd=scratch, persist=True)
        g3.hit("new.example", "captcha")
        g3.save()
        data = json.loads(state_file.read_text(encoding="utf-8"))
        if "10.1016/j.cell.2014.03.011" not in data or not data["10.1016/j.cell.2014.03.011"].get("pdf_ok"):
            bad += 1
            print("  [X] 写熔断状态时抹掉了逐篇下载结果 —— 会毁掉断点续传")
        else:
            print("状态共存：逐篇结果（pdf_ok）在写熔断状态后完好")
        if int((data.get(STATE_KEY) or {}).get("old.example", {}).get("count") or 0) != 1:
            bad += 1
            print(f"  [X] 既有持久计数被覆盖：{data.get(STATE_KEY)}")
        else:
            print("状态共存：既有站点的持久计数被保留（合并而非覆盖）")
        merged_new = int((data.get(STATE_KEY) or {}).get("new.example", {}).get("count") or 0)
        if merged_new != 1:
            bad += 1
            print(f"  [X] 新站点计数未写入：{merged_new}")
        else:
            print("状态共存：本轮新增站点已并入")

        # ── 7. 不变式 ②：记账对后来者立即可见（锁内更新）─────
        g4 = PacerGuard(limit=3, wd=None, persist=False)
        import threading  # noqa: PLC0415
        seen = []
        site = "race.example"

        def worker(i):
            with g4.lock(site):
                if g4.blocked(site):
                    seen.append((i, "skipped"))
                    return
                g4.hit(site, f"w{i}")
                seen.append((i, "passed"))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        passed = [i for i, s in seen if s == "passed"]
        if len(passed) != 3:
            bad += 1
            print(f"  [X] limit=3 却放进了 {len(passed)} 个（锁内记账失效）：{passed}")
        else:
            print(f"并发记账：limit=3 时恰好放行 3 个（锁内更新，后来者读到最新值）")

        # ── 8. 状态文件损坏时不得崩 ─────────────────────────
        state_file.write_text("{ not json", encoding="utf-8")
        try:
            g5 = PacerGuard(limit=2, wd=scratch, persist=True)
            g5.hit("x.example", "r")
            g5.save()
            data = json.loads(state_file.read_text(encoding="utf-8"))
            if STATE_KEY not in data:
                bad += 1
                print("  [X] 损坏文件后未能重建状态")
            else:
                print("健壮性：状态文件损坏时不崩，且能重建保留键")
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"  [X] 状态文件损坏导致异常：{type(e).__name__}: {e}")

        # ── 9. summary/status 可读 ──────────────────────────
        s = g5.status()
        if "limit" not in s or "sites" not in s:
            bad += 1
            print(f"  [X] status() 结构不完整：{s}")
        else:
            print(f"可观测性：status() 给出 limit/state_file/逐站点计数；"
                  f"summary='{g5.summary()}'")
    finally:
        try:
            import shutil  # noqa: PLC0415
            shutil.rmtree(scratch, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass

    print("\n[OK] 自检全部通过" if bad == 0 else f"\n[X] 自检有 {bad} 项失败")
    return 0 if bad == 0 else 1


def main() -> int:
    argv = sys.argv[1:]
    if "--self-test" in argv:
        return self_test()
    g = load_guard()
    if "--status" in argv:
        print(json.dumps(g.status(), ensure_ascii=False, indent=2))
        return 0
    if "--clear" in argv:
        i = argv.index("--clear")
        if i + 1 >= len(argv):
            print("[X] --clear 需要一个站点名")
            return 2
        site = argv[i + 1]
        g.hits.pop(site, None)
        g.persisted.pop(site, None)
        g.save()
        print(f"已清除 {site} 的熔断计数")
        return 0
    if "--site-of" in argv:
        i = argv.index("--site-of")
        if i + 1 >= len(argv):
            print("[X] --site-of 需要一个 DOI")
            return 2
        s = site_of_doi(argv[i + 1])
        print(f"{argv[i + 1]} → {s}（间隔 {g.interval_for(s)}）")
        return 3 if g.blocked(s) else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
