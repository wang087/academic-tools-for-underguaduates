# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────
# selftest_small_model.py —— deai / kw 模型层迁移自检
#
# 换机器、换环境、挪目录之后，先跑这个再开 --model / --kw-model：
#   python scripts/selftest_small_model.py
#
# 检查项（自上而下，全部通过退出码 0；任一项硬失败退出码 1）：
#   0 首次准备  自动安装缺少依赖、下载 Qwen/Qwen3-0.6B、生成 PEFT 配置
#   1 依赖      torch / transformers / peft 可 import 且报版本
#   2 设备      不写死、任何受支持设备都可推理（cuda/cuda:N/mps/cpu，默认 auto）；
#               落 CPU 或 sm_120 配非 CUDA13 torch 时给出**速度警告**（不算失败）
#   3 路径      .model-cache 基座与 vendor 中的 adapter_model.safetensors 齐备
#   4 冒烟      deai 4 句、kw 4 词，标签须命中已知答案（评测口径同款判据）
#
# 首次启动会按 SMALL_MODEL_* / HF_ENDPOINT 配置联网下载；离线重跑前必须已准备缓存。
# 依赖安装（CUDA 13 索引，本机 sm_120 必需；旧 cu121/cu124 轮子不含该架构）：
#   uv run --with torch --with transformers --with peft \
#       --index-url https://download.pytorch.org/whl/cu130 python scripts/selftest_small_model.py
#   或直接：小模型/.venv/Scripts/python.exe scripts/selftest_small_model.py
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import pathlib
import sys
import time

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

FAIL = []
WARN = []


def _ok(msg):
    print(f"  [OK] {msg}")


def _bad(msg):
    print(f"  [X]  {msg}")
    FAIL.append(msg)


def _warn(msg):
    print(f"  [!]  {msg}")
    WARN.append(msg)


def _note(msg):
    print(f"  [.]  {msg}")


