# llm_lifecycle —— 便携的 llama.cpp 服务启停与**精准释放**

> 目的：每次跑完实验，**只卸载本次用的模型权重、只关本次起的服务**，
> 绝不动系统上其他正在运行的模型 / llama / ollama 服务，并顺手清掉临时文件。
> 不依赖绝对路径；换模型、换 harness、换电脑都能跑。

---

## 一、为什么"精准"需要专门设计（本机实测依据）

先查清了本环境的**可用信号**，再定方案 —— 这些结论都是实测，不是假设：

| 识别手段 | 是否可用 | 证据 |
|---|---|---|
| `Get-CimInstance Win32_Process` 的 `CommandLine` | ❌ **恒为空** | 与 `QWEN06B_TASKS.md` §7.3 陷阱 1 一致，本次复测仍为空 |
| `(Get-Process).CommandLine` | ❌ 空 | 同上 |
| `(Get-Process).Path` | ✅ 可靠 | 能正确读到 `llamacpp\llama-server.exe` |
| 端口 → PID 反查（netstat） | ✅ 仅"正在监听"时 | 实测 8081 → 19244 |
| `/props` HTTP 探测 | ✅ 可靠 | 返回 `model_path` / `n_ctx`，可用于身份核实 |
| llama.cpp 的**权重卸载端点** | ❌ **不存在** | `/metrics` 返回 **501 Not Implemented**，CLI 无 unload 选项 |

**由此推出两条设计决定：**

1. **"卸载权重"在这个后端上等价于"终止本次服务进程"** —— 权重随进程释放。
   （命令仍保留 `--unload-url`，将来换到支持 unload 的后端可直接挂卸载报文。）
2. **不能靠"事后扫描"找自己的进程**（WMI 不可用），
   必须**签发时登记**：启动瞬间把 `PID + 唯一 api-key + 端口 + 进程创建时间` 写进 pin 文件。

---

## 二、四重校验：宁可漏杀，不可误杀

释放时只认 pin 里的 PID，并逐项校验，任一不符就**拒绝终止**并报错：

| 校验 | 防的是什么 |
|---|---|
| ① PIN 存在且进程活着 | 空指针/失效记录 |
| ② **进程创建时间**与登记值一致（±2s） | **PID 被系统回收后复用** —— 这是最危险的一种误杀 |
| ③ **可执行文件路径**与登记一致（`os.path.samefile`） | 伪造 pin / 张冠李戴 |
| ④ （可选）`/props` 能应答且带本次的 api-key | 端口被别的服务占用 |

### 安全测试（全部实测通过）

| 场景 | 结果 |
|---|---|
| **A. 伪造 pin 指向 `pwsh` 进程** | ✅ **拒绝**：`可执行文件不符（pwsh.exe vs llama-server.exe）—— 不杀任何进程`；pwsh 存活 |
| **B. pin 声称的创建时间与实际不符（PID 复用）** | ✅ **拒绝**：`PID 已被复用（创建时间不符）`；19244 存活 |
| **C. pin 指向已消失的 PID** | ✅ 跳过并清理失效 pin |
| **D. `release --all`** | ✅ 只回收本工具有 pin 的进程；**无 pin 的 19244 原封不动** |

---

## 三、实测的隔离效果（关键证据）

测试时系统上同时存在：

- **PID 19244** —— 我早先手工起的 0.6B，占 8081，8.9 GB（**不是我这次启的**）
- **8082 上的服务** —— 另一个非 llama.cpp 的进程（**不是我的**）

执行 `serve`（8083）→ `release`：

```
释放前: 7204(1028MB)  19244(8989MB)
释放:   [OK] 已终止 pid=7204（优雅退出）
        [OK] 清理临时文件 3 项：demo-run.out.log、demo-run.err.log、demo-run.json
释放后: 19244(8989MB)          ← 完全没动
端口:   8081 占用、8082 占用、8083 空闲
```

