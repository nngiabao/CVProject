from __future__ import annotations

from app.emulators.base import EmulatorProvider
from app.models import EmulatorInstance, InstanceState


class MockEmulatorProvider(EmulatorProvider):
    def __init__(self) -> None:
        self._instances = [
            EmulatorInstance(0, "LDPlayer-01", InstanceState.RUNNING, 14320),
            EmulatorInstance(1, "LDPlayer-02", InstanceState.STOPPED),
            EmulatorInstance(2, "LDPlayer-03", InstanceState.RUNNING, 17384),
            EmulatorInstance(3, "LDPlayer-04", InstanceState.STOPPED),
        ]

    @property
    def display_name(self) -> str:
        return "Demo mode — LDPlayer not detected"

    @property
    def is_demo(self) -> bool:
        return True

    def list_instances(self) -> list[EmulatorInstance]:
        return [
            EmulatorInstance(
                item.index,
                item.name,
                item.state,
                item.pid,
                item.platform,
                item.proxy,
            )
            for item in self._instances
        ]

    def start(self, index: int) -> None:
        instance = self._find(index)
        instance.state = InstanceState.RUNNING
        instance.pid = 14000 + index

    def stop(self, index: int) -> None:
        instance = self._find(index)
        instance.state = InstanceState.STOPPED
        instance.pid = None

    def restart(self, index: int) -> None:
        self.stop(index)
        self.start(index)

    def set_http_proxy(self, index: int, host: str, port: int) -> str:
        proxy = f"{host}:{port}"
        self._find(index).proxy = proxy
        return proxy

    def clear_http_proxy(self, index: int) -> None:
        self._find(index).proxy = None

    def get_http_proxy(self, index: int) -> str:
        return self._find(index).proxy or ""

    def _find(self, index: int) -> EmulatorInstance:
        return next(item for item in self._instances if item.index == index)
