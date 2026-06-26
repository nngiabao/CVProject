# Emulator Proxy Manager

Python desktop application for managing LDPlayer instances and assigning proxy
configurations. Phase 1 includes emulator discovery and lifecycle controls plus
proxy import and assignment. Network routing will be provided by a separate
elevated WinDivert service in a later phase.

## Run

```powershell
python -m pip install -r requirements.txt
python main.py
```

On Windows, the application automatically requests Administrator access through
UAC. LDPlayer control and the future WinDivert routing service require elevation.

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
**Assign proxy to selected**. The instance table shows the assigned proxy, the
resolved proxy IP, and whether the proxy host and port are reachable.
