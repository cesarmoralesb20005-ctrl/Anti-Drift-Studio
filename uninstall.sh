#!/usr/bin/env bash
set -euo pipefail

echo "Deteniendo y eliminando servicio xbox-antidrift..."
systemctl --user disable --now xbox-antidrift.service >/dev/null 2>&1 || true
rm -f "$HOME/.config/systemd/user/xbox-antidrift.service"
systemctl --user daemon-reload >/dev/null 2>&1 || true

rm -f "$HOME/.local/bin/xbox-antidrift" "$HOME/.local/bin/xbox-antidrift.c" "$HOME/.local/bin/antidrift-save-clip"
rm -f "$HOME/.local/share/applications/xbox-antidrift-gui.desktop"

echo "Desinstalado correctamente. Tus configuraciones en ~/.config/xbox-antidrift*.conf se conservaron."
