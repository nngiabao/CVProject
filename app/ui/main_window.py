from __future__ import annotations

import json
from typing import Optional
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
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
from app.process_utils import ldplayer_related_pids
from app.proxy_check import check_proxy
from app.proxy_parser import parse_proxy_line, parse_proxy_text
from app.routing import RoutingService
from app.windivert_guard import WinDivertGuard
from app.windivert_support import WinDivertStatus, check_windivert


class MainWindow(QMainWindow):
    def __init__(self, provider: EmulatorProvider) -> None:
        super().__init__()
        self.provider = provider
        self.proxy_source_file = Path.cwd() / ".proxy_source.txt"
        self.proxy_assignment_file = Path.cwd() / ".proxy_assignments.json"
        self.instances: list[EmulatorInstance] = []
        self.proxies: list[ProxyConfig] = []
        self.proxy_cursor = 0
        self.routing = RoutingService()
        self.bot_manager = BotManager(self.routing)
        self.windivert_guard = WinDivertGuard()
        self.windivert_status: WinDivertStatus = check_windivert()
        self.proxy_summary: Optional[QLabel] = None
        self.bot_heading: Optional[QLabel] = None
        self.bot_metrics: list[QLabel] = []
        self.task_table: Optional[QTableWidget] = None

        self.setWindowTitle("GrowStone Bot")
        self.resize(1240, 760)
        self.setMinimumSize(980, 620)
        self._build_ui()
        self._load_proxy_assignments()
        self.refresh_instances()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(3000)
        self.refresh_timer.timeout.connect(self.refresh_instances)
        self.refresh_timer.start()

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

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            [
                "#",
                "Instance",
                "",
                "PID",
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
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        self.table.setColumnWidth(1, 145)
        header.setSectionResizeMode(8, QHeaderView.Stretch)
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
            route = self.bot_manager.session(instance.index)
            values = (
                self._display_instance_index(instance),
                instance.name,
                "",
                str(instance.pid or "—"),
                instance.state.value,
                "Assigned" if assigned else "Unassigned",
                person.proxy_check[0] if assigned and person.proxy_check else "Not checked" if assigned else "—",
                person.proxy_check[1] if assigned and person.proxy_check else "—",
                route.local_proxy if route else "Off",
            )
            for column, value in enumerate(values):
                if column == 2:
                    self.table.setCellWidget(row, column, self._build_row_actions(instance.index))
                    continue
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, instance.index)
                if column == 1:
                    item.setForeground(QColor("#172033"))
                if column == 4:
                    state_colors = {
                        InstanceState.RUNNING: "#198754",
                        InstanceState.STARTING: "#b7791f",
                        InstanceState.STOPPED: "#69758a",
                        InstanceState.UNKNOWN: "#d64550",
                    }
                    color = state_colors[instance.state]
                    item.setForeground(QColor(color))
                if column == 5:
                    item.setForeground(QColor("#4169e1" if assigned else "#69758a"))
                if column == 6:
                    running_colors = {
                        "Running": "#198754",
                        "Not running": "#d64550",
                        "Auth failed": "#d64550",
                        "Not checked": "#b7791f",
                    }
                    item.setForeground(QColor(running_colors.get(value, "#69758a")))
                if column == 8:
                    item.setForeground(QColor("#198754" if route else "#69758a"))
                self.table.setItem(row, column, item)

        running = sum(item.state == InstanceState.RUNNING for item in self.instances)
        self.total_metric.setText(f"Total: {len(self.instances)}")
        self.running_metric.setText(f"Running: {running}")
        self.assigned_metric.setText(f"Assigned: {self.bot_manager.assigned_count()}")
        self.protection_metric.setText(self._protection_label())
        guard_stats = self.windivert_guard.stats
        self.guard_metric.setText(f"Guard: {guard_stats.protected_pids} PIDs / {guard_stats.blocked} blocked")

    def _build_row_actions(self, instance_index: int) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        actions = (
            (
                QStyle.SP_MediaPlay,
                "Start",
                lambda: self._run_instance_action(instance_index, self.provider.start, "started", apply_saved_proxy=True),
            ),
            (QStyle.SP_MediaStop, "Stop", lambda: self._run_instance_action(instance_index, self.provider.stop, "stopped")),
            (
                QStyle.SP_BrowserReload,
                "Restart",
                lambda: self._run_instance_action(instance_index, self.provider.restart, "restarted", apply_saved_proxy=True),
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

        if assignment_mode == "single":
            proxy = selected_proxy
            if proxy is None:
                return
            for instance_index in indexes:
                self._clear_emulator_proxy(instance_index)
                self.bot_manager.assign_proxy(instance_index, proxy, self._check_proxy(proxy))
        else:
            for position, instance_index in enumerate(indexes):
                proxy_index = (self.proxy_cursor + position) % len(self.proxies)
                proxy = self.proxies[proxy_index]
                self._clear_emulator_proxy(instance_index)
                self.bot_manager.assign_proxy(instance_index, proxy, self._check_proxy(proxy))
            self.proxy_cursor += len(indexes)
        self._save_proxy_assignments()
        self._update_windivert_guard()
        self._render_instances()
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
            try:
                instance_index = int(instance_key)
            except (TypeError, ValueError):
                errors.append(f"{instance_key}: invalid instance index")
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

    def _save_proxy_assignments(self) -> None:
        assignments = {
            str(instance_index): person.proxy.connection_url
            for instance_index, person in sorted(self.bot_manager.people.items())
            if person.proxy is not None
        }
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

    def _choose_proxy_assignment(self, selected_count: int) -> tuple[Optional[str], Optional[ProxyConfig]]:
        proxy_items = [f"{index + 1}. {proxy.display}" for index, proxy in enumerate(self.proxies)]
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
        started = 0
        for instance_index in indexes:
            person = self.bot_manager.person(instance_index)
            proxy = person.proxy
            if proxy is None:
                failures.append(f"Instance {instance_index}: assign a proxy first")
                continue
            instance = self._instance_by_index(instance_index)
            if instance is None or instance.pid is None:
                failures.append(f"Instance {instance_index}: start LDPlayer before enabling WinDivert protection")
                continue
            status, proxy_ip = self._check_proxy(proxy)
            person.proxy_check = (status, proxy_ip)
            if status != "Running":
                failures.append(f"Instance {instance_index}: proxy check failed ({status})")
                continue
            try:
                session = self.bot_manager.start_routing(instance_index)
                applied_proxy = self.provider.set_http_proxy(instance_index, session.listen_host, session.listen_port)
                applied_routes.append(f"Instance {instance_index}: {applied_proxy}")
                started += 1
            except Exception as exc:
                self.bot_manager.stop_routing(instance_index)
                failures.append(f"Instance {instance_index}: {exc}")

        self._update_windivert_guard()
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
            self.bot_manager.stop_routing(instance_index)
            self._clear_emulator_proxy(instance_index)
        self._update_windivert_guard()
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
        if self.windivert_guard.running:
            QMessageBox.information(
                self,
                "Routing protected",
                "Local authenticated proxy routing is running, and the WinDivert kill switch is active "
                "for LDPlayer-related process IDs.\n\n"
                f"Android proxy applied:\n{applied_text}\n\n"
                "Direct public TCP/UDP traffic from protected LDPlayer processes will be blocked. "
                "Transparent redirect is still the next layer.",
            )
        elif self.windivert_status.available:
            error = self.windivert_guard.stats.last_error
            message = "WinDivert is available, but the kill switch did not start."
            if error:
                message += f"\n\n{error}"
            QMessageBox.warning(self, "WinDivert not protecting", message)
        else:
            QMessageBox.warning(
                self,
                "WinDivert not active",
                "Local authenticated proxy routing is running, but WinDivert is not active yet.\n\n"
                f"{self.windivert_status.message}\n\n"
                "Until transparent redirection is added, LDPlayer traffic will not be forced through "
                "the proxy automatically.",
            )
        self.statusBar().showMessage(f"Started proxy routing for {started} instance(s)", 5000)

    def _update_windivert_guard(self) -> None:
        pids = self._active_routed_pids()
        if not pids:
            self.windivert_guard.stop()
            return
        self.windivert_status = check_windivert()
        if self.windivert_guard.running:
            self.windivert_guard.update_pids(pids)
        elif self.windivert_status.available:
            self.windivert_guard.start(pids)

    def _active_routed_pids(self) -> set[int]:
        instance_pids = self.bot_manager.routed_pids()
        if not instance_pids:
            return set()
        return instance_pids | ldplayer_related_pids()

    def _instance_by_index(self, instance_index: int) -> Optional[EmulatorInstance]:
        return next((instance for instance in self.instances if instance.index == instance_index), None)

    def _display_instance_index(self, instance: EmulatorInstance) -> str:
        local_index = instance.index % 10000
        if instance.index >= 10000:
            return f"{instance.platform}:{local_index}"
        return str(instance.index)

    def _clear_emulator_proxy(self, instance_index: int) -> None:
        try:
            self.provider.clear_http_proxy(instance_index)
        except Exception:
            pass

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
        apply_saved_proxy: bool = False,
    ) -> None:
        try:
            action(instance_index)
        except Exception as exc:
            QMessageBox.warning(self, "Instance action failed", f"Instance {instance_index}: {exc}")
            return
        self.refresh_instances()
        if apply_saved_proxy and self._apply_saved_proxy_routing(instance_index):
            return
        self.statusBar().showMessage(f"Instance {instance_index} {verb}", 5000)

    def _apply_saved_proxy_routing(self, instance_index: int) -> bool:
        person = self.bot_manager.person(instance_index)
        proxy = person.proxy
        if proxy is None:
            return False

        status, proxy_ip = self._check_proxy(proxy)
        person.proxy_check = (status, proxy_ip)
        if status != "Running":
            self._render_instances()
            QMessageBox.warning(
                self,
                "Saved proxy failed",
                f"Instance {instance_index} has a saved proxy, but the proxy check failed ({status}).",
            )
            return True

        try:
            session = self.bot_manager.start_routing(instance_index)
            applied_proxy = self.provider.set_http_proxy(instance_index, session.listen_host, session.listen_port)
        except Exception as exc:
            self.bot_manager.stop_routing(instance_index)
            self._render_instances()
            QMessageBox.warning(
                self,
                "Saved proxy routing failed",
                f"Instance {instance_index}: {exc}",
            )
            return True

        self.refresh_instances()
        self._update_windivert_guard()
        self._render_instances()
        self.statusBar().showMessage(
            f"Instance {instance_index} started with saved proxy route {applied_proxy}",
            5000,
        )
        return True

    def closeEvent(self, event: QCloseEvent) -> None:
        self._clear_all_emulator_proxies()
        self.windivert_guard.stop()
        self.bot_manager.clear_all_proxies()
        super().closeEvent(event)
