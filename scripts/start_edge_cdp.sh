#!/usr/bin/env bash
# Compatibility entrypoint. Lifecycle and ownership checks live in cdp_pool.py.
# Usage: start_edge_cdp.sh WORKDIR [BASE=9222] [COUNT=1] [LEVEL=0] [--browser PATH]
set -euo pipefail
SDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ $# -eq 0 || "${1:-}" == --help || "${1:-}" == -h ]]; then
  echo 'Usage: start_edge_cdp.sh WORKDIR [BASE=9222] [COUNT=1] [LEVEL=0] [--browser PATH]'
  echo 'Generic CLI: python scripts/cdp_pool.py {up|status|down|renew|kill|clean} --help'
  exit 0
fi
PY=()
if [[ -n "${PAPER_PYTHON:-}" ]]; then
  PYTHON_EXE="${PAPER_PYTHON}"
  if command -v cygpath >/dev/null 2>&1; then PYTHON_EXE="$(cygpath -u "$PYTHON_EXE")"; fi
  if [[ ! -f "$PYTHON_EXE" ]] && ! command -v "$PYTHON_EXE" >/dev/null 2>&1; then
    echo '[ERROR] PAPER_PYTHON does not identify a Python executable.' >&2; exit 1
  fi
  PY=("$PYTHON_EXE")
elif command -v uv >/dev/null 2>&1; then
  PY=(uv run --with websocket-client python)
elif command -v python3 >/dev/null 2>&1; then
  PY=(python3)
elif command -v python >/dev/null 2>&1; then
  PY=(python)
else
  echo '[ERROR] Python not found. Set PAPER_PYTHON or install Python (websocket-client required).' >&2; exit 1
fi
if ! "${PY[@]}" -c 'import sys; assert sys.version_info >= (3, 10); import websocket' ; then
  echo '[ERROR] Chosen Python needs Python >=3.10 and websocket-client; set PAPER_PYTHON to the intended environment.' >&2; exit 1
fi
if [[ "$1" == --renew ]]; then
  shift
  WORKDIR="${1:-$(pwd)}"; [[ $# -eq 0 ]] || shift
  PORT=9222; TAG=''
  if [[ $# -gt 0 && "$1" != --* ]]; then PORT="$1"; shift; fi
  if [[ $# -gt 0 && "$1" != --* ]]; then TAG="$1"; shift; fi
  exec "${PY[@]}" "${SDIR}/cdp_pool.py" renew --workdir "$WORKDIR" --port "$PORT" --tag "$TAG" "$@"
fi
WORKDIR="$1"; shift; BASE=9222; COUNT=1; LEVEL="${PAPER_CDP_LEVEL:-0}"
if [[ $# -gt 0 && "$1" != --* ]]; then BASE="$1"; shift; fi
if [[ $# -gt 0 && "$1" != --* ]]; then COUNT="$1"; shift; fi
if [[ $# -gt 0 && "$1" != --* ]]; then LEVEL="$1"; shift; fi
exec "${PY[@]}" "${SDIR}/cdp_pool.py" up --workdir "$WORKDIR" --base "$BASE" --n "$COUNT" --levels "$LEVEL" "$@"
