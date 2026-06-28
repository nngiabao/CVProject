from __future__ import annotations

from typing import Optional
from dataclasses import dataclass
from enum import Enum


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
    proxy: Optional[str] = None


@dataclass(frozen=True)
class ProxyConfig:
    scheme: str
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None

    @property
    def display(self) -> str:
        credentials = f"{self.username}:••••@" if self.username else ""
        return f"{self.scheme}://{credentials}{self.host}:{self.port}"

    @property
    def connection_url(self) -> str:
        credentials = ""
        if self.username:
            credentials = self.username
            if self.password is not None:
                credentials += f":{self.password}"
            credentials += "@"
        return f"{self.scheme}://{credentials}{self.host}:{self.port}"
