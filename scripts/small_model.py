# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# 小模型共用推理模块（deai / kw 两个 Qwen3-0.6B LoRA 适配器）
#
# 定位：把 `小模型` 项目里**已实测验证**的两个适配器接进流程1 ——
#   · deai：解读合规检查（8 标签闭集，实测 8.3% → 99.1%）
#   · kw  ：中文候选词 term/fragment 判别（实测 51.4% → 97.1%）
#
# 推理约定**逐字照搬**训练/评测时的口径（这些脚本相对本模块优先）：
#   · `小模型/eval_2to4b/eval_all_tasks.py`     —— 加载、chat template、
#     "<think>" 预填关思考、greedy、标签"输出内搜索"的公平解析
#   · `小模型/eval_2to4b/build_deai_data.py`    —— deai 指令串（INSTR 逐字一致）
#   · `小模型/eval_2to4b/build_keyword_data4.py`—— kw 指令串与输入格式
# 任何一处改动都会使实测数字失效，**不要"顺手优化"提示词**。
#
# 路径与首次启动（权重不随流程分发）：
#   ① SMALL_MODEL_ROOT 可指定缓存根；默认使用流程目录下的 .model-cache/。
#   ② 首次真正启用 --model / --kw-model 或 selftest 时，ensure_model_assets()
#      自动安装缺少的 Python 依赖，并下载 Qwen/Qwen3-0.6B 基座。
#   ③ vendor/small-model-assets/ 只保存训练后的 adapter_model.safetensors；
#      adapter_config.json 在首次启动时根据训练配置生成到缓存目录。
#   ④ 可用 SMALL_MODEL_OFFLINE=1 禁止联网；缺资源时给出明确的下载命令。
#   ⑤ 下载源：显式 HF_ENDPOINT → 官方站 → hf-mirror.com → **ModelScope（魔搭）**
#      依次回退。实测本机 huggingface.co 被 hosts 指到 127.0.0.1，直连必失败；
#      HF 全线不通时自动改走 ModelScope（同版本同文件，实测 1.5 GB 下载 + 推理通过）。
#      走 HF 镜像时会关掉 Xet（镜像不代理 CAS，不关会在大文件上拿到 401）；
#      可用 SMALL_MODEL_NO_MODELSCOPE=1 关掉最后的兜底源。
#   ⑥ 包索引：SMALL_MODEL_TORCH_INDEX（只给 torch）/ SMALL_MODEL_PIP_INDEX
#      （其余包，国内可设清华源）；uv 缓存落 .model-cache/.uv-cache，
#      避免机器上的全局 uv 缓存不可写时连装都装不上。
#
# 依赖（只在调用方显式开启模型层时才 import）：
#   uv run --with torch --with transformers --with peft python …
#   设备**不写死**：SMALL_MODEL_DEVICE=cuda|cuda:N|mps|cpu|auto（默认 auto，
#   按 cuda→mps→cpu 取第一个可用）。任何受支持设备都能推理（**包括 CPU**），
#   落到 CPU / 非默认设备 / fp16 退回时都会向 stderr 打**速度或口径警告**，不静默。
#   GPU 速度参考（本机 CUDA）：deai 4 句 0.8s、kw 4 词 0.1s；CPU 慢一到两个数量级。
#   Blackwell sm_120 若要 GPU：torch 必须走 CUDA 13 索引
#   （--index-url https://download.pytorch.org/whl/cu130，旧轮子不含该架构）。
#   或直接用 小模型/.venv/Scripts/python.exe（已装好 torch 2.14.1+cu130）。
#
# 自检：python scripts/selftest_small_model.py
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

_HERE = Path(__file__).resolve().parent

# ── 适配器目录名（outputs/ 下）────────────────────────────────
ADAPTER_NAMES = {"deai": "deai_qwen3_06b", "kw": "kw_qwen3_06b"}
BASE_REL = Path(".hf-home") / "qwen3-06b-hf"

