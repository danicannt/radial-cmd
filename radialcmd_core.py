#!/usr/bin/env python3
"""
radialcmd_core — lo que usan tanto el cliente (radialcmd) como el servicio en
segundo plano (radialcmd-daemon). No importa tkinter, para que el cliente
arranque lo más rápido posible.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

DEFAULT = Path.home() / ".config/radialcmd/comandos.txt"
BASHRC = Path.home() / ".bashrc"
VAR = "RADIALCMD_FILE"
SEP = "=>"
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
SOCK = RUNTIME / "radialcmd.sock"
CWD_FILE = RUNTIME / "radialcmd.cwd"      # lo escribe el hook de PROMPT_COMMAND
PROJECT_FILE = ".radialcmd"               # archivo por proyecto (estilo .editorconfig)
STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "radialcmd"
RECENT = STATE / "recientes.json"
RECENT_MAX = 8

# Hueco a rellenar: {palabra} en minúsculas. No casan ${VAR}, {{.Names}} de
# docker ni {a,b} de bash, que son comandos normales.
PH_RX = re.compile(r"(?<![{$])\{([a-z][a-z0-9_]*)\}(?!\})")

# Generadores de opciones para los huecos (se pueden redefinir con @nombre = ...)
GENERATORS = {
    "topic": "ros2 topic list",
    "node": "ros2 node list",
    "service": "ros2 service list",
    "iface": "ip -br link | awk '$1 != \"lo\" {print $1}' | cut -d@ -f1",
    "container": "docker ps --format '{{.Names}}'",
}
WS_GENERATORS = ("pkg", "launch")          # salen del escaneo del workspace

# El hook que hay que poner en ~/.bashrc (lo añade install.sh)
HOOK_MARK = "# radialcmd: directorio de la terminal activa"
HOOK = (HOOK_MARK + "\n"
        "PROMPT_COMMAND='printf \"%s\" \"$PWD\" > \"${XDG_RUNTIME_DIR:-/tmp}/radialcmd.cwd\"'"
        "\"${PROMPT_COMMAND:+; $PROMPT_COMMAND}\"\n")


class Cmd(str):
    """El comando de una entrada: un texto con alguna marca más."""
    danger = False                         # "! Etiqueta => ..." pide un segundo click

    def holes(self):
        return list(dict.fromkeys(PH_RX.findall(self)))

    def fill(self, name, value):
        return self.derive(PH_RX.sub(lambda m: value if m.group(1) == name else m.group(0), self))

    def derive(self, text):
        c = Cmd(text)
        c.danger = self.danger
        return c


# ---------------------------------------------------------------- archivo
def _section(root, path):
    node = root
    for part in (p.strip() for p in path.split("/")):
        if not part:
            continue
        for label, val in node:
            if label == part and isinstance(val, list):
                node = val
                break
        else:
            new = []
            node.append((part, new))
            node = new
    return node


def parse(path, root=None, gens=None):
    """Añade a `root` las entradas de `path` y a `gens` sus  @nombre = comando."""
    root = [] if root is None else root
    node = root
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            node = _section(root, line[1:-1])
            continue
        m = re.match(r"@([a-z][a-z0-9_]*)\s*=\s*(.+)$", line)
        if m:
            if gens is not None:
                gens[m.group(1)] = m.group(2).strip()
            continue
        danger = line.startswith("!")
        if danger:
            line = line[1:].strip()
        if SEP in line:
            label, cmd = (s.strip() for s in line.split(SEP, 1))
        else:
            label = cmd = line
        if cmd:
            c = Cmd(cmd)
            c.danger = danger
            node.append((label or cmd, c))
    return root


def _from_bashrc():
    """Valor de la última línea  [export] RADIALCMD_FILE=...  no comentada."""
    try:
        lines = BASHRC.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    val = None
    rx = re.compile(r"^\s*(?:export\s+)?" + VAR + r"=(.*)$")
    for line in lines:
        m = rx.match(line)
        if not m:
            continue
        raw = m.group(1).strip()
        if raw[:1] in "\"'":
            q = raw[0]
            end = raw.find(q, 1)
            raw = raw[1:end] if end > 0 else raw[1:]
        else:
            raw = raw.split("#", 1)[0].split()[0] if raw.split("#", 1)[0].split() else ""
        val = raw
    return val


def _expand(spec):
    out = []
    for part in spec.split(":"):
        part = part.strip()
        if part:
            out.append(Path(os.path.expanduser(os.path.expandvars(part))))
    return out


def resolve_files(cli=None, env_override=None):
    """Devuelve (lista de archivos, de dónde salió).
    env_override simula la variable de entorno (la usa el daemon, que no
    hereda el entorno de la terminal desde la que escribiste el .bashrc)."""
    if cli:
        return [cli], "-f"
    spec = env_override if env_override is not None else os.environ.get(VAR)
    if spec:
        return _expand(spec), "entorno"
    b = _from_bashrc()
    if b:
        return _expand(b), "~/.bashrc"
    return [DEFAULT], "por defecto"


def title_for(files):
    f = files[-1]
    # "comandos.txt" no dice nada: usa el nombre de la carpeta (el workspace)
    name = f.parent.name if f.stem.lower() in ("comandos", "commands") else f.stem
    if f.parent == DEFAULT.parent:
        name = "Comandos"
    return name


# ------------------------------------------------------------- proyecto
def install_hook(bashrc=BASHRC):
    """Añade HOOK a ~/.bashrc si no está. Devuelve True si lo ha añadido."""
    try:
        text = bashrc.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        text = ""
    if HOOK_MARK in text:
        return False
    with bashrc.open("a", encoding="utf-8") as f:
        f.write(("\n" if text and not text.endswith("\n") else "") + "\n" + HOOK)
    return True


def active_cwd():
    """Directorio de la última terminal en la que pulsaste Enter (hook)."""
    try:
        p = Path(CWD_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return p if p.is_absolute() and p.is_dir() else None


def _upwards(start):
    p = start
    while True:
        yield p
        if p.parent == p:
            return
        p = p.parent


def find_project_file(cwd):
    for d in _upwards(cwd):
        f = d / PROJECT_FILE
        if f.is_file():
            return f
    return None


def find_colcon_ws(cwd):
    for d in _upwards(cwd):
        if (d / "src").is_dir() and (d / "install").is_dir():
            return d
    return None


LAUNCH_SUFFIXES = (".launch.py", "_launch.py", ".launch.xml", "_launch.xml",
                   ".launch.yaml", "_launch.yaml")


def scan_ws(ws):
    """(paquetes, launch files) de un workspace colcon: [(pkg)], [(pkg, archivo)]."""
    pkgs = []
    for root, dirs, files in os.walk(ws / "src"):
        if "COLCON_IGNORE" in files or "AMENT_IGNORE" in files:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if "package.xml" in files:
            try:
                xml = (Path(root) / "package.xml").read_text(encoding="utf-8", errors="replace")
                m = re.search(r"<name>\s*([^<\s]+)\s*</name>", xml)
            except OSError:
                m = None
            pkgs.append((m.group(1) if m else Path(root).name, Path(root)))
            dirs[:] = []                   # no hay paquetes dentro de paquetes
    launches = []
    for name, d in pkgs:
        for root, dirs, files in os.walk(d):
            dirs[:] = [x for x in dirs if not x.startswith(".") and x not in ("test", "tests")]
            launches += [(name, f) for f in files if f.endswith(LAUNCH_SUFFIXES)]
    return (sorted({n for n, _ in pkgs}),
            sorted(set(launches), key=lambda t: (t[0], t[1])))


def ws_category(ws, scan):
    """Categoría automática con lo útil de un workspace colcon."""
    pkgs, launches = scan
    q = shlex.quote(str(ws))
    build = f"cd {q} && colcon build --symlink-install"
    items = [("Source workspace", Cmd(f"source {shlex.quote(str(ws / 'install/setup.bash'))}")),
             ("Build todo", Cmd(build))]
    if pkgs:
        items.append(("Paquetes", [(p, Cmd(f"{build} --packages-select {p}")) for p in pkgs]))
    if launches:
        items.append(("Launch", [(_launch_label(f), Cmd(f"ros2 launch {p} {f}"))
                                 for p, f in launches]))
    return (f"ROS {ws.name}", items)


def _launch_label(f):
    for suf in LAUNCH_SUFFIXES:
        if f.endswith(suf):
            return f[:-len(suf)]
    return f


def load_tree(cli=None, cwd=None, env_override=None):
    """Lee (de cero, siempre) los archivos activos. Devuelve un objeto con:
    tree, title, note, gens (generadores @), project (.radialcmd) y ws."""
    files, _ = resolve_files(cli, env_override)
    project = ws = None
    if cwd and not cli:
        project = find_project_file(cwd)
        ws = find_colcon_ws(cwd)
        if project:
            files = files + [project]
    tree, problems, gens = [], [], dict(GENERATORS)
    for f in files:
        try:
            parse(f, tree, gens)
        except FileNotFoundError:
            problems.append(f"No existe {f}")
        except Exception as ex:  # error de lectura/codificación
            problems.append(f"Error leyendo {f}: {ex}")
    note = "  ·  ".join(problems)
    if not tree and not note:
        note = f"Sin comandos en: {', '.join(map(str, files))}"
    title = (project.parent.name if project else ws.name if ws else
             title_for([f for f in files if f != project] or files))
    return SimpleNamespace(tree=tree, title=title, note=note, gens=gens,
                           project=project, ws=ws)


def run_generator(cmd, timeout=5.0):
    """Opciones para un hueco: una por línea de la salida de `cmd`."""
    if re.search(r"\bros2\b", cmd) and not shutil.which("ros2"):
        # el servicio no tiene ROS en su entorno: se usa la última distro instalada
        setup = sorted(Path("/opt/ros").glob("*/setup.bash"))
        if setup:
            cmd = f"source {shlex.quote(str(setup[-1]))} >/dev/null 2>&1; {cmd}"
    try:
        out = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True,
                             timeout=timeout, stdin=subprocess.DEVNULL).stdout
    except (subprocess.SubprocessError, OSError):
        return []                          # sin respuesta: la rueda dice "sin opciones"
    return list(dict.fromkeys(l.strip() for l in out.splitlines() if l.strip()))


# -------------------------------------------------------------- recientes
def load_recent():
    try:
        data = json.loads(RECENT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for e in data if isinstance(data, list) else []:
        if isinstance(e, dict) and e.get("cmd"):
            c = Cmd(e["cmd"])
            c.danger = bool(e.get("danger"))
            out.append((e.get("label") or c, c))
    return out


def add_recent(label, cmd):
    items = [(l, c) for l, c in load_recent() if c != cmd]
    items.insert(0, (label, cmd))
    data = [{"label": l, "cmd": str(c), "danger": c.danger} for l, c in items[:RECENT_MAX]]
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        tmp = RECENT.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(RECENT)
    except OSError:
        pass


# ------------------------------------------------------------ portapapeles
def _copy(text, cands):
    for cmd in cands:
        if not shutil.which(cmd[0]):
            continue
        try:
            subprocess.run(cmd, input=text.encode(), check=True, timeout=3,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except (subprocess.SubprocessError, OSError):
            continue
    return False


def copy_to_clipboard(text):
    """Copia al portapapeles (Ctrl+V) y a la selección primaria (click
    central / rueda del ratón), para que pegue lo mismo de las dos formas."""
    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    clip = ([["wl-copy"]] if wayland else []) + [
        ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
    primary = ([["wl-copy", "--primary"]] if wayland else []) + [
        ["xclip", "-selection", "primary"], ["xsel", "--primary", "--input"]]
    ok = _copy(text, clip)
    _copy(text, primary)
    return ok


TERMINALS = ("terminal", "terminator", "tilix", "kitty", "alacritty", "konsole",
             "xterm", "urxvt", "wezterm", "guake", "code")


def paste_to_active():
    """Pega en la ventana que tenía el foco antes de abrir la rueda (la rueda
    no pasa por el gestor de ventanas, así que _NET_ACTIVE_WINDOW sigue
    apuntando a ella). Terminales: Ctrl+Shift+V; el resto: Ctrl+V."""
    if not shutil.which("xdotool"):
        return False
    try:
        cls = subprocess.run(["xdotool", "getactivewindow", "getwindowclassname"],
                             capture_output=True, text=True, timeout=2).stdout.lower()
        keys = "ctrl+shift+v" if any(t in cls for t in TERMINALS) else "ctrl+v"
        subprocess.run(["xdotool", "getactivewindow", "windowfocus", "--sync",
                        "key", "--clearmodifiers", keys], timeout=3,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (subprocess.SubprocessError, OSError):
        return False


# ------------------------------------------------------------------ monitores
_XLIBS = None


def _monitors_xrandr_lib():
    """Pregunta a XRandR directamente (RRGetMonitors). A diferencia del comando
    `xrandr`, no sondea las salidas de vídeo: tarda ~1 ms en vez de ~800 ms y
    no congela el servidor X mientras tanto."""
    import ctypes
    import ctypes.util

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("name", ctypes.c_ulong), ("primary", ctypes.c_int),
                    ("automatic", ctypes.c_int), ("noutput", ctypes.c_int),
                    ("x", ctypes.c_int), ("y", ctypes.c_int),
                    ("width", ctypes.c_int), ("height", ctypes.c_int),
                    ("mwidth", ctypes.c_int), ("mheight", ctypes.c_int),
                    ("outputs", ctypes.c_void_p)]

    global _XLIBS
    if _XLIBS is None:      # find_library lanza `ldconfig -p`: solo una vez
        _XLIBS = (ctypes.CDLL(ctypes.util.find_library("X11")),
                  ctypes.CDLL(ctypes.util.find_library("Xrandr")))
    x11, xrr = _XLIBS
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    xrr.XRRGetMonitors.restype = ctypes.POINTER(MonitorInfo)
    xrr.XRRGetMonitors.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                   ctypes.POINTER(ctypes.c_int)]
    xrr.XRRFreeMonitors.argtypes = [ctypes.POINTER(MonitorInfo)]

    dpy = x11.XOpenDisplay(None)
    if not dpy:
        return []
    try:
        n = ctypes.c_int()
        info = xrr.XRRGetMonitors(dpy, x11.XDefaultRootWindow(dpy), 1, ctypes.byref(n))
        if not info:
            return []
        mons = [(info[i].x, info[i].y, info[i].width, info[i].height) for i in range(n.value)]
        xrr.XRRFreeMonitors(info)
        return mons
    finally:
        x11.XCloseDisplay(dpy)


def monitors():
    """Rectángulos (x, y, w, h) de cada monitor, en coordenadas X."""
    try:
        mons = _monitors_xrandr_lib()
    except (OSError, AttributeError, TypeError):
        mons = []
    # quita duplicados y rectángulos que engloban a otros (p. ej. "pantalla virtual")
    mons = list(dict.fromkeys(mons))

    def contains(a, b):
        return (a != b and a[0] <= b[0] and a[1] <= b[1]
                and a[0] + a[2] >= b[0] + b[2] and a[1] + a[3] >= b[1] + b[3])
    mons = [a for a in mons if not any(contains(a, b) for b in mons)]
    return mons
