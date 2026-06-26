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
