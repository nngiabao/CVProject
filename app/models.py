from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class InstanceState(str, Enum):
    RUNNING = "Running"
    STARTING = "Starting"
    STOPPED = "Stopped"
    UNKNOWN = "Unknown"


@dataclass(slots=True)
class EmulatorInstance:
    index: int
    name: str
    state: InstanceState
    pid: int | None = None
    platform: str = "LDPlayer"
    proxy: str | None = None


@dataclass(frozen=True, slots=True)
class ProxyConfig:
    scheme: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None

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
