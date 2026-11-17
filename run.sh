#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PY_BIN=""
if [ -x "$DIR/.venv/bin/python" ] && "$DIR/.venv/bin/python" -c "import PySide6, vosk, jeepney" >/dev/null 2>&1; then
    PY_BIN="$DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1 && python3 -c "import PySide6, vosk, jeepney" >/dev/null 2>&1; then
    PY_BIN="python3"
fi

if [ -z "$PY_BIN" ] || [ ! -x "$HOME/.local/bin/xbox-antidrift" ]; then
    echo "Faltan dependencias o el servicio nativo; instalando automáticamente..."
    if ! "$DIR/install.sh"; then
        if command -v python3 >/dev/null 2>&1; then
            python3 -m antidrift.deps --install
        else
            exit 1
        fi
    fi
    if [ -x "$DIR/.venv/bin/python" ]; then
        PY_BIN="$DIR/.venv/bin/python"
    else
        PY_BIN="python3"
    fi
fi

exec "$PY_BIN" "$DIR/app.py" "$@"
