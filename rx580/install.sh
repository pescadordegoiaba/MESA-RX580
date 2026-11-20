#!/usr/bin/env bash
# Build the MESA-RX580 RADV driver and install it next to the settings panel.
# Default destination is ~/.local. Pass --system to use /usr/local.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
SESSION=1
SYSTEM=0

usage() {
    cat <<EOF
Uso: $0 [--prefix DIR] [--no-session] [--system]

  --prefix DIR   Onde instalar. Padrão: ~/.local
  --no-session   Não grava VK_DRIVER_FILES na sessão do usuário
  --system       Instala em /usr/local (pede sudo se precisar)

O painel fica em \$PREFIX/bin/mesa-rx580-panel.
O driver fica em \$PREFIX/lib/mesa-rx580/libvulkan_radeon.so.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix)
            PREFIX="$2"
            shift 2
            ;;
        --no-session)
            SESSION=0
            shift
            ;;
        --system)
            SYSTEM=1
            PREFIX="/usr/local"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Opção desconhecida: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ ! -f "$ROOT/meson.build" ]]; then
    echo "Não achei o fonte do Mesa em $ROOT" >&2
    exit 1
fi

need() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Falta o comando '$1'." >&2
        exit 1
    fi
}

need meson
need ninja
need python3
python3 -c 'import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk' \
    || { echo "Falta GTK 4 para Python (python-gobject e gtk4)." >&2; exit 1; }

BUILD="$ROOT/build"
if [[ ! -f "$BUILD/build.ninja" ]]; then
    echo "Configurando o build do RADV..."
    meson setup "$BUILD" "$ROOT" \
        -Dvulkan-drivers=amd \
        -Dgallium-drivers= \
        -Dplatforms=x11,wayland \
        -Dvulkan-layers= \
        -Dllvm=disabled \
        -Dandroid-libbacktrace=disabled \
        -Dvideo-codecs=all \
        -Dgallium-va=disabled \
        -Dgallium-rusticl=false \
        -Dgles1=disabled \
        -Dgles2=disabled \
        -Dglx=disabled \
        -Degl=disabled \
        -Dgbm=disabled \
        -Dtools=[] \
        -Dbuildtype=debugoptimized \
        --wrap-mode=nofallback
fi

echo "Compilando libvulkan_radeon.so..."
ninja -C "$BUILD" src/amd/vulkan/libvulkan_radeon.so

LIB_SRC="$BUILD/src/amd/vulkan/libvulkan_radeon.so"
if [[ ! -f "$LIB_SRC" ]]; then
    echo "A biblioteca não foi gerada em $LIB_SRC" >&2
    exit 1
fi

install_file() {
    local mode="$1" src="$2" dest="$3"
    mkdir -p "$(dirname "$dest")"
    if [[ $SYSTEM -eq 1 && ! -w "$(dirname "$dest")" ]]; then
        sudo install -m "$mode" "$src" "$dest"
    else
        install -m "$mode" "$src" "$dest"
    fi
}

LIB_DIR="$PREFIX/lib/mesa-rx580"
ICD_DIR="$PREFIX/share/vulkan/icd.d"
BIN_DIR="$PREFIX/bin"
APP_DIR="$PREFIX/share/applications"

install_file 755 "$LIB_SRC" "$LIB_DIR/libvulkan_radeon.so"
install_file 755 "$ROOT/rx580/mesa-rx580-panel.py" "$BIN_DIR/mesa-rx580-panel"

ICD_BODY="$(cat <<EOF
{
    "file_format_version": "1.0.1",
    "ICD": {
        "library_path": "$LIB_DIR/libvulkan_radeon.so",
        "api_version": "1.4.330"
    }
}
EOF
)"
ICD_TMP="$(mktemp)"
printf '%s\n' "$ICD_BODY" >"$ICD_TMP"
install_file 644 "$ICD_TMP" "$ICD_DIR/mesa_rx580_icd.json"
rm -f "$ICD_TMP"

DESKTOP_TMP="$(mktemp)"
cat >"$DESKTOP_TMP" <<EOF
[Desktop Entry]
Name=MESA-RX580
Comment=Painel de ajustes do RADV para a RX 580
Exec=$BIN_DIR/mesa-rx580-panel
Icon=video-display
Terminal=false
Type=Application
Categories=Settings;HardwareSettings;
EOF
install_file 644 "$DESKTOP_TMP" "$APP_DIR/mesa-rx580-panel.desktop"
rm -f "$DESKTOP_TMP"

ENV_FILE="$HOME/.config/environment.d/99-mesa-rx580.conf"
if [[ $SESSION -eq 1 ]]; then
    mkdir -p "$(dirname "$ENV_FILE")"
    cat >"$ENV_FILE" <<EOF
# MESA-RX580: usa só este ICD Vulkan. Apague este arquivo para voltar ao driver do sistema.
VK_DRIVER_FILES=$ICD_DIR/mesa_rx580_icd.json
EOF
    echo "Sessão: $ENV_FILE"
    echo "Entre de novo na sessão gráfica para os programas novos verem o driver."
else
    echo "VK_DRIVER_FILES não foi gravado. Para usar o driver nesta shell:"
    echo "  export VK_DRIVER_FILES=$ICD_DIR/mesa_rx580_icd.json"
fi

echo
echo "Driver: $LIB_DIR/libvulkan_radeon.so"
echo "ICD:    $ICD_DIR/mesa_rx580_icd.json"
echo "Painel: $BIN_DIR/mesa-rx580-panel"
echo "Os ajustes do painel ficam em ~/.drirc e passam a valer no próximo processo Vulkan."