**只释放了本次的 7204；19244 与 8082 上的服务全程未受影响。**

---

## 四、崩溃也能恢复：pin 文件是"回收凭据"

包装器被**硬杀**（Ctrl-C 到不了 `finally`、进程崩溃、断电）时，服务会成为孤儿。
实测确认了这一限制，并给出对策 —— pin 文件仍在，所以可以精准回收：

```
$ ./llm sweep --dry-run
pin 总数 1｜存活孤儿 1｜失效 0
  回收 pid=21968 端口=8087 模型=Qwen3-0.6B-Q8_0.gguf 占用≈1071 MB
（--dry-run：未实际删除任何东西）

$ ./llm sweep
[OK] 已终止 pid=21968（优雅退出）
[OK] 清理临时文件 3 项：…
回收完成，释放约 1071 MB。**未触碰任何非本工具启动的进程。**
```

`sweep` 同时清理：孤儿服务、**失效 pin**、**遗留日志**。

---

## 五、便携性（换模型 / 换 harness / 换电脑）

| 维度 | 做法 |
|---|---|
| **不依赖绝对路径** | 工作区由"向上找 `llamacpp/`、`流程1/`、`.venv/` 特征目录"推断；可用 `PAPER_WORKDIR`/`LLM_WORKSPACE` 覆盖 |
| **模型** | 按名字**模糊匹配**任何 `.gguf`（精确名 → 前缀 → 包含）；也可 `--model-path` 或 `LLM_MODEL` |
| **llama.cpp** | 依次尝试 `llamacpp/`、`llama.cpp/build/bin/`、`bin/`、`PATH`；或 `--llama-server` / `LLAMA_SERVER` |
| **依赖自举** | 缺 `psutil` 自动装；**venv 无 pip 时自动退到 `uv`**（本机 `.venv` 实测就没有 pip） |
| **端口** | 自动挑空闲端口；**指定端口被占用时拒绝启动**（防 `QWEN06B_TASKS.md` 陷阱 2 的端口复用误判） |
| **harness 无关** | `run_with_llm.py` 通过环境变量注入 `OPENAI_BASE_URL`/`LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`，任何 OpenAI 兼容客户端都能接 |
| **跨平台入口** | `llm.ps1`（Windows）+ `llm`（bash，LF 换行）；都自动找解释器 |

---

## 六、用法

### 入口

```powershell
.\llm.ps1 doctor                       # 环境自检：依赖/二进制/模型/端口/pin
.\llm.ps1 serve --model Qwen3-0.6B     # 启动并登记 pin
.\llm.ps1 status                       # 列出本工具启动的服务（含状态）
.\llm.ps1 release --latest             # 精准释放最近一次
.\llm.ps1 release --all                # 释放全部由本工具启动的
.\llm.ps1 sweep [--dry-run]            # 回收孤儿 + 失效 pin + 遗留日志
.\llm.ps1 run --model Qwen3-0.6B -- python my_experiment.py
```

```bash
./llm doctor | serve | status | release | sweep | run     # Linux/macOS/Git-Bash
```

### Python 侧（推荐：进程崩了也会释放）

```python
from run_with_llm import llm_session

with llm_session(model="Qwen3-0.6B", port=8083, ctx=32768) as s:
    print(s["api_url"], s["api_key"])
    run_my_experiment(base_url=s["base_url"], api_key=s["api_key"])
# 退出 with → 终止本次进程 + 清日志/pin（异常与中断同样会走）
```

### 包装**已有**脚本，一行不改

```powershell
.\llm.ps1 run --model Qwen3-0.6B --port 8084 -- `
    python ..\zjy_kwcloud\extract_keywords_zjy.py