def main() -> int:
    print("=" * 64)
    print("  小模型层自检（deai / kw @ Qwen3-0.6B）")
    print("=" * 64)

    import small_model as sm

    # ── 0 首次启动准备 ───────────────────────────────────────
    print("[0] 首次启动准备（依赖、基座下载、适配器配置）")
    try:
        sm.ensure_model_assets()
        _ok("模型资产已就绪")
    except sm.SmallModelError as e:
        _bad(str(e))
        return 1

    # ── 1 依赖 ──────────────────────────────────────────────
    print("[1] 依赖")
    torch = None
    try:
        import torch
        _ok(f"torch {torch.__version__}")
    except ImportError as e:
        _bad(f"缺 torch（{e}）")
    tf = peft = None
    try:
        import transformers
        tf = transformers.__version__
        _ok(f"transformers {tf}")
    except ImportError as e:
        _bad(f"缺 transformers（{e}）")
    try:
        import peft
        _ok(f"peft {peft.__version__}")
    except ImportError as e:
        _bad(f"缺 peft（{e}）")
    if FAIL:
        print("\n安装提示：见本文件头部；或直接用 小模型/.venv 的 python 重跑。")
        return 1

    # ── 2 设备 ──────────────────────────────────────────────
    print("[2] 设备（不写死：SMALL_MODEL_DEVICE 可指定 cuda/cuda:N/mps/cpu/auto，"
          "默认 auto 按 cuda→mps→cpu；CPU 允许但有速度警告）")
    if torch.cuda.is_available():
        cap = torch.cuda.get_device_capability()
        name = torch.cuda.get_device_name(0)
        _ok(f"CUDA 可用：{name}｜sm_{''.join(map(str, cap))}"
            f"｜torch 编译 CUDA {torch.version.cuda}")
        if cap[0] >= 12 and not torch.version.cuda.startswith("13"):
            _warn(f"sm_{cap[0]}{cap[1]} 是 Blackwell，当前 torch 编译于 CUDA "
                  f"{torch.version.cuda} —— 轮子不含该架构时会**静默落 CPU**（慢约 9 倍量级，"
                  f"功能不受影响）。要 GPU 速度请装 CUDA 13 索引："
                  "--index-url https://download.pytorch.org/whl/cu130")
    else:
        _warn("CUDA 不可用 → 走 CPU 推理：**受支持**，标签口径与结果不变，"
              "但吞吐低一到两个数量级（deai 逐句、kw 千级词型请按时长预估）。"
              "GPU 机器上可用 SMALL_MODEL_DEVICE 显式指定设备。")

    # ── 3 路径 ──────────────────────────────────────────────
    print("[3] 路径")
    p = sm.resolve_paths()
    if p["base"]:
        _ok(f"基座 {p['base']}")
    else:
        _bad("找不到基座 .hf-home/qwen3-06b-hf —— 设 SMALL_MODEL_ROOT 或 SMALL_MODEL_BASE")
    for task, ap in p["adapters"].items():
        if ap:
            _ok(f"{task} 适配器 {ap}")
        else:
            _bad(f"找不到 {task} 适配器 —— 设 SMALL_MODEL_ADAPTER_DIR"
                 f"（默认在 <root>/outputs/{sm.ADAPTER_NAMES[task]}）")
    if FAIL:
        _note("已探测的仓库根候选：" + "；".join(str(c) for c in sm._root_candidates()))
        return 1

    # ── 4 冒烟 ──────────────────────────────────────────────
    print("[4] 冒烟（标签须命中已知答案）")
    t0 = time.perf_counter()
    try:
        m = sm.SmallModel()
    except sm.SmallModelError as e:
        _bad(f"加载失败：{e}")
        return 1
    load_sec = time.perf_counter() - t0
    _note(f"加载耗时 {load_sec:.1f}s（device={m.device}）")

    deai_cases = [
        ("构建携带现代人类特有 NOVA1 I197V 替换的人源化小鼠，发现该替换特异性地改变脑内剪接并改变小鼠发声特征。", "OK"),
        ("该研究采用了多种先进方法，取得了显著成果，具有重要意义。", "EMPTY_FILLER"),
        ("研究测得720个剪接事件，受试者约三万人。", "NUMBER_CONVERSION"),
        ("两组差异显著，有望在未来转化为临床应用。", "SPECULATION_LEAK"),
    ]
    t0 = time.perf_counter()
    got = m.deai_classify([s for s, _ in deai_cases])
    dt = time.perf_counter() - t0
    for (s, want), lab in zip(deai_cases, got):
        if lab == want:
            _ok(f"deai → {lab}")
        else:
            _bad(f"deai 期望 {want} 实得 {lab}：{s[:24]}…")
    _note(f"deai 4 句 {dt:.1f}s")

    kw_cases = [
        ({"g": "神经元", "n": 57, "n_docs": 12, "left": ["海", "大", "小"],
          "right": ["的", "网络", "胞体"]}, "term"),
        ({"g": "经元", "n": 57, "n_docs": 12, "left": ["神"],
          "right": ["的", "网络"]}, "fragment"),
        ({"g": "突触前", "n": 17, "n_docs": 8, "left": ["在", "是"],
          "right": ["膜", "释放", "终端"]}, "term"),
        ({"g": "养基", "n": 9, "n_docs": 5, "left": ["培"],
          "right": ["中", "里"]}, "fragment"),
    ]
    t0 = time.perf_counter()
    got = m.kw_classify([c for c, _ in kw_cases])
    dt = time.perf_counter() - t0
    for (c, want), lab in zip(kw_cases, got):
        if lab == want:
            _ok(f"kw {c['g']} → {lab}")
        else:
            _bad(f"kw {c['g']} 期望 {want} 实得 {lab}")
    _note(f"kw 4 词 {dt:.1f}s")

    if FAIL:
        print(f"\n[X] 自检失败 {len(FAIL)} 项")
        return 1
    print("\n[OK] 模型层自检全部通过 —— check_interpretation.py --model 与 "
          "knowledge_graph.py --kw-model 可用")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
