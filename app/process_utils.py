from __future__ import annotations

import csv
import subprocess
from io import StringIO


LDPLAYER_PROCESS_MARKERS = (
    "ldplayer",
    "dnplayer",
    "ldbox",
    "dnconsole",
    "ldconsole",
    "vboxheadless",
)


def ldplayer_related_pids() -> set[int]:
    return _process_pids_matching(LDPLAYER_PROCESS_MARKERS)


def _process_pids_matching(markers: tuple[str, ...]) -> set[int]:
    try:
        result = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if result.returncode != 0:
        return set()

    pids: set[int] = set()
    for row in csv.reader(StringIO(result.stdout)):
        if len(row) < 2:
            continue
        image_name = row[0].lower()
        if not any(marker in image_name for marker in markers):
            continue
        try:
            pids.add(int(row[1]))
        except ValueError:
            continue
    return pids