# ── 指令串（逐字来自 build_deai_data.py INSTR / build_keyword_data4.py）──
DEAI_INSTR = ("下面是论文中文解读中的一段话。判断它是否违反解读写作规范，"
              "违反则输出违规类型，合规则输出 OK。只输出一个标签。\n\n"
              "可选标签：OK / EMPTY_FILLER / SPECULATION_LEAK / STRONG_VERB_OVERREACH / "
              "L1_FORBIDDEN / NUMBER_CONVERSION / UNSUPPORTED_NOVELTY / TERM_NO_GLOSS\n"
              "（L1_FORBIDDEN 指 L1 精简档中出现了误差项、样本量、图表引用或实验步骤）")

KW_INSTR = ("判断下面这个从中文论文全文中切出的字符串是不是一个完整的"
            "中文术语。是术语输出 term，是跨词边界的碎片或泛化词输出 fragment。")

#: deai 标签搜索顺序（与 eval_all_tasks.py 完全一致：长标签在前，OK 垫底）
DEAI_LABELS = ("EMPTY_FILLER", "SPECULATION_LEAK", "STRONG_VERB_OVERREACH",
               "L1_FORBIDDEN", "NUMBER_CONVERSION", "UNSUPPORTED_NOVELTY",
               "TERM_NO_GLOSS", "OK")


class SmallModelError(RuntimeError):
    """模型层不可用（缺依赖 / 下载失败 / 找不到基座或适配器 / 推理失败）。"""


@contextmanager
def _asset_lock():
    """Serialize first-use installs/downloads without adding a lock dependency."""
    lock = _cache_root() / ".ensure.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    deadline = time.monotonic() + float(os.environ.get("SMALL_MODEL_LOCK_TIMEOUT", "1800"))
    try:
        while fd is None:
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"pid={os.getpid()}\n".encode())
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise SmallModelError(f"等待模型首次启动锁超时：{lock}")
                time.sleep(1.0)
        yield
    finally:
        if fd is not None:
            os.close(fd)
            try:
                lock.unlink()
            except FileNotFoundError:
                pass


# ══════════════════════════════════════════════════════════════
# 路径解析
# ══════════════════════════════════════════════════════════════

MODEL_REPO_ID = "Qwen/Qwen3-0.6B"
HF_OFFICIAL = "https://huggingface.co"
HF_MIRROR = "https://hf-mirror.com"
MODELSCOPE_API = "https://www.modelscope.cn"
MODELSCOPE_REVISION = "master"
CACHE_DIR_NAME = ".model-cache"
#: 基座只需要这些文件（权重 + 分词器 + 生成配置）；其余（README/LICENSE/.gitattributes）不取。
BASE_PATTERNS = ["config.json", "generation_config.json", "tokenizer_config.json",
                 "tokenizer.json", "vocab.json", "merges.txt", "model*.safetensors"]
ADAPTER_CONFIG = {
    "peft_type": "LORA",
    "task_type": "CAUSAL_LM",
    "inference_mode": True,
    "r": 16,
    "lora_alpha": 32,
    "lora_dropout": 0.05,
    "bias": "none",
    "target_modules": ["gate_proj", "up_proj", "q_proj", "k_proj", "o_proj", "v_proj", "down_proj"],
}


def _cache_root() -> Path:
    raw = (os.environ.get("SMALL_MODEL_ROOT") or "").strip()
    return Path(raw).expanduser() if raw else _HERE.parent / CACHE_DIR_NAME


def _packaged_adapter_root() -> Path:
    return _HERE.parent / "vendor" / "small-model-assets" / "outputs"


