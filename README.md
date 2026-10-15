# radial-cmd

Menú radial para tener a mano los comandos que más usas al desarrollar, testear y depurar. Pulsas un atajo y aparece una rueda bajo el ratón. Eliges un comando con un click o una tecla y queda copiado, listo para pegarlo en la terminal.

<p align="center">
  <img src="docs/menu.png" width="32%" alt="Menú principal con categorías e iconos">
  <img src="docs/submenu.png" width="32%" alt="Submenú con los comandos de ROS 2">
  <img src="docs/copiado.png" width="32%" alt="Comando copiado">
</p>

- **Rápido:** un servicio en segundo plano tiene la rueda ya cargada, así que aparece en unos 50 ms.
- **Categorías y subcategorías** en un archivo de texto plano que se relee en cada apertura.
- **Copia a los dos portapapeles:** pega con `Ctrl+V` y también con el click central del ratón.
- **Atajo de teclado** (`Ctrl+Alt+C` por defecto) y, opcionalmente, **combo de ratón**: botón lateral + click izquierdo.
- **Iconos PNG** en las categorías para encontrarlas de un vistazo.
- **Varios monitores:** la rueda sale centrada en el puntero, en el monitor donde esté.

Probado en Ubuntu 24.04 con GNOME sobre X11.

## Instalación

```bash
git clone https://github.com/danicannt/radial-cmd.git
cd radial-cmd
./install.sh
```

`install.sh` hace esto:

- instala las dependencias (`python3-tk`, `python3-pil`, `wl-clipboard`, `xclip`);
- copia los scripts a `~/.local/bin`;
- crea `~/.config/radialcmd/comandos.txt` a partir de [`comandos.example.txt`](comandos.example.txt), sin sobrescribirlo si ya existe;
- activa el servicio `radialcmd.service` de systemd para tu usuario;
- crea el atajo de GNOME `Ctrl+Alt+C`.

Para usar otro atajo, pásalo como argumento:

```bash
./install.sh '<Super>x'
```

### Combo de ratón (opcional)

Para abrir la rueda manteniendo el botón lateral de "adelante" (`BTN_EXTRA`) y haciendo click izquierdo. Vale en cualquier orden:

```bash
./install-mouse.sh
```

Te añade al grupo `input` para poder leer el ratón, así que la primera vez hay que cerrar sesión y volver a entrar. Para usar otro botón, mira su nombre con `radialcmd-mouse --detect` y pásalo como argumento:

```bash
./install-mouse.sh BTN_SIDE
./install-mouse.sh BTN_EXTRA none   # solo el botón lateral, sin click
```

El botón lateral pulsado solo sigue funcionando como siempre: "adelante" en el navegador, etc.

## Uso

| Acción | Ratón | Teclado |
|---|---|---|
| Elegir sector / entrar en categoría | click izquierdo | `1`–`9`, `0` |
| Volver atrás | click en el centro o botón derecho | `Backspace` |
| Cerrar | click fuera de la rueda | `Esc` o volver a pulsar el atajo |

Al elegir un comando, el centro muestra **✓ Copiado** y la rueda se cierra sola. Debajo de la rueda se ve el comando completo del sector que tienes bajo el ratón.

### Línea de comandos

```bash
radialcmd            # abre o cierra la rueda (lo que ejecuta el atajo)
radialcmd -f otro.txt   # abre la rueda con otro archivo, solo esta vez
radialcmd --edit     # abre en el editor el archivo de comandos activo
radialcmd --which    # muestra qué archivos se están leyendo
radialcmd --status   # dice si el servicio está activo
```

## El archivo de comandos

Por defecto es `~/.config/radialcmd/comandos.txt`. Se relee en cada apertura, así que basta con guardar los cambios.

```ini
# comentario
[ROS 2]                                  # categoría: un anillo nuevo
Source Jazzy => source /opt/ros/jazzy/setup.bash
Topics => ros2 topic list

[Cámaras/Red]                            # subcategoría
MTU 9000 => sudo ip link set dev INTERFAZ mtu 9000

[]                                       # vuelve a la raíz
docker ps                                # sin "=>": la etiqueta es el propio comando
```

