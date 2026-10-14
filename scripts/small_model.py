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
# 路径解析（迁移到其他机器时的可用性保证，按优先级）：
#   ⓪ 包内资产根 `vendor/small-model-assets/`（pack_release.py --assets 打入时存在，
#      与开发机布局同构：<root>/.hf-home/qwen3-06b-hf、<root>/outputs/…、
#      <root>/llamacpp/…）—— **发行包换机器后开箱即用，不需要外部 小模型/ 目录**
#   ① 环境变量 SMALL_MODEL_ROOT          —— 模型仓库根（含 .hf-home 与 outputs）
#   ② 环境变量 SMALL_MODEL_BASE / SMALL_MODEL_ADAPTER_DIR —— 分别覆盖基座/适配器目录
#   ③ 仓库根的候选位置自动探测：
#        <本脚本>/../../../            （流程1 在 小模型/2/流程1 时命中 小模型/）
#        <本脚本>/../../.. 的父目录     （流程1 直接放在 小模型/ 下时）
#   判据：候选目录下同时存在 .hf-home/qwen3-06b-hf 与 outputs/deai_qwen3_06b。
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

import os
import re
import sys
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
    """模型层不可用（缺依赖 / 找不到基座或适配器 / 推理失败）。"""


# ══════════════════════════════════════════════════════════════
# 路径解析
# ══════════════════════════════════════════════════════════════

def _root_candidates() -> list[Path]:
    cands = []
    # ⓪ 包内资产根优先：发行包自带权重时，它就是这台机器上的"模型仓库根"
    #    （llm_session 的 workspace 同样落到这里 —— gguf/llama-server 一并可达）
    #    _HERE = <包>/scripts，故包根是 _HERE.parent（不是 parents[1]，那是包的上一级）
    packaged = _HERE.parent / "vendor" / "small-model-assets"
    if (packaged / BASE_REL).is_dir():
        cands.append(packaged)
    env_root = (os.environ.get("SMALL_MODEL_ROOT") or "").strip()
    if env_root:
        cands.append(Path(env_root))
    # 流程1/scripts → 流程1 → 2 → 小模型
    cands.append(_HERE.parents[2])
    # 流程1/scripts → 流程1 → 小模型（流程1 直接放在模型仓库根时）
    cands.append(_HERE.parents[1])
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
    """→ {"root": Path|None, "base": Path|None, "adapters": {task: Path|None}}。

    只查路径不 import 重依赖，可在无 torch 环境下安全调用（自检/报错提示用）。
    """
    base = (os.environ.get("SMALL_MODEL_BASE") or "").strip() or None
    adir = (os.environ.get("SMALL_MODEL_ADAPTER_DIR") or "").strip() or None
    root = None
    if base is None or adir is None:
        for c in _root_candidates():
            if (c / BASE_REL).is_dir() and (c / "outputs" / ADAPTER_NAMES["deai"]).is_dir():
                root = c
                break
    base_p = Path(base) if base else (root / BASE_REL if root else None)
    adir_p = Path(adir) if adir else (root / "outputs" if root else None)
    adapters = {}
    for task, name in ADAPTER_NAMES.items():
        p = (adir_p / name) if adir_p else None
        adapters[task] = p if (p and (p / "adapter_model.safetensors").is_file()) else None
    ok_base = bool(base_p and (base_p / "config.json").is_file())
    return {"root": root, "base": base_p if ok_base else None, "adapters": adapters}


def availability() -> dict:
    """轻量探测：依赖是否可 import、路径是否齐。不加载任何权重。"""
    paths = resolve_paths()
    deps = {}
    for mod in ("torch", "transformers", "peft"):
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
        lines.append(f"缺 Python 依赖：{'、'.join(lack)}。安装（CUDA 13 索引，本机 sm_120 必需）：")
        lines.append("  uv run --with torch --with transformers --with peft "
                     "--index-url https://download.pytorch.org/whl/cu130 python …")
        lines.append("  或直接用 小模型/.venv/Scripts/python.exe（已装好）。")
    p = av["paths"]
    if p["base"] is None:
        lines.append("找不到 Qwen3-0.6B 基座（.hf-home/qwen3-06b-hf）。"
                     "设 SMALL_MODEL_ROOT 指向模型仓库根，或 SMALL_MODEL_BASE 直指基座目录。")
    for task, ap in p["adapters"].items():
        if ap is None:
            lines.append(f"找不到 {task} 适配器（outputs/{ADAPTER_NAMES[task]}）。"
                         "设 SMALL_MODEL_ADAPTER_DIR 直指适配器父目录。")
    lines.append("已探测的仓库根候选：" + "；".join(str(c) for c in _root_candidates()))
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
        paths = resolve_paths()
        if paths["base"] is None or not all(paths["adapters"].values()):
            raise SmallModelError("模型路径不全：\n" + missing_hint())
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


