#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
APPS_DIR="$HOME/.local/share/applications"
MODELS_DIR="$HOME/.local/share/antidrift-studio/models"

echo "=== Instalador de Anti-Drift Studio (Universal Linux) ==="

run_as_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif command -v pkexec >/dev/null 2>&1 && { [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; } && ! [ -t 0 ]; then
        pkexec "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        echo "Error: se requieren permisos root/sudo para instalar: $*" >&2
        exit 1
    fi
}

install_system_packages() {
    local id_line=""
    if [ -r /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        id_line="${ID:-} ${ID_LIKE:-}"
    fi

    if command -v apt-get >/dev/null 2>&1; then
        echo "Distro Debian/Ubuntu detectada: instalando paquetes base..."
        run_as_root apt-get update
        run_as_root apt-get install -y \
            gcc libc6-dev python3 python3-venv python3-pip \
            flatpak pulseaudio-utils pipewire-bin \
            libxcb-cursor0 libegl1 libgl1 curl unzip
    elif command -v pacman >/dev/null 2>&1; then
        echo "Distro Arch/Manjaro detectada: instalando paquetes base..."
        run_as_root pacman -S --needed --noconfirm \
            gcc python flatpak libpulse pipewire \
            libxcb xcb-util-cursor libglvnd curl unzip
    elif command -v dnf >/dev/null 2>&1; then
        echo "Distro Fedora/RHEL detectada: instalando paquetes base..."
        run_as_root dnf install -y \
            gcc python3 python3-pip flatpak pulseaudio-utils pipewire-utils \
            xcb-util-cursor mesa-libEGL mesa-libGL curl unzip
    elif command -v zypper >/dev/null 2>&1; then
        echo "Distro openSUSE detectada: instalando paquetes base..."
        run_as_root zypper --non-interactive install \
            gcc python3 python3-pip flatpak pulseaudio-utils pipewire-tools \
            libxcb-cursor0 Mesa-libEGL1 Mesa-libGL1 curl unzip
    elif command -v xbps-install >/dev/null 2>&1; then
        echo "Distro Void Linux detectada: instalando paquetes base..."
        run_as_root xbps-install -Sy \
            gcc python3 python3-pip flatpak pulseaudio-utils pipewire \
            xcb-util-cursor MesaLib curl unzip
    elif command -v apk >/dev/null 2>&1; then
        echo "Distro Alpine detectada: instalando paquetes base..."
        run_as_root apk add \
            gcc musl-dev python3 py3-pip flatpak pulseaudio-utils pipewire \
            xcb-util-cursor mesa-egl mesa-gl curl unzip
    else
        echo "Distro no reconocida automáticamente ($id_line). Instala manualmente: gcc, python3, flatpak, pulseaudio-utils." >&2
        exit 1
    fi
}

echo "[0/5] Comprobando dependencias del sistema..."
NEED_SYS=0
for cmd in gcc python3 flatpak pactl; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        NEED_SYS=1
    fi
done
if ! command -v parec >/dev/null 2>&1 && ! command -v pw-record >/dev/null 2>&1; then
    NEED_SYS=1
fi
if command -v python3 >/dev/null 2>&1 && ! python3 -c "import venv" >/dev/null 2>&1; then
    NEED_SYS=1
fi
if [ "$NEED_SYS" -eq 1 ]; then
    install_system_packages
fi

mkdir -p "$BIN_DIR" "$SYSTEMD_USER_DIR" "$APPS_DIR" "$MODELS_DIR" "$HOME/.config"

echo "[1/5] Compilando servicio universal anti-drift..."
gcc -O2 -Wall -Wextra "$PROJECT_DIR/daemon/xbox-antidrift.c" -o "$BIN_DIR/xbox-antidrift" -lm
cp "$PROJECT_DIR/daemon/xbox-antidrift.c" "$BIN_DIR/xbox-antidrift.c"

install -m 0755 "$PROJECT_DIR/scripts/antidrift-save-clip" "$BIN_DIR/antidrift-save-clip"

echo "[2/5] Configurando servicio systemd --user..."
cat > "$SYSTEMD_USER_DIR/xbox-antidrift.service" <<EOF
[Unit]
Description=Universal Controller Anti-Drift & Macro Virtual Pad
After=graphical-session.target

[Service]
ExecStart=%h/.local/bin/xbox-antidrift
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload || true
    systemctl --user enable --now xbox-antidrift.service || true
    systemctl --user restart xbox-antidrift.service || true
fi

echo "[3/5] Preparando entorno Python (PySide6, Vosk, Jeepney)..."
if python3 -c "import PySide6, vosk, jeepney" >/dev/null 2>&1; then
    echo "Dependencias Python ya disponibles en el sistema."
else
    if [ ! -d "$PROJECT_DIR/.venv" ]; then
        python3 -m venv "$PROJECT_DIR/.venv"
    fi
    "$PROJECT_DIR/.venv/bin/pip" install --upgrade pip
    "$PROJECT_DIR/.venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"
fi

echo "[4/5] Verificando motor de Clips (GPU Screen Recorder) y modelo de voz..."
if ! command -v gpu-screen-recorder >/dev/null 2>&1; then
    if command -v flatpak >/dev/null 2>&1; then
        if ! flatpak info com.dec05eba.gpu_screen_recorder >/dev/null 2>&1; then
            echo "Instalando GPU Screen Recorder (Flatpak usuario)..."
            flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo || true
            flatpak install --user -y flathub com.dec05eba.gpu_screen_recorder || echo "Aviso: podrás instalarlo luego desde la pestaña Clips."
        fi
    else
        echo "Aviso: instala 'gpu-screen-recorder' o 'flatpak' para usar la sección Clips."
    fi
fi

if [ ! -d "$MODELS_DIR/vosk-model-small-es-0.42" ]; then
    echo "Descargando modelo de voz offline español (~38 MB)..."
    "$PROJECT_DIR/.venv/bin/python" -m antidrift.deps --install || true
fi

chmod +x "$PROJECT_DIR/run.sh"

echo "[5/5] Creando acceso directo en el menú de aplicaciones..."
cat > "$APPS_DIR/xbox-antidrift-gui.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Anti-Drift Studio
Comment=Calibración universal, macros, baja latencia y clips por voz
Exec=$PROJECT_DIR/run.sh
Icon=input-gaming
Terminal=false
Categories=Utility;Game;Settings;
EOF

if [ ! -w /dev/uinput ]; then
    echo ""
    echo "AVISO: Tu usuario no tiene permiso de escritura en /dev/uinput."
    echo "Ejecuta una vez este comando con sudo para habilitar mandos virtuales:"
    echo "  echo 'KERNEL==\"uinput\", MODE=\"0660\", GROUP=\"input\", TAG+=\"uaccess\"' | sudo tee /etc/udev/rules.d/99-antidrift-uinput.rules && sudo usermod -aG input \"\$USER\" && sudo udevadm control --reload-rules && sudo udevadm trigger"
fi

echo ""
echo "Instalación completada. Ejecuta '$PROJECT_DIR/run.sh' o abre 'Anti-Drift Studio' desde tu menú."
