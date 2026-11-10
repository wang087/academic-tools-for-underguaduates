#!/usr/bin/env bash
# Compatibility entrypoint; renew requires a verified owned registry record.
set -euo pipefail
SDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ $# -eq 0 || "${1:-}" == --help || "${1:-}" == -h ]]; then
  echo 'Usage: renew_cdp_instance.sh WORKDIR [PORT=9222] [TAG] [--browser PATH]'
  echo 'Keeps the registered binary, level and extra args unless explicitly overridden.'
  exit 0
fi
exec bash "${SDIR}/start_edge_cdp.sh" --renew "$@"
