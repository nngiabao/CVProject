# GrowStone Bot

Python desktop application for managing LDPlayer instances and bot tasks.
Network assignment is now per-emulator WireGuard config based: each LDPlayer
instance can be assigned its own `.conf` file, then the app can install/open the
official WireGuard Android app and push that config into the emulator.

## Run

Use 64-bit Python 3.9.1 for the Windows 10 compatibility dependency set. The
requirements are pinned to PySide6 6.2.4 and older NumPy/OpenCV wheels so the
app does not pull the newest Qt, NumPy, or OpenCV builds.

```powershell
python -m pip install -r requirements.txt
python main.py
```

The application searches common LDPlayer installation folders. Set
`LDPLAYER_CONSOLE` to the full path of `dnconsole.exe` or `ldconsole.exe` when
LDPlayer is installed elsewhere.

## WireGuard workflow

1. Start the LDPlayer instance and enable local ADB connection in LDPlayer.
2. Select one or more emulator rows.
3. Use **Assign .conf** and choose a WireGuard config file.
4. Use **Install / import**. If WireGuard is missing, the app installs the APK
   from the workspace `work` folder, copies the config to Android Downloads, and
   opens the import flow when Android allows it.
5. Turn the tunnel on inside WireGuard, then use **Check VPN IP** before bot
   tasks run.

Assignments are saved in `.wireguard_assignments.json` by LDPlayer instance
index and loaded automatically on startup. The most recently chosen config path
is remembered in `.wireguard_source.txt`.

## Bot model

The UI is backed by a small object model in `app/bot.py`. `BotManager` owns one
`BotPerson` per emulator instance. Each person keeps its assigned WireGuard
config, latest IP check result, and independent task list. Bot tasks stay
disabled until the selected instance has a WireGuard config assigned.

## Stone merge feature

The first bot feature is template-based stone detection. Put cropped stone
template images in `assets/templates/stones/`. The scanner captures the emulator
screen, searches only the bottom third of the 720x1080 screen, and returns drag
coordinates when it finds two matching stones of the same template. If the
template folder is empty, the task reports that directly instead of silently
showing no match.

When the **Merge stones** task is enabled, the app checks the emulator public IP,
captures the selected instance, and keeps merging visible matching pairs until
no pair is found or the per-run safety cap is reached.
