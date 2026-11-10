# Local workflow entrypoint. Model commands use a separate explicit interpreter.
param(
  [Parameter(Position=0)][string]$Script = "doctor",
  [switch]$Model,
  [Parameter(ValueFromRemainingArguments=$true)][string[]]$ScriptArgs
)
$ErrorActionPreference = 'Stop'
$flowRoot = $PSScriptRoot
$corePython = Join-Path $flowRoot '.venv\Scripts\python.exe'
if ($Model) {
  $pythonPath = $env:SMALL_MODEL_PYTHON
  if (-not $pythonPath) {
    $candidate = Join-Path (Split-Path (Split-Path $flowRoot)) '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $candidate) { $pythonPath = $candidate }
  }
  if (-not $pythonPath -or -not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Set SMALL_MODEL_PYTHON to the Python interpreter with torch/transformers/peft installed.'
  }
} else { $pythonPath = $corePython }
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Missing local .venv; create it and install requirements-visual.txt first.' }
if ($Script -eq 'doctor') {
  & $pythonPath -c 'import sys; print(sys.executable); import pypdf, fitz, websocket, websockets, matplotlib, networkx, igraph, adjustText, wordcloud, jsonschema; print("Core/PDF/CDP/graph imports: OK")'
  exit $LASTEXITCODE
}
$scriptPath = if ([IO.Path]::IsPathRooted($Script)) { $Script } else { Join-Path $flowRoot $Script }
if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) { throw "Script not found: $scriptPath" }
& $pythonPath $scriptPath @ScriptArgs
exit $LASTEXITCODE