```

实测：子命令真实推理成功（`ANSWER: 1`）→ 自动释放 → 端口归还。
**子命令退出码非 0 时同样释放**（实测退出码 3，服务仍被回收）。

### 卸载报文（可选，面向将来支持 unload 的后端）

```powershell
.\llm.ps1 release --latest --unload-url "http://127.0.0.1:8000/unload?port={port}"
```

实测报文正确：
```
PATH: /unload?port=8088
AUTH: Bearer llc-b91cc072ced74738890ef02416575c45
BODY: {"model": "Qwen3-0.6B-Q8_0"}
```
（URL 模板 `{port}` 已替换、带上本次唯一 key、body 带模型别名。发送后再走进程终止。）

### 环境变量（全部可选）

| 变量 | 作用 |
|---|---|
| `PAPER_WORKDIR` / `LLM_WORKSPACE` | 指定工作区 |
| `LLM_MODEL` / `LLM_MODEL_DIR` | 指定模型文件 / 模型目录 |
| `LLAMA_SERVER` / `LLAMA_SERVER_BIN` | 指定 llama-server |
| `LLM_STATE_DIR` | pin 与日志落点（默认 `<工作区>/.llm_lifecycle`） |
| `LLM_PYTHON` | 指定解释器（两个入口脚本都用） |

---

## 七、pin 文件里存了什么

`<工作区>/.llm_lifecycle/pins/<tag>.json`：

```json
{
 "tag": "demo-run",
 "pid": 7204,
 "pid_started": 1791790465.31,        // 进程创建时间 → 防 PID 复用
 "port": 8083,
 "api_key": "llc-…",                  // 本次唯一 → 归属证据
 "alias": "Qwen3-0.6B-Q8_0",
 "model": "…\\Qwen3-0.6B-Q8_0.gguf",
 "llama_server": "…\\llama-server.exe",
 "ctx": 8192, "ngl": 99, "slots": 1,
 "started_at": "2026-10-12T20:08:41",
 "argv": ["…"],
 "out_log": "…", "err_log": "…",
 "host": "…", "workspace": "…"
}
```

日志与 pin 都在 `<工作区>/.llm_lifecycle/` 下 —— **随工作区走，不污染系统临时目录**。

---

## 八、产物

```
eval_2to4b/llm_lifecycle/
├─ llm_lifecycle.py    核心：serve / status / release / sweep / doctor
├─ run_with_llm.py     上下文管理器 + 命令行包装器（不改被包装脚本）
├─ llm.ps1             Windows 入口（不依赖绝对路径，自动找解释器）
├─ llm                 bash 入口（LF 换行；Linux/macOS/Git-Bash）
└─ probe_env.py        环境能力巡检（当初用来确定识别信号）
```

---

## 九、局限（如实说明）

1. **bash 入口 `llm` 未能在本机执行验证** —— 本沙箱里 WSL 与 Git-Bash
   都无法创建管道（`E_ACCESSDENIED` / `couldn't create signal pipe`）。
   已做**静态检查**（LF 换行、语法审阅），但**没有跑通**。
   请在 Linux/macOS 上先跑一次 `./llm doctor` 确认。
2. **硬杀包装器后不会自动释放** —— 这是进程模型的固有限制（`finally`/`atexit` 都到不了）。
   对策是 `sweep`，需要**手动或由上层脚本调用**一次。
   若要做到自动，需把 `sweep` 挂到下次运行的开头（本工具未默认这么做，避免动到别人的进程）。
3. **llama.cpp 无权重卸载端点**，故"卸载权重"= 终止进程。
   若你的部署是"一个 llama-server 里多模型热切换"，本工具的进程级释放不适用，
   需要用 `--unload-url` 指向你后端真正的卸载接口。
4. **PID 复用防护的窗口是 ±2 秒**。极端情况下（PID 恰好在 2 秒内被回收并分配给
   另一个 llama-server）仍可能误判；再加可执行文件校验后风险已很低。
   要更稳可在 pin 里存进程的 `cmdline`（但本环境 WMI 拿不到，故未用）。
5. **未测**：多实例并发启动（同一状态目录）、网络驱动器上的工作区、
   容器内的 PID namespace 行为。
