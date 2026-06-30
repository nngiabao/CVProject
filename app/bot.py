from __future__ import annotations

from typing import Optional
from dataclasses import dataclass

from app.models import EmulatorInstance, WireGuardConfig


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
    wireguard_config: Optional[WireGuardConfig] = None
    wireguard_check: Optional[tuple[str, str]] = None
    tasks: Optional[list[BotTask]] = None

    def __post_init__(self) -> None:
        if self.tasks is None:
            self.tasks = default_tasks()

    @property
    def assigned(self) -> bool:
        return self.wireguard_config is not None

    def assign_wireguard(
        self,
        config: WireGuardConfig,
        check: Optional[tuple[str, str]] = None,
    ) -> None:
        self.wireguard_config = config
        self.wireguard_check = check

    def clear_wireguard(self) -> None:
        self.wireguard_config = None
        self.wireguard_check = None
        for task in self.tasks or []:
            task.set_enabled(False)

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
    def __init__(self) -> None:
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

    def assign_wireguard(
        self,
        instance_index: int,
        config: WireGuardConfig,
        check: Optional[tuple[str, str]] = None,
    ) -> None:
        self.person(instance_index).assign_wireguard(config, check)

    def clear_all_wireguard(self) -> None:
        for person in self.people.values():
            person.clear_wireguard()

def default_tasks() -> list[BotTask]:
    return [
        BotTask("Warmup"),
        BotTask("Merge stones"),
        BotTask("Daily cycle"),
        BotTask("Inventory"),
        BotTask("Collect resources"),
        BotTask("Repair / restock"),
    ]
