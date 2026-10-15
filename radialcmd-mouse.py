#!/usr/bin/env python3
"""
radialcmd-mouse — abre radialcmd con un combo de ratón:
  mantener botón lateral (por defecto el de "adelante", BTN_EXTRA) + click izquierdo.

Cómo funciona: "agarra" el ratón físico (evdev grab) y reenvía todos sus eventos
a un ratón virtual (uinput), así que el ratón se comporta igual que siempre,
excepto que:
  - el click izquierdo hecho mientras mantienes el botón lateral NO llega a la
    ventana de debajo: abre el menú;
  - si pulsas el botón lateral solo (sin click), se reenvía al soltarlo, así que
    "adelante" en el navegador sigue funcionando.
Funciona igual en Wayland y en Xorg.

Uso:
  radialcmd-mouse --detect          muestra el nombre de cada botón que pulses
  radialcmd-mouse                   servicio (normalmente lo arranca systemd)
  radialcmd-mouse --mod BTN_SIDE    usar otro botón lateral
  radialcmd-mouse --trigger none    abrir el menú solo con el botón lateral
"""
import argparse
import errno
import os
import select
import socket
import subprocess
import sys
import time
from pathlib import Path

try:
    import evdev
    from evdev import ecodes as e
except ImportError:
    sys.exit("Falta python3-evdev:  sudo apt install python3-evdev")

VNAME = "radialcmd virtual mouse"
LAUNCH = [str(Path.home() / ".local/bin/radialcmd")]
RESCAN_S = 2.0


def code_of(name):
    if name.lower() == "none":
        return None
    try:
        return e.ecodes[name.upper()]
    except KeyError:
        sys.exit(f"Botón desconocido: {name}  (usa --detect para ver los nombres)")


def name_of(code):
    n = e.BTN.get(code) or e.KEY.get(code) or str(code)
    return "/".join(n) if isinstance(n, (list, tuple)) else n


SOCK = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "radialcmd.sock")