def _root_candidates() -> list[Path]:
    """Return cache and legacy roots, without downloading or importing heavy deps."""
    cands = [_cache_root()]
    env_root = (os.environ.get("SMALL_MODEL_ROOT") or "").strip()
    if env_root:
        cands.append(Path(env_root).expanduser())
    cands.extend([_HERE.parents[2], _HERE.parents[1]])
    out, seen = [], set()
    for c in cands:
        try:
            r = c.resolve()
        except OSError:
            continue
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _adapter_candidates(task: str) -> list[Path]:
    name = ADAPTER_NAMES[task]
    cands = []
    explicit = (os.environ.get("SMALL_MODEL_ADAPTER_DIR") or "").strip()
    if explicit:
        cands.append(Path(explicit).expanduser() / name)
    cands.append(_cache_root() / "outputs" / name)
    cands.append(_packaged_adapter_root() / name)
    # 适配器可来自显式目录、缓存或包内 vendor；不自动读取外部开发仓库。
    out, seen = [], set()
    for c in cands:
        try:
            r = c.resolve()
        except OSError:
            continue
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _base_candidates() -> list[Path]:
    explicit = (os.environ.get("SMALL_MODEL_BASE") or "").strip()
    cands = [Path(explicit).expanduser()] if explicit else []
    # 不再自动回退到开发机父目录；发行包必须首次自行下载到 .model-cache。
    cands.append(_cache_root() / BASE_REL)
    out, seen = [], set()
    for c in cands:
        try:
            r = c.resolve()
        except OSError:
            continue
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def resolve_paths() -> dict:
    """Return local base and adapter paths; never downloads or installs anything."""
    base_p = next((p for p in _base_candidates() if (p / "config.json").is_file()), None)
    adapters = {}
    for task in ADAPTER_NAMES:
        adapters[task] = next(
            (p for p in _adapter_candidates(task)
             if (p / "adapter_model.safetensors").is_file() and (p / "adapter_config.json").is_file()),
            None,
        )
    roots = [p.parent.parent for p in adapters.values() if p is not None]
    root = base_p.parent.parent if base_p and all(adapters.values()) else (roots[0] if roots else None)
    return {"root": root, "base": base_p, "adapters": adapters}


def _offline() -> bool:
    return (os.environ.get("SMALL_MODEL_OFFLINE") or "").strip().lower() in {"1", "true", "yes", "on"}


def _run_install(packages: list[str], index: str | None = None) -> None:
    uv = shutil.which("uv")
    args = (["--index-url", index] if index else []) + packages
    env = dict(os.environ)
    if uv:
        # uv 默认把缓存放 ~/AppData/Local/uv —— 受限环境（沙箱/只读用户目录/多用户共用）
        # 下会在启动时就 "Access is denied"，连包都还没开始下。缓存落到流程自己的
        # 缓存根里，既不依赖外部可写目录，也不污染机器上的全局缓存。
        env.setdefault("UV_CACHE_DIR", str(_cache_root() / ".uv-cache"))
        cmd = [uv, "pip", "install", "--python", sys.executable, *args]
    else:
        cmd = [sys.executable, "-m", "pip", "install", *args]
    print("[small_model] 首次启动安装运行环境：" + " ".join(cmd), file=sys.stderr)
    subprocess.run(cmd, check=True, env=env)


def _ensure_dependencies() -> None:
    missing = []
    for mod, pkg in (("torch", "torch"), ("transformers", "transformers"),
                     ("peft", "peft"), ("huggingface_hub", "huggingface_hub")):
        try:
            __import__(mod)
        except Exception:
            missing.append(pkg)
    if not missing:
        return
    if _offline():
        raise SmallModelError("离线模式下缺少运行依赖：" + ", ".join(missing))
    # 索引可用环境变量精确指定：torch 与其余包分开（PyTorch 索引里没有
    # transformers/peft；反之 CUDA 轮子只在 PyTorch 索引里）。
    torch_index = (os.environ.get("SMALL_MODEL_TORCH_INDEX") or "").strip() or None
    pip_index = (os.environ.get("SMALL_MODEL_PIP_INDEX") or "").strip() or None
    if "torch" in missing:
        _run_install(["torch"], torch_index)
        missing = [p for p in missing if p != "torch"]
    if missing:
        _run_install(missing, pip_index)


