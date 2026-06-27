# Emulator Proxy Manager

Python desktop application for managing LDPlayer instances and assigning proxy
configurations. Phase 1 includes emulator discovery and lifecycle controls plus
proxy import, assignment, and in-app local proxy routing. Transparent
WinDivert interception and kill-switch routing will be added behind the same UI
workflow in a later phase.

## Run

Use 64-bit Python 3.9.1 for the Windows 10 compatibility dependency set. The
requirements are pinned to PySide6 6.2.4 and older NumPy/OpenCV wheels so the
app does not pull the newest Qt, NumPy, or OpenCV builds.

```powershell
python -m pip install -r requirements.txt
python main.py
```

On Windows, the application automatically requests Administrator access through
UAC. LDPlayer control and the future WinDivert routing layer require elevation.

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

## Routing workflow

Use **Start proxy routing** after assigning proxies to selected instances. The
app starts one local HTTP proxy bridge per selected instance, using the Windows
host IP with `19000 + instance index` as the local endpoint. The local endpoint
does not require authentication; the app authenticates to the assigned Webshare
SOCKS5 proxy upstream.

For LDPlayer, the app also attempts to set Android's global HTTP proxy through
LDPlayer's ADB command so browser traffic inside the emulator can use the local
bridge. When routing is stopped, the app clears that Android proxy setting.
If LDPlayer has just been launched, wait until Android is fully booted before
starting routing; the app will retry ADB for a short period, but proxy setup
cannot complete while LDPlayer reports `device not found`.

The local bridge is the in-app proxy handler that will sit behind the future
WinDivert transparent redirect and kill switch. Until that redirect layer is
added, emulator traffic must be configured to use the displayed local routing
endpoint.

WinDivert support uses `pydivert` and requires the app to run as Administrator.
If the app shows **Protection: Bridge only**, transparent redirection is not
active yet and LDPlayer traffic is not being forced through the proxy.

When **Start proxy routing** is used on running LDPlayer instances and WinDivert
is available, the app enables a kill switch for those instance PIDs. Direct
public TCP/UDP traffic from protected LDPlayer processes is blocked so the
instance cannot fall back to the real connection while routing is active.

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