def launch():
    # directo al servicio por el socket (sin arrancar un proceso python nuevo);
    # si no responde, se lanza radialcmd, que sabe levantarlo
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        s.connect(SOCK)
        s.sendall(b"TOGGLE")
        if s.recv(16) == b"OK":
            return
    except OSError:
        pass
    finally:
        s.close()
    try:
        subprocess.Popen(LAUNCH, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as ex:
        print(f"No se pudo lanzar {LAUNCH[0]}: {ex}", file=sys.stderr)


def is_mouse(dev, need=()):
    if dev.name == VNAME:
        return False
    caps = dev.capabilities()
    keys, rels = caps.get(e.EV_KEY, []), caps.get(e.EV_REL, [])
    return e.BTN_LEFT in keys and e.REL_X in rels and all(c in keys for c in need)


# --------------------------------------------------------------- lógica combo
class Chord:
    """Decide qué eventos se reenvían. `out(ev)` escribe un evento al ratón
    virtual, `syn()` cierra el bloque, `fire()` abre el menú.
    El combo vale en los dos órdenes: lateral y luego click, o click
    mantenido y luego lateral."""

    def __init__(self, mod, trigger, out, syn, fire):
        self.mod, self.trigger = mod, trigger
        self.out, self.syn, self.fire = out, syn, fire
        self.mod_down = False
        self.trig_down = False                      # click reenviado y aún pulsado
        self.used = False
        self.swallow_up = False

    def handle(self, ev):
        if ev.type == e.EV_KEY and ev.code == self.mod:
            if ev.value == 1:                       # pulsado
                self.mod_down, self.used = True, False
                if self.trigger is None:            # modo "solo botón lateral"
                    self.used = True
                    self.fire()
                elif self.trig_down:                # click ya pulsado + lateral
                    # el click ya llegó a la ventana de debajo: se suelta en el
                    # ratón virtual (para no dejar un arrastre a medias) y se
                    # ignora su soltado real
                    self.used = self.swallow_up = True
                    self.trig_down = False
                    self.out(e.EV_KEY, self.trigger, 0); self.syn()
                    self.fire()
            elif ev.value == 0:                     # soltado
                self.mod_down = False
                if not self.used:                   # no hubo combo: reenvíalo
                    self.out(e.EV_KEY, self.mod, 1); self.syn()
                    self.out(e.EV_KEY, self.mod, 0); self.syn()
            return                                  # (value 2 = repetición: ignorar)

        if self.trigger is not None and ev.type == e.EV_KEY and ev.code == self.trigger:
            if ev.value == 1 and self.mod_down:     # lateral pulsado + click
                self.used = self.swallow_up = True
                self.fire()
                return
            if ev.value == 0 and self.swallow_up:
                self.swallow_up = False
                return
            if ev.value in (0, 1):
                self.trig_down = ev.value == 1

        self.out(ev.type, ev.code, ev.value)


class Proxy:
    def __init__(self, dev, mod, trigger):
        self.dev = dev
        self.ui = evdev.UInput.from_device(dev, name=VNAME)
        dev.grab()
        self.chord = Chord(mod, trigger, self.ui.write, self.ui.syn, launch)

    def pump(self):
        for ev in self.dev.read():
            self.chord.handle(ev)

    def close(self):
        for f in (lambda: self.dev.ungrab(), self.ui.close, self.dev.close):
            try:
                f()
            except OSError:
                pass


# ----------------------------------------------------------------------- modos
def detect():
    devs = []
    for p in evdev.list_devices():
        try:
            d = evdev.InputDevice(p)
        except OSError:
            continue
        if is_mouse(d):
            devs.append(d)
            print(f"Ratón: {d.name}  ({d.path})")
        else:
            d.close()
    if not devs:
        sys.exit("No veo ningún ratón. ¿Estás en el grupo 'input'? (cierra sesión tras instalar)")
    print("\nPulsa botones del ratón (Ctrl+C para salir):")
    fds = {d.fd: d for d in devs}
    try:
        while True:
            r, _, _ = select.select(fds, [], [])
            for fd in r:
                for ev in fds[fd].read():
                    if ev.type == e.EV_KEY and ev.value == 1:
                        print(f"  {name_of(ev.code):<22} ({fds[fd].name})")
    except KeyboardInterrupt:
        pass


def serve(mod, trigger):
    need = [mod] + ([trigger] if trigger is not None else [])
    proxies = {}                                     # fd -> Proxy
    paths = set()
    # dispositivos ya revisados que no son ratón (teclado, cámara...): no se
    # vuelven a abrir. Revisarlos todos cada 2 s tardaba ~300 ms, y como se
    # hace en este mismo bucle, el cursor se quedaba congelado ese rato.
    ignored = set()
    print(f"Combo: mantener {name_of(mod)}"
          + (f" + {name_of(trigger)}" if trigger is not None else ""), flush=True)
    last_scan = 0.0
    while True:
        now = time.monotonic()
        if now - last_scan >= RESCAN_S:
            last_scan = now
            present = set(evdev.list_devices())
            ignored &= present                       # si se desconecta, se olvida
            for p in present - paths - ignored:
                try:
                    d = evdev.InputDevice(p)
                except OSError:
                    continue
                if not is_mouse(d, need):
                    ignored.add(p)
                    d.close()
                    continue
                try:
                    px = Proxy(d, mod, trigger)
                except OSError as ex:
                    print(f"No puedo usar {d.name}: {ex}", file=sys.stderr, flush=True)
                    if ex.errno in (errno.EACCES, errno.EPERM):
                        print("  → falta permiso sobre /dev/uinput o /dev/input "
                              "(grupo 'input' + cerrar sesión)", file=sys.stderr, flush=True)
                    d.close()
                    continue
                proxies[d.fd] = px
                paths.add(p)
                print(f"Usando: {d.name} ({p})", flush=True)

        if not proxies:
            time.sleep(RESCAN_S)
            continue
        r, _, _ = select.select(list(proxies), [], [], RESCAN_S)
        for fd in r:
            px = proxies[fd]
            try:
                px.pump()
            except OSError:                          # ratón desconectado / dormido
                print(f"Desconectado: {px.dev.name}", flush=True)
                paths.discard(px.dev.path)
                px.close()
                del proxies[fd]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--detect", action="store_true", help="mostrar nombres de botones")
    ap.add_argument("--mod", default=os.environ.get("RADIALCMD_MOD", "BTN_EXTRA"),
                    help="botón que se mantiene (por defecto BTN_EXTRA = lateral 'adelante')")
    ap.add_argument("--trigger", default=os.environ.get("RADIALCMD_TRIGGER", "BTN_LEFT"),
                    help="botón que dispara (por defecto BTN_LEFT; 'none' = solo el lateral)")
    a = ap.parse_args()
    if a.detect:
        detect()
    else:
        serve(code_of(a.mod), code_of(a.trigger))


if __name__ == "__main__":
    main()