def _endpoint_reachable(endpoint: str, timeout: float = 6.0) -> bool:
    """探测下载源是否可用 —— 必须用**与 huggingface_hub 同一套 HTTP 栈**（httpx）。

    [STOP] 别改成裸 `ssl.create_default_context()` 探测：本机代理/沙箱的 CA 装在
    **Windows 证书库**里，系统默认上下文认它、httpx（走 certifi）不认 ——
    裸探测会得出"官方站可达"的**错误**结论，于是白试一次再报一个看不懂的证书错。
    实测（2026-10-16）：裸探测 True，真实下载 CERTIFICATE_VERIFY_FAILED。
    """
    try:
        import httpx
    except Exception:                                             # noqa: BLE001
        return True          # 探测不了就不拦，交给真正的下载去试
    try:
        r = httpx.get(f"{endpoint}/api/models/{MODEL_REPO_ID}", timeout=timeout)
        return r.status_code < 500        # 200/401/403 都说明"这个源是通的"
    except Exception:                                             # noqa: BLE001
        return False


def _endpoint_order() -> list[str]:
    """下载源顺序：显式 HF_ENDPOINT → 官方（可达时）→ hf-mirror.com。"""
    explicit = (os.environ.get("HF_ENDPOINT") or "").strip()
    if explicit:
        return [explicit]
    if _endpoint_reachable(HF_OFFICIAL):
        return [HF_OFFICIAL]
    return [HF_MIRROR, HF_OFFICIAL]


def _apply_xet_policy(endpoint: str, hub_constants) -> None:
    """走镜像时必须关掉 Xet。

    实测：hf-mirror 会照抄 HF 的 Xet 头，但 CAS（cas-server.xethub.hf.co）不认这个
    令牌 —— 大文件下到一半报 `401 Unauthorized File reconstruction error`，
    看起来像"权限问题/账号问题"，其实只要回退到传统 HTTP 就好。
    `is_xet_available()` **每次调用都读** `constants.HF_HUB_DISABLE_XET`，
    所以运行期改常量立即生效（无需重开进程）。
    """
    if endpoint.rstrip("/") == HF_OFFICIAL:
        return
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    hub_constants.HF_HUB_DISABLE_XET = True


def _fnmatch_any(path: str, patterns: list[str]) -> bool:
    """按 HF `allow_patterns` 的口径匹配：整路径与文件名各试一次 fnmatch。"""
    import fnmatch

    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(name, p) for p in patterns)


def _download_file(url: str, params: dict, dest: Path, size_hint: int = 0) -> None:
    """流式下载到 `<dest>.part` 再改名 —— 中断只留残片，不会留在目标路径上冒充成品。"""
    import httpx

    tmp = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, params=params, timeout=None, follow_redirects=True) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length") or 0) or size_hint
        done = 0
        next_mark = 0.0
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_bytes(1 << 20):
                fh.write(chunk)
                done += len(chunk)
                mb = done / 1048576
                if mb >= next_mark:
                    pct = f"{done * 100 / total:.0f}%" if total else "?"
                    print(f"[small_model]   {dest.name} {pct}（{mb:.0f} MB）", file=sys.stderr)
                    next_mark = mb + 200
    tmp.replace(dest)


def _download_base_modelscope(base: Path) -> None:
    """从 ModelScope（魔搭）取同一份基座 —— Hugging Face 全线不可达时的兜底源。

    为什么用 HTTP API 而不是 modelscope 包：官方 SDK 会拖进一大票依赖，而这里只需要
    "列文件 + 按路径取文件" 两个接口，httpx（huggingface_hub 的硬依赖，装依赖时已有）
    就够。已实测两个接口（2026-10-16）：`/repo/files` 列目录、`/repo?FilePath=` 取文件。
    已存在的文件按大小校验后跳过 —— 从 HF 那边下到一半再转过来，不必重下。
    """
    import httpx

    repo = MODEL_REPO_ID
    r = httpx.get(f"{MODELSCOPE_API}/api/v1/models/{repo}/repo/files",
                  params={"Revision": MODELSCOPE_REVISION, "Recursive": "true"}, timeout=60)
    r.raise_for_status()
    entries = ((r.json().get("Data") or {}).get("Files")) or []
    wanted = [(str(f.get("Path") or ""), int(f.get("Size") or 0)) for f in entries
              if f.get("Path") and f.get("Type") != "tree"]
    picked = [(p, s) for p, s in wanted if _fnmatch_any(p, BASE_PATTERNS)]
    if not picked:
        raise SmallModelError(
            f"ModelScope 上 {repo} 没有匹配 {BASE_PATTERNS} 的文件（该仓库共 {len(wanted)} 个）")

    print(f"[small_model] ModelScope 源：命中 {len(picked)}/{len(wanted)} 个文件", file=sys.stderr)
    url = f"{MODELSCOPE_API}/api/v1/models/{repo}/repo"
    for path, size in picked:
        dest = base / path
        if dest.is_file() and (not size or dest.stat().st_size == size):
            print(f"[small_model]   跳过已存在：{path}", file=sys.stderr)
            continue
        _download_file(url, {"Revision": MODELSCOPE_REVISION, "FilePath": path}, dest, size)


