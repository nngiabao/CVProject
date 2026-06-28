from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from typing import Any, Optional
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.bot import BotManager
from app.emulators.base import EmulatorProvider
from app.models import EmulatorInstance, InstanceState, ProxyConfig
from app.process_utils import ldplayer_related_pids, vbox_nat_pids
from app.proxy_check import check_proxy
from app.proxy_parser import parse_proxy_line, parse_proxy_text
from app.python_redirect_engine import PythonRedirectEngine
from app.routing import RoutingService
from app.tqk_redirect_engine import TqkRedirectEngine
from app.windivert_guard import WinDivertGuard
from app.windivert_support import WinDivertStatus, check_windivert


class BackgroundSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self, provider: EmulatorProvider) -> None:
        super().__init__()
        self.provider = provider
        self.proxy_source_file = Path.cwd() / ".proxy_source.txt"
        self.proxy_assignment_file = Path.cwd() / ".proxy_assignments.json"
        self.nat_map_file = Path.cwd() / ".ldplayer_nat_map.json"
        self.nat_pid_map: dict[str, int] = {}
        self.instances: list[EmulatorInstance] = []
        self.proxies: list[ProxyConfig] = []
        self.proxy_cursor = 0
        self.routing = RoutingService()
        self.bot_manager = BotManager(self.routing)
        self.redirect_engine = self._create_redirect_engine()
        self.windivert_guard = WinDivertGuard()
        self.windivert_status: WinDivertStatus = check_windivert()
        self.proxy_summary: Optional[QLabel] = None
        self.bot_heading: Optional[QLabel] = None
        self.bot_metrics: list[QLabel] = []
        self.task_table: Optional[QTableWidget] = None
        self.background_tasks: list[BackgroundSignals] = []

        self.setWindowTitle("GrowStone Bot")
        self.resize(1240, 760)
        self.setMinimumSize(980, 620)
        self._build_ui()
        self._load_nat_pid_map()
        self.refresh_instances()
        self._load_proxy_assignments()
        self._render_instances()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(6000)
        self.refresh_timer.timeout.connect(self.refresh_instances)
        self.refresh_timer.start()

    def _create_redirect_engine(self) -> Any:
        if os.environ.get("GROWSTONE_REDIRECT_ENGINE", "python").strip().lower() == "tqk":
            return TqkRedirectEngine(Path.cwd())
        return PythonRedirectEngine(Path.cwd())

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 14, 14, 10)
        layout.setSpacing(10)
        layout.addWidget(self._build_header())
        layout.addWidget(self._build_toolbar())
        layout.addWidget(self._build_content(), 1)
        self.setCentralWidget(root)
        self.statusBar().showMessage(self.provider.display_name)

    def _build_header(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("header")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(18, 14, 18, 14)

        title_box = QVBoxLayout()
        title = QLabel("Emulator Proxy Manager")
        title.setObjectName("title")
        subtitle = QLabel("LDPlayer fleet control and per-instance proxy assignment")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        layout.addLayout(title_box)
        layout.addStretch()

        self.total_metric = QLabel("Total: 0")
        self.running_metric = QLabel("Running: 0")
        self.assigned_metric = QLabel("Assigned: 0")
        self.protection_metric = QLabel(self._protection_label())
        self.guard_metric = QLabel("Guard: 0 PIDs / 0 blocked")
        for metric in (
            self.total_metric,
            self.running_metric,
            self.assigned_metric,
            self.protection_metric,
            self.guard_metric,
        ):
            metric.setObjectName("metric")
            layout.addWidget(metric)

        refresh = QPushButton("Refresh")
        refresh.setObjectName("primary")
        refresh.clicked.connect(self.refresh_instances)
        layout.addWidget(refresh)
        return frame

    def _build_toolbar(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("toolbar")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)

        select_all = QPushButton("Select all")
        select_all.clicked.connect(self.table_select_all)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear_selection)
        load = QPushButton("Load SOCKS5 proxies")
        load.setObjectName("primary")
        load.clicked.connect(self.load_proxies_from_file)
        clear_proxies = QPushButton("Clear proxies")
        clear_proxies.clicked.connect(self.clear_proxies)
        self.proxy_summary = QLabel("SOCKS5 proxies: 0")
        self.proxy_summary.setObjectName("muted")
        assign = QPushButton("Assign proxy to selected")
        assign.setObjectName("primary")
        assign.clicked.connect(self.assign_proxies)
        check = QPushButton("Check selected proxies")
        check.clicked.connect(self.check_selected_proxies)
        route = QPushButton("Start proxy")
        route.setToolTip("Starts local authenticated SOCKS5 bridges for selected assigned instances")
        route.clicked.connect(self.start_proxy_routing)
        stop_route = QPushButton("Stop proxy routing")
        stop_route.clicked.connect(self.stop_proxy_routing)

        for button in (select_all, clear, load, clear_proxies, assign, check):
            layout.addWidget(button)
        layout.addWidget(self.proxy_summary)
        for button in (route, stop_route):
            layout.addWidget(button)
        layout.addStretch()
        return frame

    def _build_content(self) -> QWidget:
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_instance_panel())
        splitter.addWidget(self._build_bot_panel())
        splitter.setSizes([620, 620])
        splitter.setChildrenCollapsible(False)
        return splitter

    def _build_instance_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 12, 12, 12)
        heading = QLabel("Emulator instances")
        heading.setObjectName("metric")
        layout.addWidget(heading)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            [
                "Instance",
                "",
                "State",
                "Proxy status",
                "Proxy running",
                "Proxy IP",
                "Routing",
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self.render_selected_instance_tasks)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 165)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        return frame

    def _build_bot_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.bot_heading = QLabel("Bot tasks")
        heading = self.bot_heading
        heading.setObjectName("metric")
        layout.addWidget(heading)

        metrics = QHBoxLayout()
        self.bot_metrics = []
        for text in ("Enabled: 0", "Idle: 0", "Errors: 0"):
            label = QLabel(text)
            label.setObjectName("metric")
            metrics.addWidget(label)
            self.bot_metrics.append(label)
        metrics.addStretch()
        layout.addLayout(metrics)

        self.task_table = QTableWidget(0, 3)
        self.task_table.setHorizontalHeaderLabels(["Task", "On", "Status"])
        self.task_table.verticalHeader().setVisible(False)
        self.task_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.task_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.task_table.setAlternatingRowColors(True)
        self.task_table.itemChanged.connect(self.update_task_state_from_table)
        self.task_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.task_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.task_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        layout.addWidget(self.task_table, 1)
        self.render_selected_instance_tasks()
        return frame

    def refresh_instances(self) -> None:
        try:
            fresh_instances = self.provider.list_instances()
        except Exception as exc:
            QMessageBox.critical(self, "LDPlayer error", str(exc))
            return

        self.instances = fresh_instances
        self.bot_manager.sync_instances(self.instances)
        for instance in self.instances:
            assigned = self.bot_manager.person(instance.index).proxy
            instance.proxy = assigned.display if assigned else None
        self._update_windivert_guard()
        self._render_instances()
        self.statusBar().showMessage(self.provider.display_name)

    def _render_instances(self) -> None:
        self.table.setRowCount(len(self.instances))
        for row, instance in enumerate(self.instances):
            person = self.bot_manager.person(instance.index)
            assigned = person.proxy
            is_routed = instance.index in self.bot_manager.routed_indexes()
            route_target = self._format_pids(self._redirect_pids(instance)) if is_routed else "Off"
            values = (
                instance.name,
                "",
                instance.state.value,
                "Assigned" if assigned else "Unassigned",
                person.proxy_check[0] if assigned and person.proxy_check else "Not checked" if assigned else "—",
                person.proxy_check[1] if assigned and person.proxy_check else "—",
                route_target,
            )
            for column, value in enumerate(values):
                if column == 1:
                    self.table.setCellWidget(row, column, self._build_row_actions(instance.index))
                    continue
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, instance.index)
                if column == 0:
                    item.setForeground(QColor("#172033"))
                if column == 2:
                    state_colors = {
                        InstanceState.RUNNING: "#198754",
                        InstanceState.STARTING: "#b7791f",
                        InstanceState.STOPPED: "#69758a",
                        InstanceState.UNKNOWN: "#d64550",
                    }
                    color = state_colors[instance.state]
                    item.setForeground(QColor(color))
                if column == 3:
                    item.setForeground(QColor("#4169e1" if assigned else "#69758a"))
                if column == 4:
                    running_colors = {
                        "Running": "#198754",
                        "Redirected": "#198754",
                        "Routed": "#198754",
                        "Bridge OK": "#198754",
                        "Not running": "#d64550",
                        "Auth failed": "#d64550",
                        "Not checked": "#b7791f",
                        "IP check failed": "#b7791f",
                    }
                    item.setForeground(QColor(running_colors.get(value, "#69758a")))
                if column == 6:
                    item.setForeground(QColor("#198754" if is_routed else "#69758a"))
                self.table.setItem(row, column, item)

        running = sum(item.state == InstanceState.RUNNING for item in self.instances)
        self.total_metric.setText(f"Total: {len(self.instances)}")
        self.running_metric.setText(f"Running: {running}")
        self.assigned_metric.setText(f"Assigned: {self.bot_manager.assigned_count()}")
        self.protection_metric.setText(self._protection_label())
        guard_stats = self.windivert_guard.stats
        self.guard_metric.setText(
            f"Guard: {guard_stats.protected_pids} PIDs / "
            f"{guard_stats.blocked_tcp} TCP + {guard_stats.blocked_udp} UDP blocked"
        )

    def _build_row_actions(self, instance_index: int) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        actions = (
            (
                QStyle.SP_MediaPlay,
                "Start",
                lambda: self._run_instance_action(instance_index, self._start_instance_with_nat_mapping, "started"),
            ),
            (
                QStyle.SP_MediaStop,
                "Stop",
                lambda: self._run_instance_action(instance_index, self._stop_instance_and_clear_nat_mapping, "stopped"),
            ),
            (
                QStyle.SP_BrowserReload,
                "Restart",
                lambda: self._run_instance_action(instance_index, self._restart_instance_with_nat_mapping, "restarted"),
            ),
        )
        for icon_name, tooltip, callback in actions:
            button = QPushButton()
            button.setObjectName("iconButton")
            button.setIcon(self.style().standardIcon(icon_name))
            button.setToolTip(tooltip)
            button.setFixedSize(28, 28)
            button.clicked.connect(callback)
            layout.addWidget(button)
        layout.addStretch()
        return widget

    def render_selected_instance_tasks(self) -> None:
        if self.task_table is None:
            return

        indexes = self.selected_indexes()
        if not indexes:
            self._render_task_table(None)
            return
        self._render_task_table(indexes[0])

    def update_task_state_from_table(self, item: QTableWidgetItem) -> None:
        if item.column() != 1:
            return
        instance_index = self._task_panel_instance_index()
        if instance_index is None:
            return
        tasks = self.bot_manager.person(instance_index).tasks or []
        if item.row() >= len(tasks):
            return
        enabled = item.checkState() == Qt.Checked
        person = self.bot_manager.person(instance_index)
        if enabled and person.proxy is None:
            QMessageBox.information(
                self,
                "Assign proxy first",
                f"Assign a SOCKS5 proxy to instance {instance_index} before enabling bot tasks.",
            )
            self._render_task_table(instance_index)
            return
        if enabled and instance_index not in self.bot_manager.routed_indexes():
            self._start_task_after_routing(instance_index, item.row())
            self._render_task_table(instance_index)
            return
        person.set_task_enabled(item.row(), enabled)
        self._render_task_table(instance_index)

    def _render_task_table(self, instance_index: Optional[int]) -> None:
        if self.task_table is None:
            return

        self.task_table.blockSignals(True)
        if instance_index is None:
            self.task_table.setRowCount(0)
            if self.bot_heading is not None:
                self.bot_heading.setText("Bot tasks")
            self._set_bot_metrics(0, 0, 0)
            self.task_table.blockSignals(False)
            return

        instance = self._instance_by_index(instance_index)
        title = instance.name if instance is not None else f"Instance {instance_index}"
        if self.bot_heading is not None:
            self.bot_heading.setText(f"Bot tasks - {title}")

        person = self.bot_manager.person(instance_index)
        tasks = person.tasks or []
        self.task_table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            task_item = QTableWidgetItem(task.name)
            enabled_item = QTableWidgetItem()
            flags = Qt.ItemIsUserCheckable | Qt.ItemIsSelectable
            if person.proxy is not None:
                flags |= Qt.ItemIsEnabled
            enabled_item.setFlags(flags)
            enabled_item.setCheckState(Qt.Checked if task.enabled else Qt.Unchecked)
            status_item = QTableWidgetItem(task.status)
            status_item.setForeground(QColor("#198754" if task.status == "Idle" else "#69758a"))
            self.task_table.setItem(row, 0, task_item)
            self.task_table.setItem(row, 1, enabled_item)
            self.task_table.setItem(row, 2, status_item)
        enabled_count, idle_count, error_count = person.task_counts()
        self._set_bot_metrics(enabled_count, idle_count, error_count)
        self.task_table.blockSignals(False)

    def _task_panel_instance_index(self) -> Optional[int]:
        indexes = self.selected_indexes()
        return indexes[0] if indexes else None

    def _set_bot_metrics(self, enabled: int, idle: int, errors: int) -> None:
        values = (f"Enabled: {enabled}", f"Idle: {idle}", f"Errors: {errors}")
        for label, value in zip(self.bot_metrics, values):
            label.setText(value)

    @staticmethod
    def _format_pids(pids: set[int]) -> str:
        if not pids:
            return "Off"
        values = sorted(pids)
        if len(values) <= 3:
            return "PIDs " + ", ".join(str(pid) for pid in values)
        return "PIDs " + ", ".join(str(pid) for pid in values[:3]) + f" +{len(values) - 3}"

    def _redirect_pids(self, instance: EmulatorInstance) -> set[int]:
        mapped_pid = self._mapped_nat_pid(instance)
        return {mapped_pid} if mapped_pid is not None else set()

    def _protected_pids(self, instance: EmulatorInstance) -> set[int]:
        return instance.live_pids() | self._redirect_pids(instance)

    def _missing_nat_mapping(self, instance: EmulatorInstance) -> bool:
        return bool(vbox_nat_pids()) and self._mapped_nat_pid(instance) is None

    def _ensure_nat_mapping(self, instance: EmulatorInstance) -> Optional[int]:
        mapped_pid = self._mapped_nat_pid(instance)
        if mapped_pid is not None:
            return mapped_pid
        if not instance.identity:
            return None

        current_nat_pids = vbox_nat_pids()
        if not current_nat_pids:
            return None

        self._drop_stale_nat_mappings()
        used_by_other_instances = {
            pid
            for identity, pid in self.nat_pid_map.items()
            if identity != instance.identity and self._pid_is_alive(pid)
        }
        available = sorted(current_nat_pids - used_by_other_instances)
        if not available:
            return None

        chosen = available[0]
        self.nat_pid_map[instance.identity] = chosen
        self._save_nat_pid_map()
        return chosen

    def _drop_stale_nat_mappings(self) -> None:
        stale = [identity for identity, pid in self.nat_pid_map.items() if not self._pid_is_alive(pid)]
        if not stale:
            return
        for identity in stale:
            self.nat_pid_map.pop(identity, None)
        self._save_nat_pid_map()

    def _mapped_nat_pid(self, instance: EmulatorInstance) -> Optional[int]:
        if not instance.identity:
            return None
        pid = self.nat_pid_map.get(instance.identity)
        if pid is None:
            return None
        if not self._pid_is_alive(pid):
            self.nat_pid_map.pop(instance.identity, None)
            self._save_nat_pid_map()
            return None
        return pid

    def _load_nat_pid_map(self) -> None:
        try:
            data = json.loads(self.nat_map_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.nat_pid_map = {}
            self._save_nat_pid_map()
            return
        if not isinstance(data, dict):
            self.nat_pid_map = {}
            self._save_nat_pid_map()
            return
        self.nat_pid_map = {
            str(identity): int(pid)
            for identity, pid in data.items()
            if isinstance(identity, str) and isinstance(pid, int) and self._pid_is_alive(pid)
        }
        self._save_nat_pid_map()

    def _save_nat_pid_map(self) -> None:
        try:
            self.nat_map_file.write_text(json.dumps(self.nat_pid_map, indent=2), encoding="utf-8")
        except OSError:
            return

    def _start_instance_with_nat_mapping(self, instance_index: int) -> None:
        before = vbox_nat_pids()
        self.provider.start(instance_index)
        self._map_nat_pid_after_start(instance_index, before)

    def _restart_instance_with_nat_mapping(self, instance_index: int) -> None:
        self._clear_nat_mapping(instance_index)
        before = vbox_nat_pids()
        self.provider.restart(instance_index)
        self._map_nat_pid_after_start(instance_index, before)

    def _stop_instance_and_clear_nat_mapping(self, instance_index: int) -> None:
        self._clear_nat_mapping(instance_index)
        self.provider.stop(instance_index)

    def _map_nat_pid_after_start(self, instance_index: int, before: set[int]) -> None:
        deadline = time.monotonic() + 12.0
        chosen: Optional[int] = None
        while time.monotonic() < deadline:
            new_pids = vbox_nat_pids() - before
            if new_pids:
                chosen = sorted(new_pids)[-1]
                break
            time.sleep(0.5)
        if chosen is None:
            return
        identity = self._instance_identity_for_index(instance_index)
        if identity is None:
            return
        self.nat_pid_map[identity] = chosen
        self._save_nat_pid_map()

    def _clear_nat_mapping(self, instance_index: int) -> None:
        identity = self._instance_identity_for_index(instance_index)
        if identity is None:
            return
        if identity in self.nat_pid_map:
            self.nat_pid_map.pop(identity, None)
            self._save_nat_pid_map()

    def _instance_identity_for_index(self, instance_index: int) -> Optional[str]:
        instance = self._instance_by_index(instance_index)
        if instance is not None and instance.identity:
            return instance.identity
        if hasattr(self.provider, "_instance_identity"):
            try:
                return str(self.provider._instance_identity(instance_index))
            except Exception:
                return None
        return None

    @staticmethod
    def _pid_is_alive(pid: int) -> bool:
        try:
            result = subprocess.run(
                ["tasklist", "/fi", f"PID eq {pid}", "/fo", "csv", "/nh"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0 and f'"{pid}"' in result.stdout

    def _start_task_after_routing(self, instance_index: int, task_row: int) -> None:
        instance = self._instance_by_index(instance_index)
        if instance is None or not (instance.live_pids() or self._redirect_pids(instance)):
            QMessageBox.information(
                self,
                "Start LDPlayer first",
                f"Start instance {instance_index} before enabling bot tasks.",
            )
            return

        self.statusBar().showMessage(f"Starting proxy routing for instance {instance_index}...", 5000)

        def work() -> dict[str, Any]:
            return {
                "instance_index": instance_index,
                "task_row": task_row,
                "routed_result": self._apply_saved_proxy_routing_blocking(instance_index),
            }

        self._run_background(
            work,
            self._finish_start_task_after_routing,
            "Bot start failed",
        )

    def _finish_start_task_after_routing(self, result: object) -> None:
        if not isinstance(result, dict):
            self.refresh_instances()
            return

        instance_index = int(result["instance_index"])
        task_row = int(result["task_row"])
        routed_result = result.get("routed_result")

        if isinstance(routed_result, dict):
            warning = routed_result.get("warning")
            if warning:
                self.refresh_instances()
                QMessageBox.warning(self, str(routed_result.get("title", "Proxy routing failed")), str(warning))
                return

        self.bot_manager.person(instance_index).set_task_enabled(task_row, True)
        self.refresh_instances()
        if not self._update_windivert_guard():
            self.bot_manager.person(instance_index).set_task_enabled(task_row, False)
            self.bot_manager.stop_routing(instance_index)
            self._clear_emulator_proxy(instance_index)
            self.refresh_instances()
            QMessageBox.warning(
                self,
                "Kill switch failed",
                f"WinDivert kill switch is not active: {self._protection_failure_message()}",
            )
            return
        self.render_selected_instance_tasks()

        message = "Bot task enabled"
        if isinstance(routed_result, dict) and routed_result.get("message"):
            message = str(routed_result["message"])
        self.statusBar().showMessage(message, 5000)

    def table_select_all(self) -> None:
        self.table.selectAll()

    def clear_selection(self) -> None:
        self.table.clearSelection()

    def selected_indexes(self) -> list[int]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [self.instances[row].index for row in rows]

    def load_proxies_from_file(self) -> None:
        proxy_file = self._resolve_proxy_file()
        if proxy_file is None:
            return

        self._load_proxy_file(proxy_file)

    def _resolve_proxy_file(self) -> Optional[Path]:
        saved_proxy_file = self._saved_proxy_file()
        if saved_proxy_file is not None:
            return saved_proxy_file

        discovered_proxy_file = self._discover_proxy_file()
        if discovered_proxy_file is not None:
            self._save_proxy_file(discovered_proxy_file)
            return discovered_proxy_file

        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Load SOCKS5 proxies",
            str(Path.home() / "Downloads"),
            "Text files (*.txt);;All files (*)",
        )
        if not file_name:
            return None

        proxy_file = Path(file_name)
        self._save_proxy_file(proxy_file)
        return proxy_file

    def _load_proxy_file(self, proxy_file: Path) -> None:
        try:
            proxy_text = proxy_file.read_text(encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Proxy file error", str(exc))
            return

        parsed_proxies, errors = parse_proxy_text(proxy_text, "socks5")
        proxies = []
        for proxy in parsed_proxies:
            if proxy.scheme != "socks5":
                errors.append(f"Skipped {proxy.display}: only SOCKS5 proxies are supported")
                continue
            proxies.append(proxy)
        self.proxies = proxies
        self.proxy_cursor = 0
        summary = f"SOCKS5 proxies: {len(proxies)}"
        if errors:
            summary += f" · Invalid: {len(errors)}"
            QMessageBox.warning(self, "Some proxies were skipped", "\n".join(errors[:10]))
        self._set_proxy_summary(summary)
        self._render_instances()
        self.statusBar().showMessage(f"{summary} from {proxy_file.name}", 5000)

    def _saved_proxy_file(self) -> Optional[Path]:
        try:
            value = self.proxy_source_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not value:
            return None
        path = Path(value)
        return path if path.is_file() and path.suffix.lower() == ".txt" else None

    def _discover_proxy_file(self) -> Optional[Path]:
        downloads = Path.home() / "Downloads"
        if not downloads.is_dir():
            return None

        candidates = [path for path in downloads.glob("Webshare*.txt") if path.is_file()]
        if not candidates:
            candidates = [path for path in downloads.glob("*.txt") if path.is_file()]
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def _save_proxy_file(self, proxy_file: Path) -> None:
        try:
            self.proxy_source_file.write_text(str(proxy_file.resolve()), encoding="utf-8")
        except OSError:
            pass

    def clear_proxies(self) -> None:
        self._clear_all_emulator_proxies()
        self.proxies.clear()
        self.bot_manager.clear_all_proxies()
        self.windivert_guard.stop()
        self.proxy_cursor = 0
        self._set_proxy_summary("SOCKS5 proxies: 0")
        self._save_proxy_assignments()
        self._render_instances()

    def assign_proxies(self) -> None:
        indexes = self.selected_indexes()
        if not indexes:
            QMessageBox.information(self, "Select instances", "Select one or more emulator instances.")
            return
        if not self.proxies:
            QMessageBox.information(self, "Load proxies", "Load a SOCKS5 proxy list before assigning proxies.")
            return

        assignment_mode, selected_proxy = self._choose_proxy_assignment(len(indexes))
        if assignment_mode is None:
            return

        clear_failures: list[str] = []
        if assignment_mode == "single":
            proxy = selected_proxy
            if proxy is None:
                return
            for instance_index in indexes:
                clear_error = self._clear_emulator_proxy(instance_index)
                if clear_error:
                    clear_failures.append(f"Instance {instance_index}: {clear_error}")
                self.bot_manager.assign_proxy(instance_index, proxy, self._check_proxy(proxy))
        else:
            for position, instance_index in enumerate(indexes):
                proxy_index = (self.proxy_cursor + position) % len(self.proxies)
                proxy = self.proxies[proxy_index]
                clear_error = self._clear_emulator_proxy(instance_index)
                if clear_error:
                    clear_failures.append(f"Instance {instance_index}: {clear_error}")
                self.bot_manager.assign_proxy(instance_index, proxy, self._check_proxy(proxy))
            self.proxy_cursor += len(indexes)
        self._save_proxy_assignments()
        self._update_windivert_guard()
        self._render_instances()
        if clear_failures:
            QMessageBox.warning(self, "Some Android proxies were not cleared", "\n".join(clear_failures[:10]))
        self.statusBar().showMessage(f"Assigned proxies to {len(indexes)} instance(s)", 5000)

    def _load_proxy_assignments(self) -> None:
        try:
            raw_assignments = json.loads(self.proxy_assignment_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "Proxy assignment error", f"Could not load saved assignments: {exc}")
            return
        if not isinstance(raw_assignments, dict):
            QMessageBox.warning(self, "Proxy assignment error", "Saved proxy assignments must be a JSON object.")
            return

        loaded = 0
        errors: list[str] = []
        for instance_key, proxy_url in raw_assignments.items():
            instance_index = self._instance_index_for_assignment_key(instance_key)
            if instance_index is None:
                errors.append(f"{instance_key}: instance not found")
                continue
            if not isinstance(proxy_url, str):
                errors.append(f"Instance {instance_index}: proxy must be text")
                continue
            try:
                proxy = parse_proxy_line(proxy_url, "socks5")
            except (ValueError, TypeError) as exc:
                errors.append(f"Instance {instance_index}: {exc}")
                continue
            if proxy.scheme != "socks5":
                errors.append(f"Instance {instance_index}: only SOCKS5 proxies are supported")
                continue
            self.bot_manager.assign_proxy(instance_index, proxy)
            loaded += 1

        if loaded:
            self._set_proxy_summary(f"Saved assignments: {loaded}")
            self.statusBar().showMessage(f"Loaded {loaded} saved proxy assignment(s)", 5000)
        if errors:
            QMessageBox.warning(self, "Some saved assignments were skipped", "\n".join(errors[:10]))
        if loaded:
            self._save_proxy_assignments()

    def _save_proxy_assignments(self) -> None:
        assignments = {}
        for instance_index, person in sorted(self.bot_manager.people.items()):
            if person.proxy is None:
                continue
            key = self._assignment_key(instance_index)
            if key is not None:
                assignments[key] = person.proxy.connection_url
        try:
            if assignments:
                self.proxy_assignment_file.write_text(
                    json.dumps(assignments, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
            elif self.proxy_assignment_file.exists():
                self.proxy_assignment_file.unlink()
        except OSError as exc:
            QMessageBox.warning(self, "Proxy assignment error", f"Could not save assignments: {exc}")

    def _assignment_key(self, instance_index: int) -> Optional[str]:
        return str(instance_index)

    def _instance_index_for_assignment_key(self, key: object) -> Optional[int]:
        if isinstance(key, str):
            try:
                return int(key)
            except ValueError:
                if key.startswith("ldplayer:"):
                    try:
                        return int(key.rsplit(":", 1)[1])
                    except (IndexError, ValueError):
                        return None
                return None
        if isinstance(key, int):
            return key
        return None

    def _choose_proxy_assignment(self, selected_count: int) -> tuple[Optional[str], Optional[ProxyConfig]]:
        proxy_items = [f"{index + 1}. {proxy.host}:{proxy.port}" for index, proxy in enumerate(self.proxies)]
        items = proxy_items.copy()
        auto_label = "Auto assign different proxies"
        if selected_count > 1:
            items.insert(0, auto_label)

        choice, accepted = QInputDialog.getItem(
            self,
            "Assign proxy",
            "Select proxy:",
            items,
            0,
            False,
        )
        if not accepted:
            return None, None
        if choice == auto_label:
            return "auto", None

        proxy_index = proxy_items.index(choice)
        return "single", self.proxies[proxy_index]

    def start_proxy_routing(self) -> None:
        indexes = self.selected_indexes()
        if not indexes:
            QMessageBox.information(self, "Select instances", "Select one or more emulator instances.")
            return

        failures: list[str] = []
        applied_routes: list[str] = []
        started_indexes: list[int] = []
        started = 0
        for instance_index in indexes:
            person = self.bot_manager.person(instance_index)
            proxy = person.proxy
            if proxy is None:
                failures.append(f"Instance {instance_index}: assign a proxy first")
                continue
            instance = self._instance_by_index(instance_index)
            if instance is None or not instance.live_pids():
                failures.append(f"Instance {instance_index}: start LDPlayer before enabling WinDivert protection")
                continue
            nat_pid = self._ensure_nat_mapping(instance)
            if vbox_nat_pids() and nat_pid is None:
                failures.append(
                    f"Instance {instance_index}: no available VBoxNetNAT PID in the NAT pool"
                )
                continue
            status, proxy_ip = self._check_proxy(proxy)
            person.proxy_check = (status, proxy_ip)
            if status != "Running":
                failures.append(f"Instance {instance_index}: proxy check failed ({status})")
                continue
            try:
                route_pids = self._redirect_pids(instance)
                self.redirect_engine.start_many(instance_index, route_pids, proxy)
                self.bot_manager.start_direct_routing(instance_index)
                clear_error = self._clear_emulator_proxy(instance_index)
                if clear_error:
                    raise RuntimeError(f"Could not clear Android proxy before tunnel start: {clear_error}")
                person.proxy_check = ("Redirected", proxy_ip)
                applied_routes.append(
                    f"Instance {instance_index}: {self._format_pids(route_pids)} -> {proxy.host}"
                )
                started_indexes.append(instance_index)
                started += 1
            except Exception as exc:
                self.redirect_engine.stop(instance_index)
                self.bot_manager.stop_routing(instance_index)
                self._clear_emulator_proxy(instance_index)
                failures.append(f"Instance {instance_index}: {exc}")

        guard_ready = self._update_windivert_guard()
        if started and not guard_ready:
            for instance_index in started_indexes:
                self.redirect_engine.stop(instance_index)
                self.bot_manager.stop_routing(instance_index)
                self._clear_emulator_proxy(instance_index)
            started = 0
            applied_routes.clear()
            failures.append(f"WinDivert kill switch is not active: {self._protection_failure_message()}")
        self._render_instances()
        if failures:
            QMessageBox.warning(self, "Some routing sessions failed", "\n".join(failures))
        if started:
            self._show_routing_status(started, applied_routes)

    def stop_proxy_routing(self) -> None:
        indexes = self.selected_indexes()
        if not indexes:
            QMessageBox.information(self, "Select instances", "Select one or more emulator instances.")
            return

        for instance_index in indexes:
            self.redirect_engine.stop(instance_index)
            self.bot_manager.stop_routing(instance_index)
            self._clear_emulator_proxy(instance_index)
        self._update_windivert_guard()
        if not self.bot_manager.routed_indexes():
            self.redirect_engine.stop_all()
        self._render_instances()
        self.statusBar().showMessage(f"Stopped proxy routing for {len(indexes)} instance(s)", 5000)

    def check_selected_proxies(self) -> None:
        indexes = self.selected_indexes()
        if not indexes:
            QMessageBox.information(self, "Select instances", "Select one or more emulator instances.")
            return

        checked = 0
        for instance_index in indexes:
            person = self.bot_manager.person(instance_index)
            proxy = person.proxy
            if not proxy:
                continue
            person.proxy_check = self._check_proxy(proxy)
            checked += 1
        self._render_instances()
        self.statusBar().showMessage(f"Checked {checked} assigned proxy/proxies", 5000)

    def _check_proxy(self, proxy: ProxyConfig) -> tuple[str, str]:
        return check_proxy(proxy)

    def _protection_label(self) -> str:
        if self.windivert_guard.running:
            return "Protection: Kill switch on"
        if self.windivert_status.available:
            return "Protection: WinDivert ready"
        return f"Protection: {self.windivert_status.message}"

    def _show_routing_status(self, started: int, applied_routes: list[str]) -> None:
        applied_text = "\n".join(applied_routes[:8])
        if len(applied_routes) > 8:
            applied_text += f"\n...and {len(applied_routes) - 8} more"
        final_status = f"Started proxy routing for {started} instance(s)"
        if self.windivert_guard.running:
            QMessageBox.information(
                self,
                "Routing protected",
                "Tqk WinDivert proxy routing is running, and the leak guard is active "
                "for LDPlayer-related process IDs.\n\n"
                f"Proxy route:\n{applied_text}\n\n"
                "TCP is redirected through the assigned SOCKS proxy. "
                "DNS is handled through secure DNS, and unhandled UDP/IPv6 traffic is blocked.",
            )
        else:
            final_status += f" - kill switch failed: {self._protection_failure_message()}"
        self.statusBar().showMessage(final_status, 5000)

    def _update_windivert_guard(self) -> bool:
        pids = self._active_routed_pids()
        if not pids:
            self.windivert_guard.stop()
            return True
        if self.windivert_guard.running:
            self.windivert_guard.update_pids(pids, block_public_tcp=not self.redirect_engine.running)
            return True
        self.windivert_status = check_windivert()
        if self.windivert_status.available:
            self.windivert_guard.start(pids, block_public_tcp=not self.redirect_engine.running)
            time_limit = time.monotonic() + 2.0
            while time.monotonic() < time_limit:
                if self.windivert_guard.running:
                    return True
                if self.windivert_guard.stats.last_error:
                    return False
                time.sleep(0.05)
        return self.windivert_guard.running

    def _protection_failure_message(self) -> str:
        if self.windivert_guard.stats.last_error:
            return self.windivert_guard.stats.last_error
        if self.redirect_engine.last_error:
            return self.redirect_engine.last_error
        return self.windivert_status.message

    def _active_routed_pids(self) -> set[int]:
        instance_pids = self.bot_manager.routed_pids()
        if not instance_pids:
            return set()
        mapped_nat_pids: set[int] = set()
        for instance_index in self.bot_manager.routed_indexes():
            instance = self._instance_by_index(instance_index)
            if instance is not None:
                mapped_nat_pids.update(self._protected_pids(instance))
        return instance_pids | mapped_nat_pids | ldplayer_related_pids()

    def _instance_by_index(self, instance_index: int) -> Optional[EmulatorInstance]:
        return next((instance for instance in self.instances if instance.index == instance_index), None)

    def _display_instance_index(self, instance: EmulatorInstance) -> str:
        return str(instance.index)

    def _clear_emulator_proxy(self, instance_index: int) -> Optional[str]:
        try:
            self.provider.clear_http_proxy(instance_index)
        except Exception as exc:
            return str(exc)
        return None

    def _clear_all_emulator_proxies(self) -> None:
        for instance_index in list(self.bot_manager.routed_indexes()):
            self._clear_emulator_proxy(instance_index)

    def _set_proxy_summary(self, text: str) -> None:
        if self.proxy_summary is not None:
            self.proxy_summary.setText(text)

    def _run_selected(self, action: Callable[[int], None], verb: str) -> None:
        indexes = self.selected_indexes()
        if not indexes:
            QMessageBox.information(self, "Select instances", "Select one or more emulator instances.")
            return

        failures: list[str] = []
        for index in indexes:
            try:
                action(index)
            except Exception as exc:
                failures.append(f"Instance {index}: {exc}")
        self.refresh_instances()

        if failures:
            QMessageBox.warning(self, "Some actions failed", "\n".join(failures))
        else:
            self.statusBar().showMessage(f"{len(indexes)} instance(s) {verb}", 5000)

    def _run_instance_action(
        self,
        instance_index: int,
        action: Callable[[int], None],
        verb: str,
    ) -> None:
        self.statusBar().showMessage(f"Instance {instance_index} {verb}...", 5000)
        self.refresh_timer.stop()

        def work() -> dict[str, Any]:
            action(instance_index)
            return {
                "instance_index": instance_index,
                "verb": verb,
            }

        self._run_background(
            work,
            self._finish_instance_action,
            "Instance action failed",
        )

    def _finish_instance_action(self, result: object) -> None:
        self.refresh_timer.start()
        if not isinstance(result, dict):
            self.refresh_instances()
            return

        instance_index = int(result["instance_index"])
        verb = str(result["verb"])
        self.refresh_instances()

        self.statusBar().showMessage(f"Instance {instance_index} {verb}", 5000)

    def _run_background(
        self,
        work: Callable[[], object],
        on_finished: Callable[[object], None],
        error_title: str,
    ) -> None:
        signals = BackgroundSignals()
        self.background_tasks.append(signals)

        def cleanup() -> None:
            if signals in self.background_tasks:
                self.background_tasks.remove(signals)
            if not self.refresh_timer.isActive():
                self.refresh_timer.start()

        def finish(result: object) -> None:
            cleanup()
            on_finished(result)

        def fail(message: str) -> None:
            cleanup()
            QMessageBox.warning(self, error_title, message)
            self.refresh_instances()

        signals.finished.connect(finish)
        signals.failed.connect(fail)

        def runner() -> None:
            try:
                signals.finished.emit(work())
            except Exception as exc:
                signals.failed.emit(str(exc))

        threading.Thread(target=runner, name="ui-background-task", daemon=True).start()

    def _apply_saved_proxy_routing_blocking(self, instance_index: int) -> Optional[dict[str, str]]:
        person = self.bot_manager.person(instance_index)
        proxy = person.proxy
        if proxy is None:
            return None

        status, proxy_ip = self._check_proxy(proxy)
        person.proxy_check = (status, proxy_ip)
        if status != "Running":
            return {
                "title": "Saved proxy failed",
                "warning": f"Instance {instance_index} has a saved proxy, but the proxy check failed ({status}).",
            }
        instance = self._instance_by_index(instance_index)
        if instance is None or not instance.live_pids():
            return {
                "title": "Proxy routing failed",
                "warning": f"Instance {instance_index}: start LDPlayer before enabling WinDivert protection.",
            }
        nat_pid = self._ensure_nat_mapping(instance)
        if vbox_nat_pids() and nat_pid is None:
            return {
                "title": "Proxy routing failed",
                "warning": f"Instance {instance_index}: no available VBoxNetNAT PID in the NAT pool.",
            }

        try:
            route_pids = self._redirect_pids(instance)
            self.redirect_engine.start_many(instance_index, route_pids, proxy)
            self.bot_manager.start_direct_routing(instance_index)
            clear_error = self._clear_emulator_proxy(instance_index)
            if clear_error:
                raise RuntimeError(f"Could not clear Android proxy before tunnel start: {clear_error}")
            person.proxy_check = ("Redirected", proxy_ip)
            applied_proxy = self._format_pids(route_pids)
        except Exception as exc:
            self.bot_manager.stop_routing(instance_index)
            self._clear_emulator_proxy(instance_index)
            self.redirect_engine.stop(instance_index)
            return {
                "title": "Proxy routing failed",
                "warning": f"Instance {instance_index}: {exc}",
            }

        return {
            "message": (
                f"Bot task started proxy route for instance {instance_index}: "
                f"{applied_proxy} -> {person.proxy_check[1]}"
            )
        }

    def closeEvent(self, event: QCloseEvent) -> None:
        self._clear_all_emulator_proxies()
        self.redirect_engine.stop_all()
        self.windivert_guard.stop()
        self.bot_manager.clear_all_proxies()
        super().closeEvent(event)
