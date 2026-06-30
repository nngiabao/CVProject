from __future__ import annotations

from typing import Optional
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class InstanceState(str, Enum):
    RUNNING = "Running"
    STARTING = "Starting"
    STOPPED = "Stopped"
    UNKNOWN = "Unknown"


@dataclass
class EmulatorInstance:
    index: int
    name: str
    state: InstanceState
    pid: Optional[int] = None
    platform: str = "LDPlayer"
    identity: Optional[str] = None
    network: Optional[str] = None
    pids: Optional[set[int]] = None

    def live_pids(self) -> set[int]:
        pids = set(self.pids or set())
        if self.pid is not None:
            pids.add(self.pid)
        return {pid for pid in pids if pid > 0}


@dataclass(frozen=True)
class WireGuardConfig:
    path: str

    @property
    def file_path(self) -> str:
        return self.path

    @property
    def name(self) -> str:
        return Path(self.path).stem

    @property
    def display(self) -> str:
        return f"{self.name}.conf"

    @property
    def connection_url(self) -> str:
        return self.path
