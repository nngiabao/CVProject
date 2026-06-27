from __future__ import annotations

from typing import Optional
from dataclasses import dataclass

from app.models import EmulatorInstance, ProxyConfig
from app.routing import RoutingService, RoutingSession


@dataclass
class BotTask:
    name: str
    enabled: bool = False
    status: str = "Off"

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self.status = "Idle" if enabled else "Off"


@dataclass
class BotPerson:
    instance_index: int
    instance: Optional[EmulatorInstance] = None
    proxy: Optional[ProxyConfig] = None
    proxy_check: Optional[tuple[str, str]] = None
    tasks: Optional[list[BotTask]] = None

    def __post_init__(self) -> None:
        if self.tasks is None:
            self.tasks = default_tasks()

    @property
    def assigned(self) -> bool:
        return self.proxy is not None

    def assign_proxy(self, proxy: ProxyConfig, proxy_check: Optional[tuple[str, str]] = None) -> None:
        self.proxy = proxy
        self.proxy_check = proxy_check

    def clear_proxy(self) -> None:
        self.proxy = None
        self.proxy_check = None

    def set_task_enabled(self, row: int, enabled: bool) -> None:
        if self.tasks is None or row >= len(self.tasks):
            return
        self.tasks[row].set_enabled(enabled)

    def task_counts(self) -> tuple[int, int, int]:
        tasks = self.tasks or []
        enabled = sum(task.enabled for task in tasks)
        idle = sum(task.status == "Idle" for task in tasks)
        errors = sum(task.status == "Error" for task in tasks)
        return enabled, idle, errors


class BotManager:
    def __init__(self, routing: RoutingService) -> None:
        self.routing = routing
        self.people: dict[int, BotPerson] = {}

    def sync_instances(self, instances: list[EmulatorInstance]) -> None:
        seen = set()
        for instance in instances:
            seen.add(instance.index)
            person = self.person(instance.index)
            person.instance = instance
        for index in set(self.people) - seen:
            self.people[index].instance = None

    def person(self, instance_index: int) -> BotPerson:
        if instance_index not in self.people:
            self.people[instance_index] = BotPerson(instance_index)
        return self.people[instance_index]

    def assigned_count(self) -> int:
        return sum(person.assigned for person in self.people.values())

    def assign_proxy(
        self,
        instance_index: int,
        proxy: ProxyConfig,
        proxy_check: Optional[tuple[str, str]] = None,
    ) -> None:
        self.stop_routing(instance_index)
        self.person(instance_index).assign_proxy(proxy, proxy_check)

    def clear_all_proxies(self) -> None:
        for person in self.people.values():
            person.clear_proxy()
        self.routing.stop_all()

    def start_routing(self, instance_index: int) -> RoutingSession:
        proxy = self.person(instance_index).proxy
        if proxy is None:
            raise RuntimeError("assign a proxy first")
        return self.routing.start(instance_index, proxy)

    def stop_routing(self, instance_index: int) -> None:
        self.routing.stop(instance_index)

    def routed_indexes(self) -> set[int]:
        return set(self.routing.sessions())

    def routed_pids(self) -> set[int]:
        pids: set[int] = set()
        for index in self.routed_indexes():
            instance = self.person(index).instance
            if instance is not None and instance.pid is not None:
                pids.add(instance.pid)
        return pids

    def session(self, instance_index: int) -> Optional[RoutingSession]:
        return self.routing.session(instance_index)


def default_tasks() -> list[BotTask]:
    return [
        BotTask("Warmup", True, "Idle"),
        BotTask("Merge stones"),
        BotTask("Daily cycle"),
        BotTask("Inventory"),
        BotTask("Collect resources"),
        BotTask("Repair / restock"),
    ]
