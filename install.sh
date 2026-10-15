#!/usr/bin/env bash
# Instala radialcmd (cliente + servicio en segundo plano) y crea un atajo de
# teclado en GNOME.
# Uso: ./install.sh                 (atajo por defecto: Ctrl+Alt+C)
#      ./install.sh '<Super>x'      (otro atajo)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HOME/.local/bin"
CFG="$HOME/.config/radialcmd"
UNIT_DIR="$HOME/.config/systemd/user"
BINDING="${1:-<Ctrl><Alt>c}"

echo "→ Dependencias (python3-tk, python3-pil, wl-clipboard, xclip, xdotool)"
sudo apt-get install -y python3-tk python3-pil wl-clipboard xclip xdotool

mkdir -p "$BIN" "$CFG" "$UNIT_DIR"
install -m 755 "$HERE/radialcmd"        "$BIN/radialcmd"
install -m 755 "$HERE/radialcmd-daemon" "$BIN/radialcmd-daemon"
install -m 644 "$HERE/radialcmd_core.py" "$BIN/radialcmd_core.py"
echo "→ Instalado en $BIN"

if [[ -f "$CFG/comandos.txt" ]]; then
  echo "→ Ya existe $CFG/comandos.txt, no lo sobrescribo"
else
  cp "$HERE/comandos.example.txt" "$CFG/comandos.txt"
  echo "→ Comandos de ejemplo en $CFG/comandos.txt"
fi

echo "→ Aviso del directorio de la terminal (para los archivos .radialcmd por proyecto)"
python3 "$BIN/radialcmd" --hook

echo "→ Servicio en segundo plano (para que el menú se abra al instante)"
cat > "$UNIT_DIR/radialcmd.service" <<EOF
[Unit]
Description=radialcmd: servicio del menú radial de comandos
PartOf=graphical-session.target
After=graphical-session.target

[Service]
ExecStart=$BIN/radialcmd-daemon
Restart=always
RestartSec=2

[Install]
WantedBy=graphical-session.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now radialcmd.service
sleep 0.3
if systemctl --user is-active --quiet radialcmd.service; then
  echo "→ Servicio activo (arranca solo al iniciar sesión)"
else
  echo "→ Aviso: el servicio no ha arrancado; 'radialcmd' lo levantará él solo"
  echo "  la primera vez que lo uses (un poco más lento esa vez). Para ver el"
  echo "  error: systemctl --user status radialcmd"
fi

SCHEMA=org.gnome.settings-daemon.plugins.media-keys
KEY_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/radialcmd/"
if command -v gsettings >/dev/null && gsettings list-schemas | grep -qx "$SCHEMA"; then
  existing="$(gsettings get "$SCHEMA" custom-keybindings)"
  if [[ "$existing" != *"$KEY_PATH"* ]]; then
    if [[ "$existing" == "@as []" || "$existing" == "[]" ]]; then
      new="['$KEY_PATH']"
    else
      new="${existing%]*}, '$KEY_PATH']"
    fi
    gsettings set "$SCHEMA" custom-keybindings "$new"
  fi
  S="$SCHEMA.custom-keybinding:$KEY_PATH"
  gsettings set "$S" name 'Menú radial de comandos'
  gsettings set "$S" command "$BIN/radialcmd"
  gsettings set "$S" binding "$BINDING"
  echo "→ Atajo GNOME: $BINDING"
else
  echo "→ No es GNOME: asigna '$BIN/radialcmd' a un atajo a mano"
fi

echo "Listo. Edita tus comandos con: radialcmd --edit"
echo "Comprobar velocidad: time radialcmd   (y Esc para cerrar)"
