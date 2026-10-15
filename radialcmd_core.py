#!/usr/bin/env python3
"""
radialcmd_core — lo que usan tanto el cliente (radialcmd) como el servicio en
segundo plano (radialcmd-daemon). No importa tkinter, para que el cliente
arranque lo más rápido posible.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

DEFAULT = Path.home() / ".config/radialcmd/comandos.txt"
BASHRC = Path.home() / ".bashrc"
VAR = "RADIALCMD_FILE"
SEP = "=>"
SOCK = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "radialcmd.sock"


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


def parse(path, root=None):
    root = [] if root is None else root
    node = root
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            node = _section(root, line[1:-1])
            continue
        if SEP in line:
            label, cmd = (s.strip() for s in line.split(SEP, 1))
        else:
            label = cmd = line
        if cmd:
            node.append((label or cmd, cmd))
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


def load_tree(cli=None, env_override=None):
    """Lee (de cero, siempre) los archivos activos y devuelve (árbol, título, aviso)."""
    files, _ = resolve_files(cli, env_override)
    tree, problems = [], []
    for f in files:
        try:
            parse(f, tree)
        except FileNotFoundError:
            problems.append(f"No existe {f}")
        except Exception as ex:  # error de lectura/codificación
            problems.append(f"Error leyendo {f}: {ex}")
    note = "  ·  ".join(problems)
    if not tree and not note:
        note = f"Sin comandos en: {', '.join(map(str, files))}"
    return tree, title_for(files), note


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