# ══════════════════════════════════════════════════════════════
# llama.cpp 服务生命周期（**复用** vendor/llm_lifecycle，不另写启动脚本）
#
# 来源：`小模型/eval_2to4b/llm_lifecycle/`（pin 登记 + 四重校验精准释放、
# 自动挑端口、/props 身份核实、孤儿 sweep —— 能力与用法见其 README）。
# 用途差别：deai/kw 适配器走本模块的 transformers 路径；**基座 gguf**
# （Qwen3-0.6B-Q8_0 零样本抽关键词等）走这里 —— 由 llm_session 负责
# 「启动 → 跑任务 → 只释放本次」，绝不触碰别的进程/服务。
# 命令行入口同样用现成的：vendor/llm_lifecycle/llm.ps1（或 bash 的 ./llm）。
# ══════════════════════════════════════════════════════════════

_LIFECYCLE_DIR = _HERE.parent / "vendor" / "llm_lifecycle"


@contextmanager
def llm_session(model: str = "Qwen3-0.6B", **kw):
    """包一层 vendor/llm_lifecycle.run_with_llm.llm_session，只补默认路径。

    workspace 缺省取**模型仓库根**（resolve_paths 探测到的、含 llamacpp/
    与 gguf 的那一级；LLM_WORKSPACE 环境变量可覆盖）。**刻意不跟随
    PAPER_WORKDIR** —— 那是案例数据目录，llm_lifecycle 虽也认它，但把
    pin/日志与模型检索锚在案例目录上是错的，这里统一注入仓库根。
    其余参数（port/ctx/ngl/slots/tag/keep_on_failure…）原样透传。
    → yield 会话信息：`api_url`（chat/completions 全址）、`base_url`
      （**已含 /v1**，别再拼一次）、`api_key`、`port`、`model_path`、`env`。
    """
    if not _LIFECYCLE_DIR.is_dir():
        raise SmallModelError(
            f"找不到 vendor/llm_lifecycle/（{str(_LIFECYCLE_DIR)}）—— "
            "llama.cpp 服务启停脚本未随流程部署。")
    kw.setdefault("model", model)
    if not kw.get("workspace"):
        env_ws = (os.environ.get("LLM_WORKSPACE") or "").strip()
        root = resolve_paths()["root"]
        kw["workspace"] = env_ws or (str(root) if root else None)
        if not root and not env_ws:
            raise SmallModelError(
                "无法确定模型仓库根（llamacpp/gguf 所在目录）。"
                "设 LLM_WORKSPACE 或 SMALL_MODEL_ROOT 后重试。")
    if str(_LIFECYCLE_DIR) not in sys.path:
        sys.path.insert(0, str(_LIFECYCLE_DIR))
    from run_with_llm import llm_session as _upstream_session
    with _upstream_session(**{k: v for k, v in kw.items() if v is not None}) as s:
        yield s


if __name__ == "__main__":
    # 本模块**不提供自写 CLI** —— 命令行入口用现成的：
    #   · llama.cpp 服务：vendor/llm_lifecycle/llm.ps1（doctor/serve/status/release/
    #     sweep/run），或 bash 侧 ./llm
    #   · deai/kw 冒烟与迁移检查：scripts/selftest_small_model.py
    # 库式调用：from small_model import SmallModel, llm_session
    print(__doc__)
    print("命令行入口请使用 vendor/llm_lifecycle/llm.ps1 或 "
          "scripts/selftest_small_model.py（本模块不自写 CLI）。")
