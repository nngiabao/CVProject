# Emulator Proxy Manager

Python desktop application for managing LDPlayer instances and assigning proxy
configurations. It includes emulator discovery and lifecycle controls, proxy
import/assignment, and a Wintun/tun2socks tunnel through assigned SOCKS5
proxies with a WinDivert leak guard.

## Run

Use 64-bit Python 3.9.1 for the Windows 10 compatibility dependency set. The
requirements are pinned to PySide6 6.2.4 and older NumPy/OpenCV wheels so the
app does not pull the newest Qt, NumPy, or OpenCV builds.

```powershell
python -m pip install -r requirements.txt
python main.py
```

On Windows, the application automatically requests Administrator access through
UAC. LDPlayer control, Wintun routing, and WinDivert protection require
elevation.

The application searches common LDPlayer installation folders. Set
`LDPLAYER_CONSOLE` to the full path of `dnconsole.exe` or `ldconsole.exe` when
LDPlayer is installed elsewhere.

Instance discovery uses LDPlayer's `list2` command. The reported PID is verified
against Windows every three seconds so the dashboard can distinguish running,
starting, stopped, and stale instances.

If LDPlayer is not found, the application opens in demo mode with sample
instances so the interface can still be developed and reviewed.

## Proxy workflow

Proxy assignment is SOCKS5-only. Use **Load SOCKS5 proxies** to import a text
file with one proxy per line, select one or more emulator rows, then use
**Assign proxy to selected**. The instance table shows assignment state, the
resolved proxy IP, and whether the authenticated proxy check passed.

The app remembers the selected proxy text file in `.proxy_source.txt`. If that
file is missing, it looks for a Webshare text file in Downloads before opening
the file picker.

Proxy assignments are saved by LDPlayer instance index in `.proxy_assignments.json`
and loaded automatically on startup. Bot tasks stay disabled until the selected
instance has an assigned SOCKS5 proxy.

## Routing workflow

Use **Start proxy routing** after assigning proxies to selected instances. The
app starts `tools/tun2socks/tun2socks.exe` with `wintun.dll`, creates the
`GrowStoneTun` Wintun adapter, and sends IPv4 traffic through the assigned
SOCKS5 proxy.

The app clears Android's global HTTP proxy setting before routing starts. LDPlayer
does not need manual Wi-Fi proxy configuration; the tunnel happens from Windows.

The tunnel is system-level, so only one SOCKS5 proxy can own it at a time. If you
need to change to another proxy, stop proxy routing first and then start it again
with the new assignment.

When **Start proxy routing** is used on running LDPlayer instances, the app also
enables a Python-side WinDivert leak guard for those instance PIDs. It blocks
unhandled UDP traffic, and if the tunnel is not running it also blocks direct
public TCP so the emulator should lose internet instead of exposing the real IP.

## Bot model

The UI is backed by a small object model in `app/bot.py`. `BotManager` owns one
`BotPerson` per emulator instance. Each person keeps its assigned proxy, proxy
check result, routing session, and independent task list. The right-side task
panel displays and edits the selected person's tasks.

## Stone merge feature

The first bot feature is template-based stone detection. Put cropped stone
template images in `assets/templates/stones/`. The scanner captures the emulator
screen, searches only the bottom third of the 720x1080 screen, and returns drag
coordinates when it finds two matching stones of the same template.

This first version detects the merge candidate; the next step is executing the
drag through ADB input once the real stone template image is available.