def _download_base(base: Path) -> None:
    """首次下载 Qwen 基座：Hugging Face（官方 → 镜像）不可达时退回 ModelScope。

    四个坑（都实测过，别再踩）：
      ① 本机 `huggingface.co` 被 hosts 指到 127.0.0.1（国内直连常见处理），
         直连只会拿到 `CERTIFICATE_VERIFY_FAILED`，报错完全看不出真实原因。
      ② huggingface_hub ≥1.x 把 `HF_ENDPOINT` 读成**模块级常量**：import 之后再
         `os.environ["HF_ENDPOINT"] = ...` 是**无效**的（表现为"换了镜像仍报同一个错"）。
         必须走 `snapshot_download(endpoint=...)` 传参。
      ③ 镜像不代理 Xet CAS：不关 Xet 就会在大文件上拿到 401（见 _apply_xet_policy）。
      ④ HF 全线不通时不能就此失败 —— 还有 ModelScope（国内可达，实测同版本同文件）。
    顺序 = 显式 `HF_ENDPOINT` → 官方 → hf-mirror.com → ModelScope，任一成功即停。
    可用 `SMALL_MODEL_NO_MODELSCOPE=1` 关掉最后的兜底源。
    """
    cache = _cache_root()
    # [STOP] HF_HOME 也必须在 import 之前设：hub 读成模块级常量，
    #   否则会去写工作区外的 ~/.cache/huggingface（沙箱下被拒，只留一行警告）。
    os.environ.setdefault("HF_HOME", str(cache / ".hf-hub"))
    import inspect

    import huggingface_hub.constants as hub_constants
    from huggingface_hub import snapshot_download

    endpoints = _endpoint_order()
    takes_endpoint = "endpoint" in inspect.signature(snapshot_download).parameters
    no_modelscope = (os.environ.get("SMALL_MODEL_NO_MODELSCOPE") or "").strip().lower() \
        in {"1", "true", "yes", "on"}

    errors = []
    for endpoint in endpoints:
        _apply_xet_policy(endpoint, hub_constants)
        print(f"[small_model] 首次启动下载基座 {MODEL_REPO_ID} ← {endpoint}", file=sys.stderr)
        try:
            if takes_endpoint:
                snapshot_download(repo_id=MODEL_REPO_ID, local_dir=str(base),
                                  allow_patterns=BASE_PATTERNS, endpoint=endpoint)
            else:                                   # 老版本 hub：只能靠环境变量
                os.environ["HF_ENDPOINT"] = endpoint
                snapshot_download(repo_id=MODEL_REPO_ID, local_dir=str(base),
                                  allow_patterns=BASE_PATTERNS)
            os.environ["HF_ENDPOINT"] = endpoint    # 成功源记下，供后续调用复用
            return
        except Exception as e:                                    # noqa: BLE001
            errors.append(f"{endpoint}: {type(e).__name__}: {str(e).splitlines()[0][:200]}")
            print(f"[small_model] 该源不可用，换下一个：{errors[-1]}", file=sys.stderr)

    if not no_modelscope:
        print(f"[small_model] Hugging Face 全线不可达，改试 ModelScope ← {MODELSCOPE_API}",
              file=sys.stderr)
        try:
            _download_base_modelscope(base)
            return
        except Exception as e:                                    # noqa: BLE001
            errors.append(f"modelscope: {type(e).__name__}: {str(e).splitlines()[0][:200]}")
            print(f"[small_model] ModelScope 也不可用：{errors[-1]}", file=sys.stderr)

    tried = "、".join(endpoints + ([] if no_modelscope else ["ModelScope"]))
    raise SmallModelError(
        f"下载 {MODEL_REPO_ID} 失败（已试：{tried}）：\n  " + "\n  ".join(errors)
        + "\n可显式设 HF_ENDPOINT（如 https://hf-mirror.com）、SMALL_MODEL_OFFLINE=1，"
          "或设 SMALL_MODEL_BASE 指向已有的完整基座目录。"
    )


