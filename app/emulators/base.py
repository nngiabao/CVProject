from __future__ import annotations

from abc import ABC, abstractmethod

from app.models import EmulatorInstance


class EmulatorProvider(ABC):
    @property
    @abstractmethod
    def display_name(self) -> str:
        raise NotImplementedError

    @property
    def is_demo(self) -> bool:
        return False

    @abstractmethod
    def list_instances(self) -> list[EmulatorInstance]:
        raise NotImplementedError

    @abstractmethod
    def start(self, index: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self, index: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def restart(self, index: int) -> None:
        raise NotImplementedError

    def screenshot_png(self, index: int) -> bytes:
        raise NotImplementedError("Screenshot capture is not supported by this emulator provider")

    def drag(self, index: int, start: tuple[int, int], end: tuple[int, int], duration_ms: int = 350) -> None:
        raise NotImplementedError("Touch drag is not supported by this emulator provider")
