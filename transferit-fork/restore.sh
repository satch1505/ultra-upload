#!/usr/bin/env bash
set -euo pipefail

# Restaura el fork personal de transferit sobre la instalación pipx.
# Sustituye transferit/ y transferit_cli/ en el site-packages del venv.

VENV="${TRANSFERIT_VENV:-/root/.local/share/pipx/venvs/transferit-py}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Localiza el site-packages (soporta varias versiones de python).
SITE="$(ls -d "$VENV"/lib/python*/site-packages | head -n 1)"
if [ -z "$SITE" ]; then
    echo "ERROR: no se encontró site-packages en $VENV" >&2
    exit 1
fi

echo "Venv:   $VENV"
echo "Site:   $SITE"
echo "Fork:   $HERE"

for pkg in transferit transferit_cli; do
    rm -rf "$SITE/$pkg"
    cp -r "$HERE/$pkg" "$SITE/$pkg"
    echo "  + restaurado $pkg"
done

# Sanity-check: confirma que los valores del fork están activos.
"$VENV/bin/python" -c "
from transferit._upload import DEFAULT_CONCURRENCY, WS_BUFFER_LIMIT, WS_ACK_WINDOW, _WsDisconnect
assert DEFAULT_CONCURRENCY == 4, DEFAULT_CONCURRENCY
assert WS_BUFFER_LIMIT == 5_000_000, WS_BUFFER_LIMIT
assert WS_ACK_WINDOW == 1_500_000, WS_ACK_WINDOW
print('OK: fork active')
"