def _copy_adapter_weights() -> dict[str, Path]:
    cache = _cache_root()
    result = {}
    for task, name in ADAPTER_NAMES.items():
        src = next((p / "adapter_model.safetensors" for p in _adapter_candidates(task)
                    if (p / "adapter_model.safetensors").is_file()), None)
        if src is None:
            continue
        dst = cache / "outputs" / name
        dst.mkdir(parents=True, exist_ok=True)
        target = dst / "adapter_model.safetensors"
        if not target.is_file() or target.stat().st_size != src.stat().st_size:
            shutil.copy2(src, target)
        cfg = dict(ADAPTER_CONFIG)
        cfg["base_model_name_or_path"] = MODEL_REPO_ID
        (dst / "adapter_config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result[task] = dst
    return result


def ensure_model_assets() -> dict:
    """Install missing runtime pieces, download the base once, and materialize adapter configs."""
    _ensure_dependencies()
    cache = _cache_root()
    base = next((p for p in _base_candidates() if (p / "config.json").is_file()), None)
    if base is None:
        base = cache / BASE_REL
        base.mkdir(parents=True, exist_ok=True)
        if _offline():
            raise SmallModelError(f"离线模式下找不到模型基座：{base}；请先联网运行一次，或手动下载 {MODEL_REPO_ID}")
        _download_base(base)
    _copy_adapter_weights()
    paths = resolve_paths()
    if paths["base"] is None or not all(paths["adapters"].values()):
        raise SmallModelError("模型资产准备后仍不完整：\n" + missing_hint())
    return paths


def availability() -> dict:
    """轻量探测：依赖是否可 import、路径是否齐。不加载任何权重。"""
    paths = resolve_paths()
    deps = {}
    for mod in ("torch", "transformers", "peft", "huggingface_hub"):
        try:
            __import__(mod)
            deps[mod] = True
        except Exception:                                       # noqa: BLE001
            deps[mod] = False
    return {"paths": paths, "deps": deps,
            "ready": all(deps.values()) and paths["base"] is not None
                     and all(paths["adapters"].values())}


def missing_hint() -> str:
    """人类可读的"缺什么、怎么装"提示（显式开启却不可用时打印，不静默）。"""
    av = availability()
    lines = []
    lack = [m for m, ok in av["deps"].items() if not ok]
    if lack:
        lines.append(f"缺 Python 依赖：{'、'.join(lack)}。首次启动会自动安装；也可手动运行：")
        lines.append("  uv pip install --python <当前python> torch transformers peft huggingface_hub")
        lines.append("  Blackwell/sm_120 可设置 SMALL_MODEL_TORCH_INDEX=https://download.pytorch.org/whl/cu130")
    p = av["paths"]
    if p["base"] is None:
        lines.append(f"找不到 Qwen3-0.6B 基座。首次启动会下载 {MODEL_REPO_ID}；"
                     "离线时请先联网运行一次，或设 SMALL_MODEL_BASE 指向完整基座目录。")
    for task, ap in p["adapters"].items():
        if ap is None:
            lines.append(f"找不到 {task} 训练权重（adapter_model.safetensors）。"
                         "请确认 vendor/small-model-assets/outputs/ 中包含该文件，"
                         "或设 SMALL_MODEL_ADAPTER_DIR 指向适配器父目录。")
    lines.append("模型缓存根：" + str(_cache_root()))
    lines.append("可设 SMALL_MODEL_OFFLINE=1 禁止联网；首次下载需要 Hugging Face 网络访问。")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# 模型加载与批量推理
# ══════════════════════════════════════════════════════════════

class SmallModel:
    """基座 + 双适配器（一次加载，deai/kw 共享基座，按需 set_adapter 切换）。

    设备**不写死**：`SMALL_MODEL_DEVICE`（cuda / cuda:N / mps / cpu / auto，
    默认 auto）或构造参数 `device=` 均可指定；auto 按 cuda → mps → cpu 取第一个
    可用者。**任何受支持的设备都能推理（包括 CPU）**，但落到 CPU 或低效 dtype 时
    向 stderr 打警告（速度可能慢一个数量级），不静默。
    """

    def __init__(self, device: str | None = None, verbose: bool = False):
        try:
            with _asset_lock():
                paths = ensure_model_assets()
        except SmallModelError:
            raise
        except Exception as e:  # noqa: BLE001
            raise SmallModelError(f"首次启动准备模型失败：{e}") from e
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
            from peft import PeftModel
        except ImportError as e:
            raise SmallModelError(f"缺依赖（{e}）：\n{missing_hint()}") from e

        self._torch = torch
        device, why = self._pick_device(device, torch)
        self.device = device
        if why:
            print(f"[small_model][警告] {why}", file=sys.stderr)

        # dtype 跟随设备能力：CUDA 支持 bf16 用 bf16（训练/评测口径），
        # 老 CUDA 用 fp16，CPU 用 fp32（bf16 在 CPU 上既慢又未必支持）。
        if device.startswith("cuda"):
            dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            if dtype is torch.float16:
                print("[small_model][警告] 该 CUDA 设备不支持 bf16，退回 fp16 推理，"
                      "与评测口径（bf16）存在数值差异风险。", file=sys.stderr)
        else:
            dtype = torch.float32

        tok = AutoTokenizer.from_pretrained(str(paths["base"]), trust_remote_code=True)
        if tok.pad_token is None:
            tok.pad_token = tok.eos_token
        # 批量贪心解码需要左 padding（生成任务的标准做法）
        tok.padding_side = "left"
        model = AutoModelForCausalLM.from_pretrained(
            str(paths["base"]), trust_remote_code=True, dtype=dtype)
        first = True
        for task, ap in paths["adapters"].items():
            if first:
                model = PeftModel.from_pretrained(model, str(ap), adapter_name=task)
                first = False
            else:
                model.load_adapter(str(ap), adapter_name=task)
        self.tok = tok
        self.model = model.to(device).eval()
        if verbose:
            print(f"[small_model] 基座 {paths['base']}｜device={device}｜dtype={dtype}",
                  file=sys.stderr)

    @staticmethod
    def _pick_device(device, torch):
        """→ (device, 警告文案或 None)。不拦任何受支持设备，只用警告提示速度。"""
        req = (device or os.environ.get("SMALL_MODEL_DEVICE") or "auto").strip().lower()
        if req in ("", "auto"):
            if torch.cuda.is_available():
                return "cuda", None
            if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                return "mps", ("未用 CUDA（选中 mps）：若本机应有 NVIDIA 显卡，"
                               "检查 torch 是否为含该架构的 CUDA 构建 —— 落错设备慢约 9 倍。")
            return "cpu", ("在 CPU 上推理（未检测到 CUDA 设备）：功能一致但速度可能慢"
                           "一个数量级（本机 GPU 参考：deai 4 句 0.8s、kw 4 词 0.1s）。"
                           "如需 GPU：安装含本机架构的 torch（Blackwell sm_120 要 CUDA 13 "
                           "索引 https://download.pytorch.org/whl/cu130 ），"
                           "或设 SMALL_MODEL_DEVICE 指定设备。")
        if req.startswith("cuda"):
            if not torch.cuda.is_available():
                raise SmallModelError(
                    f"SMALL_MODEL_DEVICE/device={req} 但 CUDA 不可用 —— "
                    "多半是 torch 缺该架构的 CUDA 轮子（sm_120 需 CUDA 13 索引），"
                    "改用 cpu 或按上方提示重装。")
            return req, None
        if req == "mps":
            if not (getattr(torch.backends, "mps", None)
                    and torch.backends.mps.is_available()):
                raise SmallModelError(f"指定 device={req} 但 mps 不可用。")
            return "mps", ("在 mps 上推理：非默认设备，若本机应有 NVIDIA 显卡，"
                           "请确认这是有意选择。")
        if req == "cpu":
            return "cpu", ("按指定/探测在 CPU 上推理：功能一致，速度可能慢一个数量级"
                           "（deai 逐句、kw 千级词型时请以秒/条预估）。")
        raise SmallModelError(f"不支持的设备 {req}（可用：cuda / cuda:N / mps / cpu / auto）。")

    # ── 底层：批量分类 ────────────────────────────────────────
    def _classify(self, task: str, inputs: list[str], max_new: int,
                  batch_size: int = 16) -> list[str]:
        """inputs 为拼好的 user 内容（instruction + 空行 + 数据）。→ 原始输出文本。"""
        self.model.set_adapter(task)
        tok, model, torch = self.tok, self.model, self._torch
        outs: list[str] = []
        for i in range(0, len(inputs), batch_size):
            chunk = inputs[i:i + batch_size]
            prompts = []
            for content in chunk:
                msgs = [{"role": "user", "content": content}]
                text = tok.apply_chat_template(msgs, tokenize=False,
                                               add_generation_prompt=True)
                text += "<think>\n\n</think>\n\n"   # 统一关思考（评测口径）
                prompts.append(text)
            enc = tok(prompts, return_tensors="pt", padding=True).to(model.device)
            with torch.no_grad():
                gen = model.generate(**enc, max_new_tokens=max_new, do_sample=False,
                                     pad_token_id=tok.pad_token_id,
                                     eos_token_id=tok.eos_token_id)
            plen = enc["input_ids"].shape[1]
            for row in gen:
                raw = tok.decode(row[plen:], skip_special_tokens=True)
                # 与 eval_all_tasks.py 同款清理：剥掉可能的 think 残留
                raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
                outs.append(raw.replace("</think>", "").strip())
        return outs

    # ── deai ─────────────────────────────────────────────────
    def deai_classify(self, sentences: list[str], batch_size: int = 16) -> list[str | None]:
        """逐句违规类型判别。→ 标签（DEAI_LABELS 之一）；解析不出为 None（不计入）。"""
        inputs = [f"{DEAI_INSTR}\n\n{s}" for s in sentences]
        raws = self._classify("deai", inputs, max_new=48, batch_size=batch_size)
        labels = []
        for raw in raws:
            low = raw.lower()
            lab = None
            for cand in DEAI_LABELS:            # 顺序搜索（评测的公平解析口径）
                if cand.lower() in low:
                    lab = cand
                    break
            labels.append(lab)
        return labels

    # ── kw ───────────────────────────────────────────────────
    def kw_classify(self, items: list[dict], batch_size: int = 32) -> list[str | None]:
        """items: {"g","n","n_docs","left","right"}（left/right 为邻接字符列表）。
        → "term" / "fragment" / None（解析不出，调用方应保守保留）。"""
        inputs = []
        for it in items:
            inputs.append(
                f"{KW_INSTR}\n\n字符串：{it['g']}\n出现次数：{it['n']}\n"
                f"出现于文档数：{it['n_docs']}\n"
                f"左邻接：{'、'.join(it.get('left') or [])}\n"
                f"右邻接：{'、'.join(it.get('right') or [])}")
        raws = self._classify("kw", inputs, max_new=24, batch_size=batch_size)
        labels = []
        for raw in raws:
            low = raw.lower()
            if "fragment" in low or "碎片" in raw or "不是" in raw:
                labels.append("fragment")
            elif "term" in low or "术语" in raw or "是完" in raw:
                labels.append("term")
            else:
                labels.append(None)
        return labels


if __name__ == "__main__":
    print(__doc__)
    print("首次准备与自检请运行：scripts/selftest_small_model.py")
