# llm 生命周期入口（Windows PowerShell）
# =====================================================================
# 不依赖绝对路径：相对本脚本定位，工作区/模型/二进制由 python 端推断。
# 用法：
#   .\llm.ps1 doctor
#   .\llm.ps1 serve --model Qwen3-0.6B
#   .\llm.ps1 status
#   .\llm.ps1 release --latest
#   .\llm.ps1 sweep                  # 回收被硬杀后残留的孤儿服务
#   .\llm.ps1 run --model Qwen3-0.6B -- python my_experiment.py
param(
    [Parameter(Position = 0)][string]$Cmd = "help",
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest
)
$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$self = $PSScriptRoot

function Find-Python {
    if ($env:LLM_PYTHON -and (Test-Path $env:LLM_PYTHON)) { return $env:LLM_PYTHON }
    # 向上找本仓库自带 venv
    $d = $self
    for ($i = 0; $i -lt 5; $i++) {
        $d = Split-Path $d -Parent
        if (-not $d) { break }
        foreach ($c in @("$d\.venv\Scripts\python.exe", "$d\.venv\bin\python")) {
            if (Test-Path $c) { return $c }
        }
    }
    # 退回 PATH
    foreach ($c in @("python", "python3", "py")) {
        $g = Get-Command $c -ErrorAction SilentlyContinue
        if ($g) { return $g.Source }
    }
    throw "[X] 找不到 python 解释器。设 `$env:LLM_PYTHON 指定。"
}

$py = Find-Python
switch ($Cmd) {
    { $_ -in "serve", "status", "release", "doctor", "sweep" } {
        & $py (Join-Path $self "llm_lifecycle.py") $Cmd @Rest
    }
    "run" {
        & $py (Join-Path $self "run_with_llm.py") @Rest
    }
    default {
        Write-Host "llm —— 便携 llama.cpp 生命周期入口"
        Write-Host "  解释器: $py"
        Write-Host "  子命令: doctor | serve | status | release | sweep | run"
        Write-Host ""
        Write-Host "例："
        Write-Host "  .\llm.ps1 doctor"
        Write-Host "  .\llm.ps1 serve --model Qwen3-0.6B"
        Write-Host "  .\llm.ps1 run --model Qwen3-0.6B -- python ..\zjy_kwcloud\extract_keywords_zjy.py"
        Write-Host "  .\llm.ps1 release --latest"
        Write-Host "  .\llm.ps1 sweep"
    }
}
