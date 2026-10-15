#!/usr/bin/env bash
# Abre radialcmd con: mantener botón lateral + click izquierdo.
# Requiere haber ejecutado antes ./install.sh
# Uso: ./install-mouse.sh                 (botón lateral "adelante": BTN_EXTRA)
#      ./install-mouse.sh BTN_SIDE        (otro botón; míralo con --detect)
#      ./install-mouse.sh BTN_EXTRA none  (solo el botón lateral, sin click)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HOME/.local/bin"
UNIT_DIR="$HOME/.config/systemd/user"
MOD="${1:-BTN_EXTRA}"
TRIGGER="${2:-BTN_LEFT}"

echo "→ Dependencias (python3-evdev)"
sudo apt-get install -y python3-evdev

echo "→ Permisos: ratón virtual (uinput) y lectura del ratón (grupo input)"
echo 'KERNEL=="uinput", SUBSYSTEM=="misc", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' \
  | sudo tee /etc/udev/rules.d/99-radialcmd-uinput.rules >/dev/null
echo uinput | sudo tee /etc/modules-load.d/radialcmd-uinput.conf >/dev/null
sudo modprobe uinput
sudo udevadm control --reload-rules
sudo udevadm trigger --name-match=uinput || true
NEED_RELOGIN=0
if ! id -nG "$USER" | grep -qw input; then
  sudo usermod -aG input "$USER"
  NEED_RELOGIN=1
fi

mkdir -p "$BIN" "$UNIT_DIR"
install -m 755 "$HERE/radialcmd-mouse.py" "$BIN/radialcmd-mouse"

cat > "$UNIT_DIR/radialcmd-mouse.service" <<EOF
[Unit]
Description=radialcmd: abrir menú con botón lateral + click
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Environment=RADIALCMD_MOD=$MOD
Environment=RADIALCMD_TRIGGER=$TRIGGER
ExecStart=$BIN/radialcmd-mouse
Restart=always
RestartSec=3

[Install]
WantedBy=graphical-session.target
EOF

systemctl --user daemon-reload
systemctl --user enable radialcmd-mouse.service

if [[ $NEED_RELOGIN == 1 ]]; then
  echo
  echo "Listo. CIERRA SESIÓN Y VUELVE A ENTRAR (te he añadido al grupo 'input');"
  echo "el servicio arrancará solo al entrar."
else
  systemctl --user restart radialcmd-mouse.service
  echo "Listo. Combo: mantener $MOD + $TRIGGER"
fi
echo "Estado: systemctl --user status radialcmd-mouse"