- **`Etiqueta => comando`:** el comando puede llevar `|`, `&&`, comillas, etc.
- **Tamaño de los anillos:** queda mejor con 4–8 entradas por anillo; si crece, usa subcategorías.
- **Categorías repetidas:** se juntan, también entre archivos distintos.

### Varios archivos (comandos generales + proyecto)

Si en `~/.bashrc` defines `RADIALCMD_FILE` con varias rutas separadas por `:`, se combinan:

```bash
export RADIALCMD_FILE=~/.config/radialcmd/comandos.txt:~/mi_proyecto/comandos.txt
```

El servicio lee esa línea de `~/.bashrc` en cada apertura, sin reiniciar nada. Orden de prioridad: `-f` > variable de entorno > `~/.bashrc` > archivo por defecto.

### Iconos

Pon un PNG en `~/.config/radialcmd/icons/` con el nombre de una palabra. Aparecerá en todas las entradas cuya etiqueta contenga esa palabra:

```
~/.config/radialcmd/icons/ros.png      →  "ROS 2"
~/.config/radialcmd/icons/docker.png   →  "Docker"
```

Se reducen solos a 26 px de alto como máximo; mejor si tienen fondo transparente. Los iconos no se incluyen en el repositorio porque los logos de ROS y Docker son marcas registradas.

## Cómo funciona

```mermaid
flowchart TB
    kb["Atajo de teclado<br/><i>Ctrl+Alt+C</i>"] --> cli["<b>radialcmd</b><br/>trigger() → _send()"]
    mouse["Botón lateral + click<br/><i>evdev</i>"] --> rm["<b>radialcmd-mouse</b><br/>Chord.handle() → launch()"]
    cli -- TOGGLE --> sock(["socket Unix<br/>$XDG_RUNTIME_DIR/radialcmd.sock"])
    rm -- TOGGLE --> sock

    subgraph daemon["radialcmd-daemon · servicio systemd, siempre cargado"]
        accept["_on_accept()<br/>_handle_message()"] --> open["open() / hide()"]
        open --> draw["draw() + Shaper<br/>rueda recortada en círculo"]
        input["on_click / on_key / on_motion"] --> select["select(i)<br/>entra o copia"]
        select --> draw
        open -. hilo aparte .-> mon["_refresh_monitors_async()"]
    end
    sock --> accept

    subgraph core["radialcmd_core.py"]
        load["load_tree()<br/>resolve_files + parse"]
        copy["copy_to_clipboard()"]
        monitors["monitors()<br/>XRRGetMonitors"]
    end
    open --> load
    select --> copy
    mon --> monitors

    files[("comandos.txt<br/>~/.bashrc")] --> load
    copy --> clip[["CLIPBOARD (Ctrl+V)<br/>PRIMARY (click central)"]]
    monitors --> x11[["Servidor X"]]
```

1. **Disparo.** El atajo ejecuta `radialcmd`, que manda `TOGGLE` por el socket y termina sin cargar nada más (~20 ms). El combo de ratón lo detecta `radialcmd-mouse`, que reenvía todos los demás eventos a un ratón virtual (`uinput`) para que el ratón funcione como siempre.
2. **Servicio.** `radialcmd-daemon` tiene Tk y la ventana ya creados. Duerme sin gastar CPU hasta que el socket recibe algo.
3. **Apertura.** `open()` relee los archivos de comandos, coloca la rueda bajo el puntero y la dibuja antes de mostrarla. La ventana se recorta en círculo con la extensión Shape de X, sin capa a pantalla completa ni transparencias, para que el compositor la pinte al instante.
4. **Elección.** `select()` entra en la categoría o copia el comando con `xclip` / `wl-copy` a los dos portapapeles. Después, la rueda se cierra.

Si el servicio no está activo, `radialcmd` lo arranca él mismo la primera vez.

## Depuración

```bash
systemctl --user status radialcmd          # servicio de la rueda
systemctl --user status radialcmd-mouse    # servicio del combo de ratón
journalctl --user -u radialcmd-mouse -f    # qué ratones usa, desconexiones…
```

Para ver cuánto tarda cada paso de la apertura, para el servicio y lánzalo a mano con trazas de tiempo:

```bash
systemctl --user stop radialcmd
RADIALCMD_DEBUG_TIMING=1 ~/.local/bin/radialcmd-daemon
```

## Licencia

[MIT](LICENSE)
